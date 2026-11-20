from uuid import UUID

from fastapi import APIRouter, Request

from app.access import AccessRoute
from app.operations_service import OperationsService
from app.schemas import Schema
from app.worker_registry import WorkerRegistry

router = APIRouter(route_class=AccessRoute)


@router.get("/system/status")
def system_status(request: Request):
    return OperationsService(request.app.state.database).status()


class NodeDrain(Schema):
    draining: bool


@router.get("/system/workers")
def workers(request: Request):
    return WorkerRegistry(request.app.state.database).list()


@router.post("/system/workers/{node_id}/drain")
def drain_worker(node_id: str, body: NodeDrain, request: Request):
    return WorkerRegistry(request.app.state.database).drain(node_id, body.draining)


@router.get("/projects/{project_id}/operations")
def project_operations(project_id: UUID, request: Request):
    return OperationsService(request.app.state.database).project(project_id)
