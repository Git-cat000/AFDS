"""Stage bytes and atomically enqueue recovery; ZIP/media work belongs to Worker."""

import hashlib
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app import db, restore_schema
from app.errors import DomainError
from app.execution.queue import Queue, public_job, serialize
from app.project_engine import ProjectEngine
from app.schemas import APR, ProjectCreate
from app.storage import contained

router = APIRouter()
RESTORE_NAMESPACE = UUID("8317baad-705f-49f4-aa96-cdb9a4a0c243")


@router.post("/projects/restore", status_code=202)
async def restore_project(
    request: Request,
    request_id: UUID,
    title: str = Query(min_length=1, max_length=200),
    approved: bool = False,
):
    title = title.strip()
    if not approved or not title:
        raise DomainError("APPROVAL_REQUIRED", "Confirm restoring to a new project copy", 409)
    limit = restore_schema.MAX_ARCHIVE_BYTES
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError as error:
        raise DomainError("INVALID_UPLOAD", "Invalid archive upload size", 422) from error
    if declared < 0 or declared > limit:
        raise DomainError("UPLOAD_TOO_LARGE", "Project archive upload limit is 2 GB", 413)
    database = request.app.state.database
    pid = str(uuid5(RESTORE_NAMESPACE, str(request_id)))
    relative = f"uploads/{pid}/{uuid4()}.zip"
    destination = contained(database.settings.data_dir, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count, digest = 0, hashlib.sha256()
    try:
        with destination.open("xb") as writer:
            async for chunk in request.stream():
                count += len(chunk)
                if count > limit:
                    raise DomainError(
                        "UPLOAD_TOO_LARGE", "Project archive upload limit is 2 GB", 413
                    )
                digest.update(chunk)
                writer.write(chunk)
        if not count:
            raise DomainError("INVALID_UPLOAD", "Project archive upload is empty", 422)
        service, queue = ProjectEngine(database), Queue(database)
        with database.transaction() as connection:
            existing = connection.execute(
                select(db.projects).where(db.projects.c.id == pid)
            ).first()
            if existing:
                rows = connection.execute(
                    select(db.jobs).where(db.jobs.c.project_id == pid)
                ).mappings()
                job = next(
                    (
                        serialize(row)
                        for row in rows
                        if row["payload"].get("operation") == "restore_project"
                    ),
                    None,
                )
                if (
                    not job
                    or job["request"]["sha256"] != digest.hexdigest()
                    or job["request"]["title"] != title
                ):
                    raise DomainError(
                        "RESTORE_REQUEST_CONFLICT",
                        "This restore request ID has different input",
                        409,
                    )
                apr = service.load(connection, pid)
                if job["state"] == "completed":
                    queue.reactivate(connection, apr, job)
                destination.unlink(missing_ok=True)
                return public_job(job)
            apr = APR(
                **ProjectCreate(title=title, brief="Portable project recovery").model_dump(),
                project_id=pid,
            ).model_dump(mode="json")
            service.save(connection, apr, None, "Create restore destination")
            body = restore_schema.RestoreJobRequest(
                project_id=pid,
                request_id=request_id,
                title=title,
                staging_path=relative,
                sha256=digest.hexdigest(),
                size_bytes=count,
                base_revision=apr["revision"],
            ).model_dump(mode="json")
            job = queue.enqueue(connection, apr, "restore_project", body)
            service.approve(
                connection,
                pid,
                "restore_project_copy",
                {"request_id": str(request_id), "sha256": digest.hexdigest()},
            )
            return public_job(job)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
