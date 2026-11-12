from uuid import UUID

from fastapi import APIRouter, Request

from app.planning_service import PlanningService, SemanticPlanningRequest, StoryPlanningRequest

router = APIRouter(prefix="/projects/{project_id}/planning")


@router.post("/storyboard", status_code=202)
def storyboard(project_id: UUID, body: StoryPlanningRequest, request: Request):
    return PlanningService(request.app.state.database).enqueue(project_id, "storyboard", body)


@router.post("/semantic", status_code=202)
def semantic(project_id: UUID, body: SemanticPlanningRequest, request: Request):
    return PlanningService(request.app.state.database).enqueue(project_id, "semantic", body)
