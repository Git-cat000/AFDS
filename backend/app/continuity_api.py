from uuid import UUID

from fastapi import APIRouter, Request

from app.continuity import ContinuityService
from app.continuity_schema import ContinuityChange, SemanticEditRequest
from app.schemas import RevisionRequest

router = APIRouter()


def service(request):
    return ContinuityService(request.app.state.database)


@router.get("/projects/{project_id}/continuity")
def continuity(project_id: UUID, request: Request):
    return service(request).read(project_id)


@router.post("/projects/{project_id}/continuity/preview")
def preview(project_id: UUID, body: ContinuityChange, request: Request):
    return service(request).preview(project_id, body)


@router.post("/projects/{project_id}/continuity/plans/{plan_id}/execute")
def execute(project_id: UUID, plan_id: UUID, body: RevisionRequest, request: Request):
    return service(request).execute(project_id, plan_id, body)


@router.post("/projects/{project_id}/semantic-edits")
def semantic_propose(project_id: UUID, body: SemanticEditRequest, request: Request):
    return service(request).semantic_propose(project_id, body)


@router.post("/projects/{project_id}/semantic-edits/{plan_id}/execute")
def semantic_execute(project_id: UUID, plan_id: UUID, body: RevisionRequest, request: Request):
    return service(request).semantic_execute(project_id, plan_id, body)
