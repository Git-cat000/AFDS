import copy
import hashlib
import json
import random
import time

from sqlalchemy import insert, select, update

from app import db
from app.adapters import check_provider
from app.errors import DomainError
from app.project_engine import ProjectEngine, emit, find
from app.schemas import Format, new_id
from app.storage import contained, file_hash

ACTIVE = {"claimed", "running", "verifying", "cancel_requested"}
TERMINAL = {"completed", "failed", "cancelled", "needs_review"}
TRANSITIONS = {
    "queued": {"claimed", "cancelled"},
    "claimed": {"running", "cancel_requested", "queued", "failed"},
    "running": {"verifying", "cancel_requested", "failed", "retry_wait", "queued"},
    "verifying": {"completed", "needs_review", "failed", "cancel_requested", "queued"},
    "cancel_requested": {"cancelled"},
    "retry_wait": {"queued", "cancelled"},
    "failed": {"queued"},
    "cancelled": {"queued"},
    "completed": set(),
    "needs_review": {"completed"},
}


def transition(previous, target):
    if target not in TRANSITIONS.get(previous, set()):
        raise DomainError("INVALID_JOB_TRANSITION", f"Cannot move {previous} to {target}", 409)
    return target


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def shot_request(apr, shot, provider_config=None):
    request = {
        "shot_id": shot["id"],
        "order": shot["order"],
        "action": shot["action"],
        "emotion": shot["emotion"],
        "camera": shot["camera"],
        "duration_frames": shot["duration_frames"],
        "format": apr["format"],
        "style_bible": apr["style_bible"],
        "provider_id": "mock-video-local",
        "model_id": "ffmpeg-test-pattern",
        "adapter_version": "0.1.0",
    }
    if provider_config:
        request.update(
            provider_id=provider_config["provider_id"],
            model_id=provider_config["model"],
            adapter_version="0.2.0",
            provider_config=provider_config,
        )
    return request


def render_request(apr):
    items = []
    for placement in apr["timeline"]["tracks"][0]["items"]:
        shot = find(apr["shots"], placement["shot_id"])
        if not shot["active_asset_version_id"]:
            raise DomainError("MISSING_ASSET", "Generate every timeline shot first", 409)
        version = find(apr["asset_versions"], shot["active_asset_version_id"])
        if version["status"] == "stale" or shot["status"] in {"stale", "needs_review"}:
            raise DomainError("STALE_ASSET", "Regenerate or review stale timeline shots", 409)
        asset = find(apr["assets"], version["asset_id"])
        items.append({**placement, "asset_id": asset["id"], "sha256": asset["sha256"]})
    if not items:
        raise DomainError("EMPTY_TIMELINE", "Add shots to the timeline first", 422)
    return {
        "items": items,
        "format": apr["format"],
        "audio_policy": "silent",
        "adapter_version": "0.1.0",
    }


def serialize(row):
    return {
        **copy.deepcopy(row["payload"]),
        "id": row["id"],
        "project_id": row["project_id"],
        "state": row["state"],
        "attempt": row["attempt"],
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "available_at": row["available_at"],
    }


class Queue:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)

    def get(self, connection, project_id, job_id):
        row = (
            connection.execute(
                select(db.jobs).where(
                    db.jobs.c.id == str(job_id),
                    db.jobs.c.project_id == str(project_id),
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DomainError("NOT_FOUND", "Job not found in this project", 404)
        return serialize(row)

    def enqueue(self, connection, apr, operation, request, target=None):
        fingerprint = digest(request)
        key = f"{apr['project_id']}:{operation}:{fingerprint}"
        existing = (
            connection.execute(select(db.jobs).where(db.jobs.c.idempotency_key == key))
            .mappings()
            .first()
        )
        if existing:
            result = serialize(existing)
            if result["state"] in {"completed", "needs_review"}:
                self.reactivate(connection, apr, result)
                if result["state"] == "needs_review":
                    self.set_state(connection, result, "completed")
                result["reused_from_job_id"] = result["id"]
            return result
        payload = {
            "operation": operation,
            "target_entity_id": target,
            "request": request,
            "fingerprint": fingerprint,
            "dependencies": [],
            "progress": 0,
            "provider_id": request.get("provider_id", "ffmpeg"),
            "model_id": request.get("model_id", "ffmpeg"),
            "adapter_version": request.get("adapter_version", "0.1.0"),
            "estimated_cost": {"status": "known", "amount": 0, "currency": "USD"},
            "actual_cost": {"status": "known", "amount": 0, "currency": "USD"},
            "max_attempts": self.database.settings.max_job_attempts,
            "output_asset_ids": [],
            "error": None,
            "created_at": time.time(),
            "input_summary": {"request_hash": fingerprint},
            "elapsed_ms": None,
            "external_task_id": None,
            "submission_started": False,
        }
        if payload["provider_id"].startswith("openai-"):
            unknown = {"status": "unknown", "amount": None, "currency": None}
            payload.update(
                estimated_cost=unknown, actual_cost=unknown, remote_cancel_supported=False
            )
        jid = new_id()
        connection.execute(
            insert(db.jobs).values(
                id=jid,
                project_id=apr["project_id"],
                idempotency_key=key,
                state="queued",
                attempt=0,
                available_at=0,
                payload=payload,
            )
        )
        emit(connection, apr["project_id"], "job.queued", {"operation": operation}, jid)
        return self.get(connection, apr["project_id"], jid)

    def reactivate(self, connection, apr, job):
        for asset_id in job["output_asset_ids"]:
            row = (
                connection.execute(
                    select(db.assets).where(
                        db.assets.c.id == asset_id,
                        db.assets.c.project_id == apr["project_id"],
                    )
                )
                .mappings()
                .first()
            )
            path = contained(self.database.settings.data_dir, row["path"]) if row else None
            if path is None or not path.is_file() or file_hash(path) != row["payload"]["sha256"]:
                raise DomainError(
                    "CACHE_MEDIA_INVALID",
                    "Cached media is missing or corrupt. "
                    "Restore the asset or change the shot to regenerate.",
                    409,
                )
        if job["operation"] in {"generate_image", "generate_speech"}:
            return
        if job["operation"] == "render_timeline":
            apr["render_asset_id"] = job["output_asset_ids"][0]
            apr["render_status"] = "ready"
            return
        version = next(
            (v for v in apr["asset_versions"] if v["generated_by_job_id"] == job["id"]), None
        )
        if version is None:
            raise DomainError("CACHE_MEDIA_INVALID", "Cached asset version is missing", 409)
        version["status"] = "ready"
        shot = find(apr["shots"], job["target_entity_id"])
        if not shot["locked"] and shot["status"] not in {"approved", "needs_review", "archived"}:
            if shot["active_asset_version_id"] != version["id"] or shot["status"] != "ready":
                shot["active_asset_version_id"] = version["id"]
                shot["status"] = "ready"
                apr["render_status"] = "stale"

    def generate(self, project_id, body):
        from app.provider_settings import ProviderStore

        store = ProviderStore(self.database)
        provider_id = body.provider_id or store.list()["defaults"]["video"]
        config = None
        if provider_id == "mock-video-local":
            check_provider(self.database.settings, provider_id, body.capability)
        elif provider_id == "openai-video":
            if not body.allow_remote:
                raise DomainError(
                    "REMOTE_CONSENT_REQUIRED", "Approve sharing shot content and unknown costs", 409
                )
            config = store.snapshot(provider_id, body.capability)
        else:
            raise DomainError("MODEL_UNAVAILABLE", "Unknown video provider", 422)
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            before = copy.deepcopy(apr)
            result = []
            for sid in dict.fromkeys(body.shot_ids):
                shot = find(apr["shots"], sid)
                request = shot_request(apr, shot, config)
                seconds = Format(**apr["format"]).seconds(shot["duration_frames"])
                if not 1 <= seconds <= 30:
                    raise DomainError(
                        "PROVIDER_CONSTRAINT", "Mock video supports 1–30 seconds", 422
                    )
                if shot["status"] == "archived":
                    raise DomainError("ARCHIVED_SHOT", "Restore the shot before generation", 409)
                if shot["locked"]:
                    raise DomainError("SHOT_LOCKED", "Unlock this shot before generation", 409)
                # Approved shots may produce candidates, but worker never replaces their selection.
                result.append(self.enqueue(connection, apr, "generate_video", request, shot["id"]))
            if config:
                self.engine.approve(
                    connection,
                    project_id,
                    "remote_video",
                    {
                        "provider_id": provider_id,
                        "provider_revision": config["revision"],
                        "job_ids": [job["id"] for job in result],
                        "cost_status": "unknown",
                    },
                )
            if before != apr:
                apr["revision"] += 1
                self.engine.save(connection, apr, before, "Reactivate cached shot outputs", "user")
            return result

    def generate_media(self, project_id, body):
        from app.provider_settings import CAPABILITIES, ProviderStore

        kind = "image" if body.operation == "generate_image" else "speech"
        if not body.allow_remote:
            raise DomainError(
                "REMOTE_CONSENT_REQUIRED", "Approve content sharing and unknown costs", 409
            )
        store = ProviderStore(self.database)
        provider_id = body.provider_id or store.list()["defaults"][kind]
        config = store.snapshot(provider_id, CAPABILITIES[kind])
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            request = {
                "prompt": body.prompt,
                "format": apr["format"],
                "provider_config": config,
                "provider_id": provider_id,
                "model_id": config["model"],
                "adapter_version": "0.2.0",
                "request_id": str(body.request_id) if body.request_id else None,
            }
            job = self.enqueue(connection, apr, body.operation, request)
            self.engine.approve(
                connection,
                project_id,
                "remote_media",
                {"provider_id": provider_id, "job_id": job["id"], "cost_status": "unknown"},
            )
            return job

    def update_remote(self, job, owner, **fields):
        from app.media import Cancelled

        with self.database.transaction() as connection:
            current = self.get(connection, job["project_id"], job["id"])
            if (
                current["lease_owner"] != owner
                or current["attempt"] != job["attempt"]
                or current["lease_expires_at"] is None
                or current["lease_expires_at"] < time.time()
                or current["state"] not in {"running", "cancel_requested"}
            ):
                raise Cancelled()
            payload = connection.execute(
                select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
            ).scalar_one()
            payload.update(fields)
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
            )
            job.update(fields)
            emit(connection, job["project_id"], "job.remote_updated", fields, job["id"])

    def attach_remote(self, project_id, job_id, body):
        if not body.approved:
            raise DomainError(
                "APPROVAL_REQUIRED", "Confirm the remote task belongs to this job", 409
            )
        with self.database.transaction() as connection:
            job = self.get(connection, project_id, job_id)
            if job["operation"] != "generate_video" or job["provider_id"] != "openai-video":
                raise DomainError(
                    "CAPABILITY_UNSUPPORTED", "Remote recovery supports video jobs", 422
                )
            if job["state"] not in {"failed", "cancelled"}:
                raise DomainError(
                    "INVALID_JOB_TRANSITION", "Stop the local job before recovery", 409
                )
            self.set_state(
                connection,
                job,
                "queued",
                external_task_id=body.external_task_id,
                submission_started=True,
                error=None,
            )
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job["id"]).values(attempt=0, available_at=0)
            )
            self.engine.approve(
                connection,
                project_id,
                "remote_recovery",
                {"job_id": job["id"], "external_task_id": body.external_task_id},
            )
            return self.get(connection, project_id, job_id)

    def render(self, project_id, body):
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            before = copy.deepcopy(apr)
            result = self.enqueue(connection, apr, "render_timeline", render_request(apr))
            if before != apr:
                apr["revision"] += 1
                self.engine.save(connection, apr, before, "Reactivate cached render", "user")
            return result

    def set_state(self, connection, job, target, **payload_updates):
        transition(job["state"], target)
        row = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
        ).scalar_one()
        payload = {**row, **payload_updates}
        values = {"state": target, "payload": payload}
        if target in TERMINAL or target in {"queued", "retry_wait"}:
            values.update(lease_owner=None, lease_expires_at=None)
        connection.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(**values))
        emit(connection, job["project_id"], f"job.{target}", payload_updates, job["id"])
        job.update(state=target, **payload_updates)

    def claim(self, owner):
        now = time.time()
        with self.database.transaction() as connection:
            expired = (
                connection.execute(
                    select(db.jobs).where(
                        db.jobs.c.state.in_(ACTIVE),
                        db.jobs.c.lease_expires_at < now,
                    )
                )
                .mappings()
                .all()
            )
            for row in expired:
                job = serialize(row)
                if job["state"] == "cancel_requested":
                    self.set_state(connection, job, "cancelled")
                elif job["attempt"] >= job["max_attempts"]:
                    self.set_state(
                        connection,
                        job,
                        "failed",
                        error={
                            "code": "LEASE_EXHAUSTED",
                            "message": "Worker recovery attempts exhausted",
                        },
                    )
                else:
                    self.set_state(connection, job, "queued")
            # SQLite mode intentionally runs at most one active media task.
            if connection.execute(select(db.jobs.c.id).where(db.jobs.c.state.in_(ACTIVE))).first():
                return None
            waiting = (
                connection.execute(
                    select(db.jobs).where(
                        db.jobs.c.state == "retry_wait",
                        db.jobs.c.available_at <= now,
                    )
                )
                .mappings()
                .all()
            )
            for row in waiting:
                self.set_state(connection, serialize(row), "queued")
            row = (
                connection.execute(
                    select(db.jobs)
                    .where(
                        db.jobs.c.state == "queued",
                        db.jobs.c.available_at <= now,
                    )
                    .order_by(
                        db.jobs.c.available_at,
                        db.jobs.c.rowid if "rowid" in db.jobs.c else db.jobs.c.id,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            job = serialize(row)
            self.set_state(connection, job, "claimed")
            connection.execute(
                update(db.jobs)
                .where(db.jobs.c.id == job["id"])
                .values(
                    lease_owner=owner,
                    lease_expires_at=now + self.database.settings.lease_seconds,
                    attempt=job["attempt"] + 1,
                )
            )
            return self.get(connection, job["project_id"], job["id"])

    def heartbeat(self, job_id, owner):
        with self.database.transaction() as connection:
            result = connection.execute(
                update(db.jobs)
                .where(
                    db.jobs.c.id == job_id,
                    db.jobs.c.lease_owner == owner,
                    db.jobs.c.state.in_({"claimed", "running", "verifying"}),
                    db.jobs.c.lease_expires_at >= time.time(),
                )
                .values(lease_expires_at=time.time() + self.database.settings.lease_seconds)
            )
            return result.rowcount == 1

    def cancel(self, project_id, job_id):
        with self.database.transaction() as connection:
            job = self.get(connection, project_id, job_id)
            if job["state"] in {"queued", "retry_wait"}:
                self.set_state(connection, job, "cancelled")
            elif job["state"] in {"claimed", "running", "verifying"}:
                details = {}
                if job["provider_id"].startswith("openai-"):
                    details["remote_notice"] = (
                        "本地任务已请求停止。远端任务可能继续运行并产生费用，请在服务商处查看。"
                    )
                self.set_state(connection, job, "cancel_requested", **details)
            return self.get(connection, project_id, job_id)

    def retry(self, project_id, job_id, body=None):
        with self.database.transaction() as connection:
            job = self.get(connection, project_id, job_id)
            if job["state"] in {"queued", "claimed", "running", "retry_wait"}:
                return job
            if job["state"] not in {"failed", "cancelled"}:
                raise DomainError(
                    "INVALID_JOB_TRANSITION", "Only failed/cancelled jobs can retry", 409
                )
            remote = job["provider_id"].startswith("openai-")
            restarting = bool(body and body.restart_remote)
            terminal_remote = (job.get("error") or {}).get("code") == "PROVIDER_JOB_FAILED"
            if remote and terminal_remote and not restarting:
                raise DomainError(
                    "REMOTE_CONSENT_REQUIRED", "Explicitly authorize a new remote generation", 409
                )
            if restarting and (not remote or not body.allow_remote):
                raise DomainError(
                    "REMOTE_CONSENT_REQUIRED", "A new remote request can incur another charge", 409
                )
            fields = {}
            if restarting:
                attempts = list(job.get("remote_attempts", []))
                attempts.append(
                    {
                        "external_task_id": job.get("external_task_id"),
                        "error": job.get("error"),
                        "timestamp": time.time(),
                    }
                )
                fields.update(
                    external_task_id=None,
                    submission_started=False,
                    remote_notice=None,
                    remote_attempts=attempts,
                )
                self.engine.approve(
                    connection,
                    project_id,
                    "remote_resubmit",
                    {"job_id": job["id"], "cost_status": "unknown"},
                )
            self.set_state(connection, job, "queued", error=None, progress=0, **fields)
            connection.execute(
                update(db.jobs)
                .where(db.jobs.c.id == str(job_id))
                .values(
                    attempt=0,
                    available_at=0,
                )
            )
            return self.get(connection, project_id, job_id)

    def fail(self, job, owner, code, message, transient=False):
        with self.database.transaction() as connection:
            current = self.get(connection, job["project_id"], job["id"])
            if current["lease_owner"] != owner:
                return
            if current["state"] == "cancel_requested":
                self.set_state(connection, current, "cancelled")
                return
            retry = transient and current["attempt"] < current["max_attempts"]
            target = "retry_wait" if retry else "failed"
            self.set_state(connection, current, target, error={"code": code, "message": message})
            if retry:
                connection.execute(
                    update(db.jobs)
                    .where(db.jobs.c.id == job["id"])
                    .values(
                        available_at=time.time() + 2 ** current["attempt"] + random.random(),
                    )
                )
