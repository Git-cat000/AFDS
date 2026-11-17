import copy
import hashlib
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request

from app.access import AccessRoute
from app.errors import DomainError
from app.execution.queue import Queue, public_job
from app.image_service import MAX_IMAGE_BYTES
from app.project_engine import ProjectEngine
from app.storage import contained, ensure_space

router = APIRouter(prefix="/projects/{project_id}/images", route_class=AccessRoute)


@router.post("/import", status_code=202)
async def import_image(
    project_id: UUID,
    request: Request,
    base_revision: int = Query(ge=1),
    name: str = Query(default="Reference", min_length=1, max_length=200),
    rights_confirmed: bool = False,
):
    if not rights_confirmed:
        raise DomainError(
            "IMAGE_RIGHTS_REQUIRED", "Confirm your rights to use the reference image", 422
        )
    declared = request.headers.get("content-length")
    try:
        if declared is not None and not 0 < int(declared) <= MAX_IMAGE_BYTES:
            raise DomainError("UPLOAD_TOO_LARGE", "Image upload limit is 16 MiB", 413)
    except ValueError as error:
        raise DomainError("INVALID_UPLOAD", "Invalid content length", 422) from error
    database = request.app.state.database
    ensure_space(database.settings.data_dir, int(declared or 0))
    engine = ProjectEngine(database)
    with database.engine.connect() as connection:
        engine.check_revision(engine.load(connection, project_id), base_revision)
    relative = f"uploads/{project_id}/{uuid4()}.image"
    path = contained(database.settings.data_dir, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    total, digest = 0, hashlib.sha256()
    try:
        with path.open("xb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise DomainError("UPLOAD_TOO_LARGE", "Image upload limit is 16 MiB", 413)
                output.write(chunk)
                digest.update(chunk)
        if not total:
            raise DomainError("INVALID_UPLOAD", "Image upload is empty", 422)
        with database.transaction() as connection:
            apr = engine.load(connection, project_id)
            engine.check_revision(apr, base_revision)
            before = copy.deepcopy(apr)
            job = Queue(database).enqueue(
                connection,
                apr,
                "import_image",
                {
                    "staging_path": relative,
                    "sha256": digest.hexdigest(),
                    "size_bytes": total,
                    "name": name,
                    "base_revision": base_revision,
                    "provider_id": "local-image-import",
                    "model_id": "canonical-png",
                    "adapter_version": "0.6.0",
                },
            )
            engine.approve(
                connection,
                project_id,
                "image_import_rights",
                {"name": name, "sha256": digest.hexdigest()},
            )
            apr["revision"] += 1
            engine.save(connection, apr, before, "Queue authorized local image import")
        return public_job(job)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
