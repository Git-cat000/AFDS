"""Sanitized diagnostics and previewed, fenced cleanup of old unregistered outputs."""

import copy
import hmac
import os
import re
import stat
import time
from uuid import UUID

from pydantic import Field
from sqlalchemy import insert, select, update

from app import APP_VERSION, APR_SCHEMA_VERSION, db
from app.errors import DomainError
from app.execution.queue import Queue, digest, public_job
from app.history_service import ACTIVE_STATES, owned_header
from app.media import Cancelled
from app.operations_service import OperationsService
from app.project_engine import ProjectEngine
from app.schemas import RevisionRequest, new_id
from app.storage import contained

MIN_AGE_SECONDS = 24 * 3600
MAX_SCAN = 5000
MAX_CANDIDATES = 100
MEDIA_NAME = re.compile(
    r"^[a-f0-9]{64}\.(?:mp4|mov|mkv|webm|png|jpg|jpeg|webp|wav|mp3|m4a|aac|flac|ogg)$"
)


def clock():
    return time.time()


class CleanupRequest(RevisionRequest):
    plan_id: UUID
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")


def identity(info):
    return {
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
    }


class MaintenanceService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)
        self.queue = Queue(database)

    def report(self, project_id=None):
        operations = OperationsService(self.database)
        result = {
            "version": APP_VERSION,
            "apr_schema_version": APR_SCHEMA_VERSION,
            "runtime": operations.status(),
            "created_at": clock(),
        }
        if project_id:
            result["project"] = operations.project(project_id)
        return result

    def root(self, project_id):
        relative = f"projects/{project_id}/assets"
        root = contained(self.database.settings.data_dir, relative)
        expected = self.database.settings.data_dir / relative
        if root != expected:
            raise DomainError("INVALID_PATH", "Asset directory contains a symbolic link", 403)
        return root

    def no_active_jobs(self, connection, project_id, exclude=None):
        query = select(db.jobs.c.id).where(
            db.jobs.c.project_id == str(project_id), db.jobs.c.state.in_(ACTIVE_STATES)
        )
        if exclude:
            query = query.where(db.jobs.c.id != exclude)
        if connection.execute(query.limit(1)).first():
            raise DomainError(
                "JOBS_ACTIVE", "Wait for project tasks to finish before storage maintenance", 409
            )

    def registered(self, connection, project_id, relative):
        return (
            connection.execute(
                select(db.assets.c.id)
                .where(db.assets.c.project_id == str(project_id), db.assets.c.path == relative)
                .limit(1)
            ).first()
            is not None
        )

    def scan(self, connection, project_id):
        root = self.root(project_id)
        candidates, scanned, truncated = [], 0, False
        started = time.monotonic()
        if not root.is_dir():
            return candidates, scanned, truncated
        with os.scandir(root) as folders:
            for folder in folders:
                scanned += 1
                if scanned >= MAX_SCAN or time.monotonic() - started >= 2:
                    return candidates, scanned, True
                if (
                    not re.fullmatch(r"[a-f0-9]{2}", folder.name)
                    or folder.is_symlink()
                    or not folder.is_dir(follow_symlinks=False)
                ):
                    continue
                with os.scandir(folder.path) as entries:
                    for entry in entries:
                        scanned += 1
                        if (
                            scanned >= MAX_SCAN
                            or time.monotonic() - started >= 2
                            or len(candidates) >= MAX_CANDIDATES
                        ):
                            return candidates, scanned, True
                        if (
                            not MEDIA_NAME.fullmatch(entry.name)
                            or not entry.name.startswith(folder.name)
                            or entry.is_symlink()
                        ):
                            continue
                        # Windows DirEntry.stat reports zero device/inode fields.
                        # Use the same lstat identity at preview and deletion time.
                        info = (root / folder.name / entry.name).lstat()
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or clock() - max(info.st_mtime, info.st_ctime) < MIN_AGE_SECONDS
                        ):
                            continue
                        name = f"{folder.name}/{entry.name}"
                        relative = f"projects/{project_id}/assets/{name}"
                        if not self.registered(connection, project_id, relative):
                            candidates.append({"name": name, **identity(info)})
        return candidates, scanned, truncated

    def preview(self, project_id, body):
        # Reads are bounded; persist only the exact preview after checking revision again.
        with self.database.engine.connect() as connection:
            header = owned_header(connection, project_id)
            self.engine.check_revision(header, body.base_revision)
            self.no_active_jobs(connection, project_id)
            files, scanned, truncated = self.scan(connection, project_id)
        plan = {
            "id": new_id(),
            "type": "storage_cleanup",
            "state": "proposed",
            "project_id": str(project_id),
            "base_revision": body.base_revision,
            "created_at": clock(),
            "expires_at": clock() + 900,
            "files": files,
            "scanned": scanned,
            "truncated": truncated,
            "bytes": sum(item["size"] for item in files),
            "min_age_seconds": MIN_AGE_SECONDS,
        }
        plan["preview_token"] = digest(plan)
        with self.database.transaction() as connection:
            self.engine.check_revision(owned_header(connection, project_id), body.base_revision)
            self.no_active_jobs(connection, project_id)
            connection.execute(
                insert(db.plans).values(id=plan["id"], project_id=str(project_id), payload=plan)
            )
        return plan

    def enqueue(self, project_id, body):
        if not body.approved:
            raise DomainError(
                "APPROVAL_REQUIRED",
                "Approve deletion of the exact previewed old unregistered files",
                409,
            )
        with self.database.transaction() as connection:
            apr = owned_header(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            plan = connection.execute(
                select(db.plans.c.payload).where(
                    db.plans.c.id == str(body.plan_id), db.plans.c.project_id == str(project_id)
                )
            ).scalar_one_or_none()
            if not plan or plan.get("type") != "storage_cleanup":
                raise DomainError("NOT_FOUND", "Storage preview not found", 404)
            if (
                not hmac.compare_digest(plan["preview_token"], body.preview_token)
                or plan["base_revision"] != body.base_revision
                or plan["expires_at"] < clock()
            ):
                raise DomainError(
                    "PREVIEW_EXPIRED", "Refresh the storage preview before cleanup", 409
                )
            if plan.get("job_id"):
                return public_job(self.queue.get(connection, project_id, plan["job_id"]))
            self.no_active_jobs(connection, project_id)
            job = self.queue.enqueue(
                connection,
                apr,
                "cleanup_storage",
                {
                    "plan_id": plan["id"],
                    "base_revision": body.base_revision,
                    "files": plan["files"],
                    "provider_id": "local-maintenance",
                    "model_id": "old-unregistered-files",
                    "adapter_version": "0.6.0",
                },
            )
            plan.update(state="queued", job_id=job["id"])
            connection.execute(
                update(db.plans).where(db.plans.c.id == plan["id"]).values(payload=plan)
            )
            self.engine.approve(
                connection,
                project_id,
                "storage_cleanup",
                {
                    "plan_id": plan["id"],
                    "file_count": len(plan["files"]),
                    "preview_token": body.preview_token,
                },
            )
            return public_job(job)

    def execute(self, job, owner, checkpoint):
        root = self.root(job["project_id"])
        receipt = copy.deepcopy(
            job.get("cleanup_result")
            or {
                "deleted_count": 0,
                "deleted_bytes": 0,
                "skipped_count": 0,
                "already_absent": 0,
                "processed": [],
            }
        )
        for item in job["request"]["files"]:
            if item["name"] in receipt["processed"]:
                continue
            if not checkpoint():
                raise Cancelled()
            with self.database.transaction() as connection:
                self.queue.check_owned(connection, job, owner)
                self.engine.check_revision(
                    owned_header(connection, job["project_id"]), job["request"]["base_revision"]
                )
                self.no_active_jobs(connection, job["project_id"], exclude=job["id"])
                parts = item["name"].split("/")
                if (
                    len(parts) != 2
                    or not re.fullmatch(r"[a-f0-9]{2}", parts[0])
                    or not MEDIA_NAME.fullmatch(parts[1])
                    or not parts[1].startswith(parts[0])
                ):
                    raise DomainError(
                        "INVALID_PATH", "Storage preview contains an invalid media name", 403
                    )
                lexical = root / item["name"]
                path = contained(root, item["name"])
                relative = f"projects/{job['project_id']}/assets/{item['name']}"
                if (
                    path != lexical
                    or lexical.is_symlink()
                    or self.registered(connection, job["project_id"], relative)
                ):
                    receipt["skipped_count"] += 1
                elif not path.exists():
                    receipt["already_absent"] += 1
                else:
                    info = path.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or identity(info) != {key: item[key] for key in identity(info)}
                        or clock() - max(info.st_mtime, info.st_ctime) < MIN_AGE_SECONDS
                    ):
                        receipt["skipped_count"] += 1
                    else:
                        path.unlink()
                        receipt["deleted_count"] += 1
                        receipt["deleted_bytes"] += info.st_size
                receipt["processed"].append(item["name"])
                payload = connection.execute(
                    select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
                ).scalar_one()
                payload["cleanup_result"] = dict(receipt)
                connection.execute(
                    update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
                )
        return receipt

    def publish(self, job, owner, receipt, elapsed_ms):
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            self.queue.set_state(connection, current, "verifying", progress=90)
            self.queue.set_state(
                connection,
                current,
                "completed",
                progress=100,
                cleanup_result=receipt,
                elapsed_ms=elapsed_ms,
            )
