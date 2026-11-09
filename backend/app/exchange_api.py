"""Revision-checked enqueue and owned, integrity-checked artifact download."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import DomainError
from app.exchange import _hash_file, _relative_file, export_request
from app.execution.queue import Queue
from app.project_engine import ProjectEngine
from app.schemas import RevisionRequest

router = APIRouter()


class ExportRequest(RevisionRequest):
    format: Literal["otio", "zip"]


class ExportArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relative_path: str = Field(min_length=1, max_length=500)
    filename: str = Field(pattern=r"^[^/\\\x00-\x1f]{1,200}$")
    media_type: Literal["application/zip", "application/vnd.otio+json"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int | None = Field(default=None, gt=0)


@router.post("/projects/{project_id}/exports", status_code=202)
def create_export(project_id: UUID, body: ExportRequest, request: Request):
    from app.execution.queue import public_job

    database = request.app.state.database
    engine = ProjectEngine(database)
    with database.transaction() as connection:
        apr = engine.load(connection, project_id)
        engine.check_revision(apr, body.base_revision)
        job = Queue(database).enqueue(
            connection, apr, "export_project", export_request(apr, body.format)
        )
        return public_job(job)


@router.get("/projects/{project_id}/exports/{job_id}/file")
def export_file(project_id: UUID, job_id: UUID, request: Request):
    database = request.app.state.database
    with database.engine.connect() as connection:
        ProjectEngine(database).load(connection, project_id)
        job = Queue(database).get(connection, project_id, job_id)
    if job["operation"] != "export_project" or job["state"] != "completed":
        raise DomainError("EXPORT_NOT_READY", "Export is not ready for download", 409)
    try:
        artifact = ExportArtifact.model_validate(job.get("artifact"))
    except ValidationError as error:
        raise DomainError("EXPORT_NOT_READY", "Export artifact is unavailable", 409) from error
    path = _relative_file(
        database.settings.data_dir,
        artifact.relative_path,
        database.settings.data_dir / "projects" / str(project_id) / "exports",
    )
    sha, size = _hash_file(path, lambda: None)
    if sha != artifact.sha256 or (artifact.size_bytes is not None and size != artifact.size_bytes):
        raise DomainError("HASH_MISMATCH", "Export artifact integrity check failed", 422)
    return FileResponse(path, media_type=artifact.media_type, filename=artifact.filename)
