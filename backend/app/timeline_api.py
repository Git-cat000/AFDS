"""Composition CRUD and bounded raw audio uploads; no media work in HTTP."""

import copy
import hashlib
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request

from app.audio_pipeline import MAX_UPLOAD_BYTES
from app.errors import DomainError
from app.storage import contained
from app.timeline_service import AnchorRequest, CompositionUpdate, TimelineService

router = APIRouter(prefix="/projects/{project_id}/timeline")


@router.put("/composition")
def update_composition(project_id: UUID, body: CompositionUpdate, request: Request):
    return TimelineService(request.app.state.database).update(project_id, body)


@router.post("/anchors/preview")
def preview_anchors(project_id: UUID, body: AnchorRequest, request: Request):
    return TimelineService(request.app.state.database).preview(project_id, body)


@router.post("/anchors/commit")
def commit_anchors(project_id: UUID, body: AnchorRequest, request: Request):
    return TimelineService(request.app.state.database).commit(project_id, body)


@router.post("/audio/import", status_code=202)
async def import_audio(
    project_id: UUID,
    request: Request,
    base_revision: int = Query(ge=1),
    name: str = Query(default="audio", min_length=1, max_length=200),
    rights_confirmed: bool = Query(default=False),
):
    if not rights_confirmed:
        raise DomainError("AUDIO_RIGHTS_REQUIRED", "Confirm your rights to use this audio", 422)
    declared = request.headers.get("content-length")
    if declared:
        try:
            size = int(declared)
        except ValueError as error:
            raise DomainError("INVALID_UPLOAD", "Invalid upload size", 422) from error
        if size <= 0 or size > MAX_UPLOAD_BYTES:
            raise DomainError("UPLOAD_TOO_LARGE", "Audio upload limit is 32 MB", 413)
    database = request.app.state.database
    service = TimelineService(database)
    with database.engine.connect() as connection:
        apr = service.engine.load(connection, project_id)
        service.engine.check_revision(apr, base_revision)
    relative = f"uploads/{project_id}/{uuid4()}.upload"
    destination = contained(database.settings.data_dir, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    sha = hashlib.sha256()
    try:
        with destination.open("xb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise DomainError("UPLOAD_TOO_LARGE", "Audio upload limit is 32 MB", 413)
                output.write(chunk)
                sha.update(chunk)
        if not total:
            raise DomainError("INVALID_UPLOAD", "Audio upload is empty", 422)
        from app.execution.queue import Queue, public_job

        with database.transaction() as connection:
            apr = service.engine.load(connection, project_id)
            service.engine.check_revision(apr, base_revision)
            before = copy.deepcopy(apr)
            job = Queue(database).enqueue(
                connection,
                apr,
                "import_audio",
                {
                    "project_id": str(project_id),
                    "staging_path": relative,
                    "sha256": sha.hexdigest(),
                    "name": name,
                    "rights_confirmed": True,
                    "format": apr["format"],
                    "provider_id": "local-audio-import",
                    "model_id": "ffmpeg-pcm",
                    "adapter_version": "0.4.0",
                },
            )
            service.engine.approve(
                connection,
                project_id,
                "audio_import_rights",
                {"name": name, "sha256": sha.hexdigest()},
            )
            apr["revision"] += 1
            service.engine.save(connection, apr, before, "Queue authorized local audio import")
        return public_job(job)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
