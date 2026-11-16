"""Composition CRUD and bounded raw audio uploads; no media work in HTTP."""

import copy
import hashlib
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from pydantic import Field

from app.audio_pipeline import MAX_UPLOAD_BYTES
from app.errors import DomainError
from app.storage import contained, ensure_space
from app.timeline_models import TimelineModel
from app.timeline_service import (
    AnchorRequest,
    CompositionUpdate,
    CompositionUpdateV2,
    TimelineService,
)

router = APIRouter(prefix="/projects/{project_id}/timeline")


class PreviewRequest(TimelineModel):
    base_revision: int = Field(ge=1)
    start_frame: int = Field(default=0, ge=0, le=216000)
    duration_frames: int = Field(gt=0, le=1800)
    max_width: int = Field(default=640, ge=160, le=640, multiple_of=2)


@router.post("/preview", status_code=202)
def render_preview(project_id: UUID, body: PreviewRequest, request: Request):
    from app.composition import build_composition
    from app.composition_render import preview_spec
    from app.execution.queue import Queue, public_job
    from app.project_engine import ProjectEngine

    database = request.app.state.database
    engine = ProjectEngine(database)
    with database.transaction() as connection:
        apr = engine.load(connection, project_id)
        engine.check_revision(apr, body.base_revision)
        spec = build_composition(apr)
        rate = apr["format"]["fps_num"] / apr["format"]["fps_den"]
        if (
            body.start_frame + body.duration_frames > spec["total_frames"]
            or body.duration_frames > rate * 30
        ):
            raise DomainError(
                "INVALID_PREVIEW_RANGE", "Choose up to 30 seconds within the film", 422
            )
        spec = preview_spec(spec, body.start_frame, body.duration_frames, body.max_width)
        return public_job(Queue(database).enqueue(connection, apr, "render_preview", spec))


@router.put("/composition")
def update_composition(project_id: UUID, body: CompositionUpdate, request: Request):
    return TimelineService(request.app.state.database).update(project_id, body)


@router.post("/anchors/preview")
def preview_anchors(project_id: UUID, body: AnchorRequest, request: Request):
    return TimelineService(request.app.state.database).preview(project_id, body)


@router.put("/composition-v2")
def update_composition_v2(project_id: UUID, body: CompositionUpdateV2, request: Request):
    return TimelineService(request.app.state.database).update_v2(project_id, body)


@router.post("/composition/preview")
def preview_composition(project_id: UUID, body: CompositionUpdateV2, request: Request):
    return TimelineService(request.app.state.database).preview_composition(project_id, body)


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
    return await stage_media_import(
        project_id, request, base_revision, name, rights_confirmed, "audio"
    )


@router.post("/video/import", status_code=202)
async def import_video(
    project_id: UUID,
    request: Request,
    base_revision: int = Query(ge=1),
    name: str = Query(default="video", min_length=1, max_length=200),
    rights_confirmed: bool = Query(default=False),
    extract_audio: bool = Query(default=True),
):
    return await stage_media_import(
        project_id, request, base_revision, name, rights_confirmed, "video", extract_audio
    )


async def stage_media_import(
    project_id, request, base_revision, name, rights_confirmed, media_kind, extract_audio=False
):
    from app.video_import import MAX_VIDEO_UPLOAD_BYTES

    max_bytes = MAX_UPLOAD_BYTES if media_kind == "audio" else MAX_VIDEO_UPLOAD_BYTES
    label = media_kind.capitalize()
    limit_message = f"{label} upload limit is {max_bytes // 1024**2} MB"
    if not rights_confirmed:
        raise DomainError(
            f"{media_kind.upper()}_RIGHTS_REQUIRED",
            f"Confirm your rights to use this {media_kind}",
            422,
        )
    declared = request.headers.get("content-length")
    if declared:
        try:
            size = int(declared)
        except ValueError as error:
            raise DomainError("INVALID_UPLOAD", "Invalid upload size", 422) from error
        if size <= 0 or size > max_bytes:
            raise DomainError("UPLOAD_TOO_LARGE", limit_message, 413)
    database = request.app.state.database
    ensure_space(database.settings.data_dir, int(declared or 0))
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
                if total > max_bytes:
                    raise DomainError("UPLOAD_TOO_LARGE", limit_message, 413)
                output.write(chunk)
                sha.update(chunk)
        if not total:
            raise DomainError("INVALID_UPLOAD", f"{label} upload is empty", 422)
        from app.execution.queue import Queue, public_job

        with database.transaction() as connection:
            apr = service.engine.load(connection, project_id)
            service.engine.check_revision(apr, base_revision)
            before = copy.deepcopy(apr)
            payload = {
                "project_id": str(project_id),
                "staging_path": relative,
                "sha256": sha.hexdigest(),
                "name": name,
                "rights_confirmed": True,
                "format": apr["format"],
                "provider_id": f"local-{media_kind}-import",
                "model_id": "ffmpeg-pcm" if media_kind == "audio" else "ffmpeg-h264",
                "adapter_version": "0.4.0" if media_kind == "audio" else "0.5.0",
            }
            if media_kind == "video":
                payload["extract_audio"] = extract_audio
            job = Queue(database).enqueue(
                connection,
                apr,
                f"import_{media_kind}",
                payload,
            )
            service.engine.approve(
                connection,
                project_id,
                f"{media_kind}_import_rights",
                {"name": name, "sha256": sha.hexdigest()},
            )
            apr["revision"] += 1
            service.engine.save(
                connection, apr, before, f"Queue authorized local {media_kind} import"
            )
        return public_job(job)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
