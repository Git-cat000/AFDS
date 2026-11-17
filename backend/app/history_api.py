from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.access import AccessRoute
from app.history_service import HistoryService, StateFilter

router = APIRouter(prefix="/projects/{project_id}", route_class=AccessRoute)


@router.get("/jobs/page")
def jobs(
    project_id: UUID,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    state: StateFilter = "all",
):
    return HistoryService(request.app.state.database).jobs(project_id, limit, cursor, state)


@router.get("/budget/summary")
def budget(project_id: UUID, request: Request):
    return HistoryService(request.app.state.database).budget(project_id)


@router.get("/budget/entries")
def entries(
    project_id: UUID,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
):
    return HistoryService(request.app.state.database).entries(project_id, limit, cursor)


@router.get("/revisions/page")
def revisions(
    project_id: UUID,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
):
    return HistoryService(request.app.state.database).revisions(project_id, limit, cursor)


@router.get("/activity")
def activity(project_id: UUID, request: Request, after: int | None = Query(default=None, ge=0)):
    return HistoryService(request.app.state.database).activity(project_id, after)
