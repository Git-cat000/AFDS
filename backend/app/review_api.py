from uuid import UUID

from fastapi import APIRouter, Request

from app.review_service import ReviewRequest, ReviewService

router = APIRouter(prefix="/projects/{project_id}/reviews")


@router.post("", status_code=202)
def review(project_id: UUID, body: ReviewRequest, request: Request):
    return ReviewService(request.app.state.database).enqueue(project_id, body)


@router.get("")
def reports(project_id: UUID, request: Request):
    return ReviewService(request.app.state.database).list(project_id)
