"""Local skill library and revisioned project guidance endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError

from app.errors import DomainError
from app.project_engine import ProjectEngine
from app.schemas import ChangeSet
from app.skill_packages import SkillLibrary, invalid, public_package
from app.skill_schema import (
    MAX_UNPACKED,
    MAX_UPLOAD,
    GuidanceUpdate,
    ProductionCheckRequest,
    ScriptApproval,
    ScriptPreviewRequest,
    SkillImport,
    Stage,
)

router = APIRouter()


async def bounded_body(request, limit):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            raise DomainError("UPLOAD_TOO_LARGE", "Skill import exceeds supported size", 413)
        content.extend(chunk)
    return bytes(content)


@router.get("/skills")
def library(
    request: Request,
    stage: Stage | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return SkillLibrary(request.app.state.database).list(stage, offset, limit)


@router.post("/skills/import", status_code=201)
async def import_files(request: Request):
    try:
        body = SkillImport.model_validate_json(
            await bounded_body(request, MAX_UNPACKED * 4 // 3 + 1024**2)
        )
    except ValidationError as error:
        raise invalid("Provide bounded skill files, stages and source attribution") from error
    return SkillLibrary(request.app.state.database).import_files(
        body.files, body.stages, body.source
    )


@router.post("/skills/import-zip", status_code=201)
async def import_zip(
    request: Request, stages: str = Query(max_length=200), source: str = Query("", max_length=1000)
):
    try:
        metadata = SkillImport(
            files=[{"path": "SKILL.md", "content_base64": ""}],
            stages=stages.split(","),
            source=source,
        )
    except ValidationError as error:
        raise invalid("Choose valid production stages") from error
    return SkillLibrary(request.app.state.database).import_zip(
        await bounded_body(request, MAX_UPLOAD), metadata.stages, metadata.source
    )


@router.get("/skills/{package_id}")
def detail(package_id: str, request: Request):
    return public_package(SkillLibrary(request.app.state.database).get(package_id))


@router.get("/skills/{package_id}/resource")
def resource(package_id: str, request: Request, path: str = Query(max_length=240)):
    content = SkillLibrary(request.app.state.database).resource(package_id, path)
    return Response(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )


def configure(project_id, body, request, commit):
    database = request.app.state.database
    selected = {
        stage: [SkillLibrary(database).snapshot(value) for value in selections]
        for stage, selections in body.selections.items()
    }
    change = ChangeSet(
        base_revision=body.base_revision,
        approved=body.approved,
        reason="Configure production guidance",
        operations=[
            {
                "op": "set",
                "entity_type": "project",
                "entity_id": project_id,
                "field": "production_guidance",
                "value": selected,
            }
        ],
    )
    if commit and not body.approved:
        raise DomainError("APPROVAL_REQUIRED", "Approve the selected production guidance", 409)
    return ProjectEngine(database).changeset(project_id, change, commit=commit)


@router.post("/projects/{project_id}/skills/guidance/preview")
def preview(project_id: UUID, body: GuidanceUpdate, request: Request):
    return configure(project_id, body, request, False)


@router.post("/projects/{project_id}/skills/guidance/commit")
def commit(project_id: UUID, body: GuidanceUpdate, request: Request):
    return configure(project_id, body, request, True)


@router.post("/projects/{project_id}/skills/scripts/preview")
def script_preview(project_id: UUID, body: ScriptPreviewRequest, request: Request):
    from app.skill_execution import SkillExecution

    return SkillExecution(request.app.state.database).preview(project_id, body)


@router.post("/projects/{project_id}/skills/scripts/{plan_id}/execute", status_code=202)
def script_execute(project_id: UUID, plan_id: UUID, body: ScriptApproval, request: Request):
    from app.skill_execution import SkillExecution

    return SkillExecution(request.app.state.database).approve(project_id, plan_id, body)


@router.post("/projects/{project_id}/skills/checks", status_code=202)
def check(project_id: UUID, body: ProductionCheckRequest, request: Request):
    from app.skill_execution import SkillExecution

    return SkillExecution(request.app.state.database).enqueue_check(project_id, body)


@router.get("/projects/{project_id}/skills/runs")
def runs(
    project_id: UUID,
    request: Request,
    stage: Stage | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    from app.skill_execution import SkillExecution

    return SkillExecution(request.app.state.database).list(project_id, stage, offset, limit)


@router.get("/projects/{project_id}/skills/runs/{job_id}/files/{artifact_id}")
def output(project_id: UUID, job_id: UUID, artifact_id: str, request: Request):
    from app.skill_execution import SkillExecution

    path, artifact = SkillExecution(request.app.state.database).artifact(
        project_id, job_id, artifact_id
    )
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=artifact["name"].split("/")[-1],
        headers={"X-Content-Type-Options": "nosniff"},
    )
