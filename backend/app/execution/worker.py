import argparse
import copy
import logging
import time
from pathlib import Path

from app import db
from app.adapters import video_adapter
from app.config import Settings
from app.errors import DomainError
from app.execution.queue import Queue, digest, render_request, shot_request
from app.media import Cancelled, render
from app.project_engine import ProjectEngine, emit, find
from app.schemas import new_id
from app.storage import asset_path, contained, file_hash, import_asset

logger = logging.getLogger("afds.worker")


class Worker:
    def __init__(self, database):
        self.database = database
        self.settings = database.settings
        self.queue = Queue(database)
        self.engine = ProjectEngine(database)
        self.owner = new_id()

    def run_once(self):
        job = self.queue.claim(self.owner)
        if job is None:
            return False
        started = time.monotonic()
        try:
            with self.database.transaction() as connection:
                current = self.queue.get(connection, job["project_id"], job["id"])
                if (
                    current["lease_owner"] != self.owner
                    or current["lease_expires_at"] is None
                    or current["lease_expires_at"] < time.time()
                    or current["attempt"] != job["attempt"]
                ):
                    return True
                if current["state"] == "cancel_requested":
                    self.queue.set_state(connection, current, "cancelled")
                    return True
                self.queue.set_state(connection, current, "running", progress=10)
            work = contained(self.settings.data_dir, f"cache/{job['id']}/{new_id()}")
            work.mkdir(parents=True, exist_ok=True)
            output = work / "output.mp4"

            def checkpoint():
                return self.queue.heartbeat(job["id"], self.owner)

            if job["operation"] == "generate_video":
                if job["provider_id"] == "mock-video-local":
                    adapter = video_adapter(self.settings, job["provider_id"])
                    result = adapter.generate(job["request"], output, checkpoint)
                else:
                    result = self.run_remote(job, work)
            elif job["operation"] in {"generate_image", "generate_speech"}:
                result = self.run_remote(job, work)
            elif job["operation"] == "import_audio":
                from app.audio_pipeline import process_audio_import

                result = process_audio_import(self.settings, job["request"], work, checkpoint)
            elif job["operation"] == "review_shot":
                from app.remote_media import remote_lease
                from app.review_service import ReviewService

                with remote_lease(self.queue, job, self.owner) as review_checkpoint:
                    result = ReviewService(self.database).execute(job, work, review_checkpoint)
            elif job["operation"] == "export_project":
                from app.exchange import execute_export

                result = execute_export(self.database, job, work, checkpoint)
            else:
                sources = [
                    asset_path(self.database, job["project_id"], item["asset_id"])
                    for item in job["request"]["items"]
                ]
                for source, item in zip(sources, job["request"]["items"], strict=True):
                    if file_hash(source) != item["sha256"]:
                        raise DomainError(
                            "HASH_MISMATCH", "Input media integrity check failed", 422
                        )
                result = {
                    "video": output,
                    "metadata": render(self.settings, job["request"], sources, output, checkpoint),
                }
                if job["request"].get("audio_items") or job["request"].get("captions"):
                    from app.audio_pipeline import finish_audio

                    audio_sources = []
                    for item in job["request"].get("audio_items", []):
                        source = asset_path(self.database, job["project_id"], item["asset_id"])
                        if file_hash(source) != item["sha256"]:
                            raise DomainError(
                                "HASH_MISMATCH", "Audio media integrity check failed", 422
                            )
                        audio_sources.append(source)
                    mixed = work / "mixed.mp4"
                    result["metadata"] = finish_audio(
                        self.settings, job["request"], audio_sources, output, mixed, checkpoint
                    )
                    result["video"] = mixed
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if job["operation"] == "review_shot":
                from app.review_service import ReviewService

                ReviewService(self.database).publish(job, self.owner, result, elapsed_ms)
            elif job["operation"] == "export_project":
                self.publish_export(job, result, elapsed_ms)
            else:
                self.publish(job, result, elapsed_ms)
        except Cancelled:
            self.queue.fail(
                job, self.owner, "LEASE_LOST", "Worker lease lost or cancellation requested"
            )
        except TimeoutError:
            self.queue.fail(
                job, self.owner, "MEDIA_TIMEOUT", "Media operation timed out", transient=True
            )
        except DomainError as error:
            self.queue.fail(
                job,
                self.owner,
                error.code,
                error.message,
                transient=getattr(error, "transient", False),
            )
        except FileNotFoundError:
            self.queue.fail(
                job, self.owner, "MEDIA_DEPENDENCY_MISSING", "Check FFmpeg and input media"
            )
        except OSError:
            self.queue.fail(
                job,
                self.owner,
                "STORAGE_ERROR",
                "Cannot write media. Check disk space and permissions",
            )
        except Exception:
            logger.exception("job_id=%s unexpected worker failure", job["id"])
            self.queue.fail(
                job, self.owner, "INTERNAL_ERROR", "Unexpected worker failure, inspect local logs"
            )
        logger.info(
            "job_id=%s project_id=%s elapsed_ms=%s",
            job["id"],
            job["project_id"],
            int((time.monotonic() - started) * 1000),
        )
        return True

    def run_remote(self, job, work):
        from app.remote_media import execute_remote_media, remote_lease

        with remote_lease(self.queue, job, self.owner) as checkpoint:
            return execute_remote_media(self.queue, job, self.owner, work, checkpoint)

    def publish(self, job, result, elapsed_ms):
        with self.database.transaction() as connection:
            current = self.queue.get(connection, job["project_id"], job["id"])
            if (
                current["lease_owner"] != self.owner
                or current["attempt"] != job["attempt"]
                or current["lease_expires_at"] is None
                or current["lease_expires_at"] < time.time()
            ):
                return
            if current["state"] == "cancel_requested":
                self.queue.set_state(connection, current, "cancelled")
                return
            self.queue.set_state(connection, current, "verifying", progress=90)
            before = self.engine.load(connection, job["project_id"])
            apr = copy.deepcopy(before)
            provenance = {
                "job_id": job["id"],
                "provider_id": job["provider_id"],
                "model_id": job["model_id"],
                "adapter_version": job["adapter_version"],
                "prompt_hash": job["fingerprint"],
                "parameters": job["request"].get("format", apr["format"]),
                "elapsed_ms": elapsed_ms,
                "cost": job["actual_cost"],
                "is_mock": result.get("is_mock", True),
                "protocol": job["request"].get("provider_config", {}).get("protocol", "local"),
                "quality": {"media_probe": "pass", "semantic_review": "not_run"},
            }
            if "normalization" in result:
                provenance["normalization"] = result["normalization"]
            if job["operation"] == "import_audio":
                provenance.update(
                    name=job["request"]["name"],
                    rights_confirmed=True,
                    license="user-provided-unverified",
                    source="user-upload",
                    consent="explicit_upload_rights_confirmation",
                )
            metadata = dict(result["metadata"])
            for key in ("audio", "caption_count"):
                if key in metadata:
                    provenance[key] = metadata.pop(key)
            if job["operation"] == "render_timeline":
                input_ids = {
                    item["asset_id"]
                    for item in job["request"]["items"] + job["request"].get("audio_items", [])
                }
                provenance["is_mock"] = all(
                    asset["provenance"].get("is_mock", False)
                    for asset in apr["assets"]
                    if asset["id"] in input_ids
                )
                provenance["contains_mock"] = any(
                    asset["provenance"].get("is_mock", False)
                    or asset["provenance"].get("contains_mock", False)
                    for asset in apr["assets"]
                    if asset["id"] in input_ids
                )
            asset = import_asset(
                connection,
                self.settings,
                job["project_id"],
                result["video"],
                metadata,
                provenance,
                result.get("media_type", "video"),
            )
            apr["assets"].append(asset)
            if job["request"].get("provider_config", {}).get("protocol") == "comfyui":
                from app.provider_settings import ProviderStore

                ProviderStore(self.database).mark_media_verified(
                    connection, job["request"]["provider_config"], asset["media_type"]
                )
            output_ids = [asset["id"]]
            if job["operation"] == "generate_video":
                poster = import_asset(
                    connection,
                    self.settings,
                    job["project_id"],
                    result["poster"],
                    {"width": asset["width"], "height": asset["height"]},
                    provenance,
                    "image",
                )
                apr["assets"].append(poster)
                output_ids.append(poster["id"])
                shot = find(apr["shots"], job["target_entity_id"])
                fresh = (
                    digest(shot_request(apr, shot, job["request"].get("provider_config")))
                    == job["fingerprint"]
                )
                version = {
                    "id": new_id(),
                    "shot_id": shot["id"],
                    "asset_id": asset["id"],
                    "poster_asset_id": poster["id"],
                    "generated_by_job_id": job["id"],
                    "status": "ready" if fresh else "stale",
                    "created_at": time.time(),
                }
                apr["asset_versions"].append(version)
                if (
                    fresh
                    and not shot["locked"]
                    and shot["status"] not in {"approved", "archived", "needs_review"}
                ):
                    shot["active_asset_version_id"] = version["id"]
                    shot["status"] = "ready"
                    apr["render_status"] = "stale"
            elif job["operation"] in {"generate_image", "generate_speech", "import_audio"}:
                fresh = True
            else:
                try:
                    fresh = digest(render_request(apr)) == job["fingerprint"]
                except DomainError:
                    fresh = False
                if fresh:
                    apr["render_asset_id"] = asset["id"]
                    apr["render_status"] = "ready"
            apr["revision"] += 1
            self.engine.save(connection, apr, before, f"Publish {job['operation']}", "worker")
            target = "completed" if fresh else "needs_review"
            self.queue.set_state(
                connection,
                current,
                target,
                progress=100,
                output_asset_ids=output_ids,
                elapsed_ms=elapsed_ms,
            )
            emit(
                connection, job["project_id"], "asset.created", {"asset_ids": output_ids}, job["id"]
            )
            if job["operation"] == "render_timeline" and fresh:
                emit(
                    connection,
                    job["project_id"],
                    "render.completed",
                    {"asset_id": asset["id"]},
                    job["id"],
                )

    def publish_export(self, job, result, elapsed_ms):
        source = Path(result["path"]).resolve()
        if not source.is_relative_to(self.settings.data_dir.resolve()):
            raise DomainError("INVALID_PATH", "Export artifact is outside storage", 403)
        sha = file_hash(source)
        suffix = ".zip" if result["media_type"] == "application/zip" else ".otio"
        relative = Path("projects") / job["project_id"] / "exports" / f"{job['id']}-{sha}{suffix}"
        destination = contained(self.settings.data_dir, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{self.owner}-{job['attempt']}.partial"
        try:
            with source.open("rb") as reader, temporary.open("wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    if not self.queue.heartbeat(job["id"], self.owner):
                        raise Cancelled()
                    writer.write(chunk)
            if file_hash(temporary) != sha:
                raise DomainError("HASH_MISMATCH", "Export copy changed", 500)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        with self.database.transaction() as connection:
            current = self.queue.get(connection, job["project_id"], job["id"])
            if (
                current["lease_owner"] != self.owner
                or current["attempt"] != job["attempt"]
                or current["lease_expires_at"] is None
                or current["lease_expires_at"] < time.time()
            ):
                return
            if current["state"] == "cancel_requested":
                self.queue.set_state(connection, current, "cancelled")
                return
            self.queue.set_state(connection, current, "verifying", progress=90)
            self.queue.set_state(
                connection,
                current,
                "completed",
                progress=100,
                elapsed_ms=elapsed_ms,
                artifact={
                    "relative_path": relative.as_posix(),
                    "filename": result["filename"],
                    "media_type": result["media_type"],
                    "sha256": sha,
                    "size_bytes": destination.stat().st_size,
                },
            )
            emit(
                connection,
                job["project_id"],
                "export.completed",
                {"job_id": job["id"], "sha256": sha},
                job["id"],
            )


def main():
    parser = argparse.ArgumentParser(description="AFDS independent media worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    database = db.Database(Settings())
    database.migrate()
    worker = Worker(database)
    try:
        while True:
            worked = worker.run_once()
            if args.once:
                break
            if not worked:
                time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Worker stopped. Expired leases will recover on restart.")
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
