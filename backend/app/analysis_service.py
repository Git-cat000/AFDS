"""Durable analysis results and revision-bound human approval of timeline candidates."""

import copy
import json
import wave
from fractions import Fraction
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5

import numpy as np
from pydantic import Field, ValidationError
from sqlalchemy import select, update

from app import db
from app.budget import BudgetService
from app.composition import backbone_duration
from app.errors import DomainError
from app.execution.queue import Queue, digest, public_job, serialize
from app.media import run_process
from app.project_engine import ProjectEngine, emit, find
from app.provider_http import ProviderError
from app.routing_schema import RoutingRequirements
from app.routing_service import RoutingService
from app.storage import asset_path, contained, ensure_space, file_hash
from app.timeline_models import MAX_FRAMES, TimelineModel
from app.timeline_service import validate_timeline

TRANSCRIPTION_WAV_LIMIT = 24 * 1024**2


class AnalysisRequest(TimelineModel):
    base_revision: int = Field(ge=1)
    request_id: UUID
    kind: Literal["beats", "transcription"]
    asset_id: UUID
    source_start_frame: int = Field(default=0, ge=0, le=MAX_FRAMES)
    duration_frames: int = Field(gt=0, le=MAX_FRAMES)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
    provider_id: str | None = Field(default=None, max_length=200)
    allow_remote: bool = False
    route_preview_id: UUID | None = None


class ApplyAnalysis(TimelineModel):
    base_revision: int = Field(ge=1)
    selected_indices: list[int] = Field(min_length=1, max_length=1000)
    target_start_frame: int = Field(default=0, ge=0, le=MAX_FRAMES)
    replace_caption_ids: list[UUID] = Field(default_factory=list, max_length=500)
    approved: bool = False
    preview_token: str | None = Field(default=None, max_length=64)


class AnalysisService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)
        self.queue = Queue(database)

    def enqueue(self, project_id, body: AnalysisRequest):
        submitted = body.model_dump(mode="json")
        key = f"{project_id}:analysis:{body.request_id}"
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            prior = (
                connection.execute(select(db.jobs).where(db.jobs.c.idempotency_key == key))
                .mappings()
                .first()
            )
            if prior:
                job = serialize(prior)
                if job["request"]["input"] != submitted:
                    raise DomainError(
                        "REQUEST_ID_CONFLICT", "Analysis request ID has different input", 409
                    )
                return public_job(job)
            self.engine.check_revision(apr, body.base_revision)
            asset = find(apr["assets"], body.asset_id)
            rate = Fraction(apr["format"]["fps_num"], apr["format"]["fps_den"])
            if (
                asset["media_type"] != "audio"
                or body.duration_frames / rate > 1800
                or (body.source_start_frame + body.duration_frames) / rate
                > asset["duration_seconds"] + 1 / 48000
            ):
                raise DomainError(
                    "INVALID_ANALYSIS_RANGE", "Select up to30minutes within owned audio", 422
                )
            config = None
            if body.kind == "transcription":
                if not body.allow_remote:
                    raise DomainError(
                        "REMOTE_CONSENT_REQUIRED",
                        "Approve sending this audio and provider charges",
                        409,
                    )
                config = RoutingService(self.database).choose(
                    connection,
                    apr,
                    "transcription",
                    provider_id=body.provider_id,
                    allow_remote=body.allow_remote,
                    preview_id=body.route_preview_id,
                    requirements=RoutingRequirements(timestamps=True),
                )
                if not config.get("transcription_timestamps"):
                    raise DomainError(
                        "CAPABILITY_UNSUPPORTED", "Enable segment timestamps for this model", 422
                    )
            payload = {
                "input": submitted,
                "asset_id": str(body.asset_id),
                "sha256": asset["sha256"],
                "format": apr["format"],
                "duration_seconds": float(body.duration_frames / rate),
                "provider_id": config["provider_id"] if config else "local-beat-analysis",
                "model_id": config["model"] if config else "energy-onsets-v1",
                "adapter_version": "0.5.0",
            }
            if config:
                payload["provider_config"] = config
            job = self.queue.enqueue(
                connection,
                apr,
                "transcribe_audio" if config else "analyze_beats",
                payload,
                idempotency_key=key,
            )
            if config:
                self.engine.approve(
                    connection,
                    project_id,
                    "remote_transcription",
                    {
                        "job_id": job["id"],
                        "asset_id": str(body.asset_id),
                        "sha256": asset["sha256"],
                        "source_start_frame": body.source_start_frame,
                        "duration_frames": body.duration_frames,
                        "provider_revision": config["revision"],
                    },
                )
            return public_job(job)

    def execute(self, job, owner, work, checkpoint):
        request = job["request"]
        body = AnalysisRequest.model_validate(request["input"])
        source = asset_path(self.database, job["project_id"], request["asset_id"])
        if file_hash(source) != request["sha256"]:
            raise DomainError("HASH_MISMATCH", "Analysis source changed", 422)
        rate = Fraction(request["format"]["fps_num"], request["format"]["fps_den"])
        audio = work / "analysis.wav"
        ensure_space(
            self.database.settings.data_dir, round(request["duration_seconds"] * 32000) + 4096
        )
        run_process(
            [
                self.database.settings.binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-ss",
                f"{float(body.source_start_frame / rate):.9f}",
                "-t",
                f"{request['duration_seconds']:.9f}",
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            self.database.settings.media_timeout,
            checkpoint,
        )
        if body.kind == "beats":
            from app.audio_analysis import detect_beats

            with wave.open(str(audio), "rb") as handle:
                if handle.getnframes() > 16000 * 1800:
                    raise DomainError("INVALID_ANALYSIS_RANGE", "Audio exceeds30minutes", 422)
                samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
            result = detect_beats(samples, 16000, rate.numerator, rate.denominator).model_dump(
                mode="json"
            )
        else:
            result = self.transcribe(job, owner, audio, work, checkpoint)
        result.update(
            kind=body.kind,
            asset_id=request["asset_id"],
            source_sha256=request["sha256"],
            source_start_frame=body.source_start_frame,
            duration_frames=body.duration_frames,
        )
        return result

    def transcribe(self, job, owner, source, work, checkpoint):
        from app.transcription_adapter import TranscriptionAdapter

        snapshot = job["request"]["provider_config"]
        config = RoutingService(self.database).runtime(snapshot, job["project_id"])
        if source.stat().st_size > TRANSCRIPTION_WAV_LIMIT:
            compressed = work / "transcription.mp3"
            run_process(
                [
                    self.database.settings.binary("ffmpeg"),
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    str(compressed),
                ],
                self.database.settings.media_timeout,
                checkpoint,
            )
            source = compressed
        if source.stat().st_size > 25 * 1024**2:
            raise DomainError("INVALID_ANALYSIS_RANGE", "Select a smaller transcription range", 422)
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            if current.get("submission_started"):
                raise DomainError(
                    "REMOTE_SUBMISSION_UNCERTAIN",
                    "Check provider usage and approve a new request",
                    409,
                )
            BudgetService(self.database).enforce(
                self.engine.load(connection, job["project_id"]), config
            )
            BudgetService(self.database).begin_submission(
                connection, current["budget_entry_id"], config["timeout_seconds"]
            )
            payload = connection.execute(
                select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
            ).scalar_one()
            payload["submission_started"] = True
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
            )
            job["submission_started"] = True
        try:
            request = {**job["request"], "language": job["request"]["input"].get("language")}
            return (
                TranscriptionAdapter(config, source)
                .transcribe(request, work, checkpoint)
                .model_dump(mode="json")
            )
        except ProviderError as error:
            if error.rejected:
                self.queue.update_remote(job, owner, submission_started=False)
            elif error.code in {
                "PROVIDER_TIMEOUT",
                "PROVIDER_CONNECTION_FAILED",
                "PROVIDER_HTTP_ERROR",
            }:
                raise ProviderError(
                    "REMOTE_SUBMISSION_UNCERTAIN",
                    "Transcription may have been accepted. "
                    "Check provider usage before a new request",
                ) from error
            raise

    def publish(self, job, owner, result, elapsed_ms):
        data = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > 1024**2:
            raise DomainError("ANALYSIS_RESULT_TOO_LARGE", "Select a shorter audio range", 422)
        sha = digest(result)
        relative = Path("projects") / job["project_id"] / "analysis" / f"{job['id']}-{sha}.json"
        destination = contained(self.database.settings.data_dir, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            self.engine.load(connection, job["project_id"])
            if not destination.exists():
                temporary = destination.parent / f".{owner}-{job['attempt']}.partial"
                try:
                    temporary.write_bytes(data)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            if destination.read_bytes() != data:
                raise DomainError("HASH_MISMATCH", "Immutable analysis result changed", 422)
            self.queue.set_state(connection, current, "verifying", progress=90)
            self.queue.set_state(
                connection,
                current,
                "completed",
                progress=100,
                elapsed_ms=elapsed_ms,
                analysis_result={
                    "relative_path": relative.as_posix(),
                    "sha256": file_hash(destination),
                    "result_hash": sha,
                    "count": len(result.get("frames", result.get("segments", []))),
                },
            )
            emit(
                connection,
                job["project_id"],
                "analysis.completed",
                {"job_id": job["id"]},
                job["id"],
            )

    def result(self, connection, project_id, job_id):
        job = self.queue.get(connection, project_id, job_id)
        if (
            job["operation"] not in {"analyze_beats", "transcribe_audio"}
            or job["state"] != "completed"
        ):
            raise DomainError("ANALYSIS_NOT_READY", "Analysis result is not ready", 409)
        artifact = job.get("analysis_result", {})
        path = contained(self.database.settings.data_dir, artifact.get("relative_path", ""))
        expected = contained(
            self.database.settings.data_dir, Path("projects") / str(project_id) / "analysis"
        )
        if (
            not path.is_relative_to(expected)
            or not path.is_file()
            or path.stat().st_size > 1024**2
            or file_hash(path) != artifact.get("sha256")
        ):
            raise DomainError("HASH_MISMATCH", "Analysis result is missing or changed", 422)
        result = json.loads(path.read_bytes())
        if digest(result) != artifact["result_hash"]:
            raise DomainError("HASH_MISMATCH", "Analysis result hash changed", 422)
        return job, result

    def read(self, project_id, job_id):
        with self.database.engine.connect() as connection:
            self.engine.load(connection, project_id)
            job, result = self.result(connection, project_id, job_id)
            return {
                "job": public_job(job),
                "result": result,
                "sha256": job["analysis_result"]["sha256"],
            }

    def list(self, project_id, limit=20, offset=0):
        with self.database.engine.connect() as connection:
            self.engine.load(connection, project_id)
            rows = connection.execute(
                select(db.jobs)
                .where(
                    db.jobs.c.project_id == str(project_id),
                    db.jobs.c.payload["operation"]
                    .as_string()
                    .in_(["analyze_beats", "transcribe_audio"]),
                )
                .order_by(db.jobs.c.payload["created_at"].as_float().desc(), db.jobs.c.id.desc())
                .limit(limit)
                .offset(offset)
            ).mappings()
            return [public_job(serialize(row)) for row in rows]

    def proposal(self, apr, connection, job_id, body):
        self.engine.check_revision(apr, body.base_revision)
        job, result = self.result(connection, apr["project_id"], job_id)
        source_format = job["request"]["format"]
        if Fraction(source_format["fps_num"], source_format["fps_den"]) != Fraction(
            apr["format"]["fps_num"], apr["format"]["fps_den"]
        ):
            raise DomainError(
                "ANALYSIS_STALE", "Project frame rate changed. Analyze the audio again", 409
            )
        source = find(apr["assets"], result["asset_id"])
        if source["sha256"] != result["source_sha256"]:
            raise DomainError("HASH_MISMATCH", "Analysis source changed", 422)
        candidates = result.get("frames", result.get("segments", []))
        indices = body.selected_indices
        if len(set(indices)) != len(indices) or any(i < 0 or i >= len(candidates) for i in indices):
            raise DomainError("INVALID_ANALYSIS_SELECTION", "Choose distinct valid candidates", 422)
        remove_ids = {str(value) for value in body.replace_caption_ids}
        captions = apr["timeline"].get("captions", [])
        if result["kind"] == "beats" and remove_ids:
            raise DomainError(
                "INVALID_ANALYSIS_SELECTION", "Beat candidates cannot replace captions", 422
            )
        if not remove_ids <= {caption["id"] for caption in captions}:
            raise DomainError("NOT_FOUND", "Caption does not belong to this project", 404)
        added_captions, added_markers = [], []
        rate = Fraction(apr["format"]["fps_num"], apr["format"]["fps_den"])
        total = backbone_duration(apr["timeline"])
        for index in sorted(indices):
            identity = str(uuid5(UUID(job_id), f"{index}:{body.target_start_frame}"))
            if result["kind"] == "beats":
                frame = body.target_start_frame + candidates[index]
                if frame >= total:
                    raise DomainError(
                        "INVALID_ANALYSIS_RANGE", "Candidate exceeds the film end", 422
                    )
                added_markers.append(
                    {"id": identity, "frame": frame, "kind": "beat", "label": f"Beat {index + 1}"}
                )
            else:
                segment = candidates[index]
                start = body.target_start_frame + round(Fraction(segment["start_ms"], 1000) * rate)
                end = body.target_start_frame + round(Fraction(segment["end_ms"], 1000) * rate)
                if end > total:
                    raise DomainError("INVALID_ANALYSIS_RANGE", "Caption exceeds the film end", 422)
                added_captions.append(
                    {
                        "id": identity,
                        "start_frame": start,
                        "duration_frames": max(1, end - start),
                        "text": segment["text"],
                    }
                )
        after = copy.deepcopy(apr)
        retained = [caption for caption in captions if caption["id"] not in remove_ids]
        existing = {item["id"] for item in retained}
        after["timeline"]["captions"] = retained + [
            item for item in added_captions if item["id"] not in existing
        ]
        markers = after["timeline"].get("markers", [])
        existing = {item["id"] for item in markers}
        after["timeline"]["markers"] = markers + [
            item for item in added_markers if item["id"] not in existing
        ]
        try:
            validate_timeline(after)
        except (ValueError, ValidationError) as error:
            raise DomainError(
                "INVALID_TIMELINE",
                "Candidate selection exceeds timeline limits. Select fewer candidates",
                422,
            ) from error
        preview = {
            "base_revision": apr["revision"],
            "result_sha256": job["analysis_result"]["sha256"],
            "add_captions": added_captions,
            "add_markers": added_markers,
            "remove_captions": [caption for caption in captions if caption["id"] in remove_ids],
        }
        preview["preview_token"] = digest(
            {
                "request": body.model_dump(mode="json", exclude={"approved", "preview_token"}),
                "job_id": job_id,
                "preview": preview,
            }
        )
        return after, preview

    def preview_apply(self, project_id, job_id, body):
        with self.database.transaction() as connection:
            return self.proposal(
                self.engine.load(connection, project_id), connection, str(job_id), body
            )[1]

    def commit_apply(self, project_id, job_id, body):
        if not body.approved or not body.preview_token:
            raise DomainError("APPROVAL_REQUIRED", "Preview and approve selected candidates", 409)

        def apply(apr, connection):
            after, preview = self.proposal(apr, connection, str(job_id), body)
            if preview["preview_token"] != body.preview_token:
                raise DomainError(
                    "PREVIEW_CHANGED", "Candidate selection changed. Preview again", 409
                )
            apr["timeline"] = after["timeline"]
            apr["timeline"]["version"] += 1
            if preview["add_captions"] or preview["remove_captions"]:
                apr["render_status"] = "stale"
            self.engine.approve(connection, project_id, "apply_audio_analysis", preview)

        return self.engine.mutate(
            project_id, body.base_revision, "Approve audio analysis candidates", apply
        )
