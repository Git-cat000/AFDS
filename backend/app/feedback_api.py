from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.access import AccessRoute
from app.feedback_service import FeedbackRequest, FeedbackService

router = APIRouter(prefix="/projects/{project_id}/feedback", route_class=AccessRoute)


@router.post("", status_code=201)
def create(project_id: UUID, body: FeedbackRequest, request: Request):
    return FeedbackService(request.app.state.database).create(project_id, body)


@router.get("")
def page(
    project_id: UUID,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    shot_id: UUID | None = None,
):
    return FeedbackService(request.app.state.database).page(project_id, limit, cursor, shot_id)
