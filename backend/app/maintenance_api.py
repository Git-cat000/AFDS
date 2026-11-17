from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.access import AccessRoute
from app.maintenance_service import CleanupRequest, MaintenanceService
from app.schemas import RevisionRequest

router = APIRouter(route_class=AccessRoute)


@router.get("/system/report")
def report(request: Request, project_id: UUID | None = None):
    data = MaintenanceService(request.app.state.database).report(project_id)
    return JSONResponse(
        data, headers={"Content-Disposition": 'attachment; filename="afds-diagnostics.json"'}
    )


@router.post("/projects/{project_id}/maintenance/cleanup/preview")
def preview(project_id: UUID, body: RevisionRequest, request: Request):
    return MaintenanceService(request.app.state.database).preview(project_id, body)


@router.post("/projects/{project_id}/maintenance/cleanup", status_code=202)
def cleanup(project_id: UUID, body: CleanupRequest, request: Request):
    return MaintenanceService(request.app.state.database).enqueue(project_id, body)
