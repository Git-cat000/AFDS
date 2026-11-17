import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.access import AccessRoute, require_project_access
from app.collaboration_schema import OperationBatch, PresenceUpdate, UndoBatch
from app.collaboration_service import CollaborationService
from app.errors import DomainError
from app.identity_context import actor_id

router = APIRouter(prefix="/projects/{project_id}", route_class=AccessRoute)


@router.get("/workspace/collaboration")
def snapshot(project_id: UUID, request: Request):
    return CollaborationService(request.app.state.database).snapshot(project_id)


@router.post("/workspace/operations")
def apply_operations(project_id: UUID, body: OperationBatch, request: Request):
    database = request.app.state.database
    return CollaborationService(database).apply_operations(
        project_id, body.operations, actor_id(database.settings)
    )


@router.post("/workspace/operations/{operation_id}/undo")
def undo_operation(project_id: UUID, operation_id: UUID, request: Request):
    database = request.app.state.database
    return CollaborationService(database).undo_operation(
        project_id, operation_id, actor_id(database.settings)
    )


@router.post("/workspace/undo")
def undo_batch(project_id: UUID, body: UndoBatch, request: Request):
    database = request.app.state.database
    return CollaborationService(database).undo_operations(
        project_id, body.operation_ids, actor_id(database.settings)
    )


@router.get("/presence")
def online(project_id: UUID, request: Request):
    return CollaborationService(request.app.state.database).online(project_id)


@router.put("/presence")
def presence(project_id: UUID, body: PresenceUpdate, request: Request):
    database = request.app.state.database
    principal = request.state.principal
    return CollaborationService(database).presence(
        project_id, principal.user_id, principal.session_id or "local", body.entity_id
    )


@router.get("/events/stream")
async def stream(project_id: UUID, request: Request, after: int = Query(default=0, ge=0)):
    service = CollaborationService(request.app.state.database)
    raw_cursor = request.headers.get("Last-Event-ID", "")
    if raw_cursor.isascii() and raw_cursor.isdecimal() and len(raw_cursor) <= 18:
        after = max(after, int(raw_cursor))

    async def generate():
        cursor = after
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            try:
                await run_in_threadpool(require_project_access, request, project_id, "read")
                page = await run_in_threadpool(service.events, project_id, cursor)
            except DomainError as error:
                yield "event: access_revoked\ndata: " + json.dumps({"code": error.code}) + "\n\n"
                return
            if page.get("reset"):
                cursor = page["cursor"]
                yield f"id: {cursor}\nevent: snapshot_required\ndata: {{}}\n\n"
            for item in page["items"]:
                cursor = item["event_id"]
                yield f"id: {cursor}\nevent: project\ndata: {json.dumps(item)}\n\n"
            if time.monotonic() - heartbeat >= 15:
                heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            if len(page["items"]) < 100:
                await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
