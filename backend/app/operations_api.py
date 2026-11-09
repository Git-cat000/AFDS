from uuid import UUID

from fastapi import APIRouter, Request

from app.operations_service import OperationsService

router = APIRouter()


@router.get("/system/status")
def system_status(request: Request):
    return OperationsService(request.app.state.database).status()


@router.get("/projects/{project_id}/operations")
def project_operations(project_id: UUID, request: Request):
    return OperationsService(request.app.state.database).project(project_id)
