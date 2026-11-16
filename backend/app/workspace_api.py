from uuid import UUID

from fastapi import APIRouter, Request

from app.workspace_schema import WorkspaceUpdate
from app.workspace_service import WorkspaceService

router = APIRouter(prefix="/projects/{project_id}/workspace")


@router.get("")
def read_workspace(project_id: UUID, request: Request):
    return WorkspaceService(request.app.state.database).read(project_id)


@router.put("")
def update_workspace(project_id: UUID, body: WorkspaceUpdate, request: Request):
    return WorkspaceService(request.app.state.database).update(project_id, body)
