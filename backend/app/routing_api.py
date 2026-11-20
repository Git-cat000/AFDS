"""Project model routing previews and owner-managed policies."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.access import AccessRoute
from app.routing_service import RoutePreviewRequest, RoutingPolicyUpdate, RoutingService

router = APIRouter(prefix="/projects/{project_id}/routing", route_class=AccessRoute)


@router.get("")
def routing_settings(project_id: UUID, request: Request):
    service = RoutingService(request.app.state.database)
    apr = service.engine.read(project_id)
    return {
        "revision": apr["revision"],
        "policies": apr.get("routing_policies", {}),
        "candidates": [
            value.model_dump(mode="json", exclude={"project_ids", "all_projects"})
            for value in service.inventory(project_id)
        ],
    }


@router.put("/policy")
def routing_policy(project_id: UUID, body: RoutingPolicyUpdate, request: Request):
    return RoutingService(request.app.state.database).update_policy(project_id, body)


@router.post("/preview")
def routing_preview(project_id: UUID, body: RoutePreviewRequest, request: Request):
    return RoutingService(request.app.state.database).preview(project_id, body)
