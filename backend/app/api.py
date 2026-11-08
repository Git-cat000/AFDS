from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from app import db, director
from app.project_engine import ProjectEngine, rebuild_timeline
from app.schemas import (
    APR,
    ChangeSet,
    DirectorRequest,
    GenerateRequest,
    JobRetryRequest,
    MediaGenerateRequest,
    Operation,
    PatchRequest,
    ProjectCreate,
    RemoteRecoveryRequest,
    RevisionRequest,
    Scene,
    Shot,
    TimelineRequest,
)

router = APIRouter()


def engine(request: Request):
    return ProjectEngine(request.app.state.database)


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, request: Request):
    return engine(request).create(body)


@router.post("/projects/import", status_code=201)
def import_project(body: APR, request: Request):
    return engine(request).import_apr(body)


@router.get("/projects")
def list_projects(request: Request):
    with request.app.state.database.engine.connect() as connection:
        return list(
            connection.execute(
                select(db.projects.c.payload).where(db.projects.c.archived.is_(False))
            ).scalars()
        )


@router.get("/projects/{project_id}")
@router.get("/projects/{project_id}/apr")
@router.get("/projects/{project_id}/export")
def read_project(project_id: UUID, request: Request):
    return engine(request).read(project_id)


@router.get("/projects/{project_id}/revisions")
def history(project_id: UUID, request: Request):
    service = engine(request)
    with service.database.engine.connect() as connection:
        service.load(connection, project_id)
        return [
            dict(r)
            for r in connection.execute(
                select(
                    db.revisions.c.revision,
                    db.revisions.c.reason,
                    db.revisions.c.author,
                    db.revisions.c.created_at,
                )
                .where(db.revisions.c.project_id == str(project_id))
                .order_by(db.revisions.c.revision.desc())
            ).mappings()
        ]


@router.post("/projects/{project_id}/changesets/{mode}")
def changeset(project_id: UUID, mode: str, body: ChangeSet, request: Request):
    from app.errors import DomainError

    if mode not in {"preview", "commit"}:
        raise DomainError("NOT_FOUND", "Unknown changeset operation", 404)
    return engine(request).changeset(project_id, body, commit=mode == "commit")


@router.patch("/projects/{project_id}/shots/{shot_id}")
def patch_shot(project_id: UUID, shot_id: UUID, body: PatchRequest, request: Request):
    changes = ChangeSet(
        base_revision=body.base_revision,
        approved=body.approved,
        reason=body.reason,
        operations=[
            Operation(op="set", entity_type="shot", entity_id=shot_id, field=field, value=value)
            for field, value in body.patch.items()
        ],
    )
    return engine(request).changeset(project_id, changes, commit=True)


@router.post("/projects/{project_id}/revisions/{revision}/revert")
def revert(project_id: UUID, revision: int, body: RevisionRequest, request: Request):
    return engine(request).revert(project_id, revision, body)


@router.patch("/projects/{project_id}/scenes/{scene_id}")
def patch_scene(project_id: UUID, scene_id: UUID, body: PatchRequest, request: Request):
    changes = ChangeSet(
        base_revision=body.base_revision,
        approved=body.approved,
        reason=body.reason,
        operations=[
            Operation(op="set", entity_type="scene", entity_id=scene_id, field=field, value=value)
            for field, value in body.patch.items()
        ],
    )
    return engine(request).changeset(project_id, changes, commit=True)


@router.delete("/projects/{project_id}/shots/{shot_id}")
def archive_shot(project_id: UUID, shot_id: UUID, body: RevisionRequest, request: Request):
    patch = PatchRequest(**body.model_dump(), patch={"status": "archived"}, reason="Archive shot")
    return patch_shot(project_id, shot_id, patch, request)


@router.delete("/projects/{project_id}/scenes/{scene_id}")
def archive_scene(project_id: UUID, scene_id: UUID, body: RevisionRequest, request: Request):
    from app.project_engine import find

    service = engine(request)

    def archive(apr, connection):
        scene = find(apr["scenes"], scene_id)
        operations = [
            Operation(op="set", entity_type="shot", entity_id=sid, field="status", value="archived")
            for sid in scene["shot_ids"]
        ]
        if operations:
            changes = ChangeSet(**body.model_dump(), operations=operations)
            service.apply_changes(connection, apr, changes)

    return service.mutate(project_id, body.base_revision, "Archive scene shots", archive)


@router.delete("/projects/{project_id}")
def archive_project(project_id: UUID, body: RevisionRequest, request: Request):
    from app.errors import DomainError
    from app.execution.queue import ACTIVE, Queue, serialize

    service = engine(request)
    if not body.approved:
        raise DomainError("APPROVAL_REQUIRED", "Approve archiving this project", 409)

    def archive(apr, connection):
        queue = Queue(service.database)
        rows = (
            connection.execute(
                select(db.jobs).where(
                    db.jobs.c.project_id == str(project_id),
                    db.jobs.c.state.in_(ACTIVE | {"queued", "retry_wait"}),
                )
            )
            .mappings()
            .all()
        )
        if any(row["state"] in ACTIVE for row in rows):
            raise DomainError("JOBS_ACTIVE", "Cancel active jobs before archiving", 409)
        for row in rows:
            queue.set_state(connection, serialize(row), "cancelled")
        apr["status"] = "archived"
        service.approve(connection, project_id, "archive_project", {})

    return service.mutate(project_id, body.base_revision, "Archive project", archive)


@router.get("/projects/{project_id}/scenes")
def scenes(project_id: UUID, request: Request):
    return engine(request).read(project_id)["scenes"]


@router.get("/projects/{project_id}/shots")
def shots(project_id: UUID, request: Request):
    return engine(request).read(project_id)["shots"]


class SceneCreate(RevisionRequest):
    scene: Scene


class ShotCreate(RevisionRequest):
    shot: Shot


@router.post("/projects/{project_id}/scenes", status_code=201)
def create_scene(project_id: UUID, body: SceneCreate, request: Request):
    return engine(request).mutate(
        project_id,
        body.base_revision,
        "Create scene",
        lambda apr, _: apr["scenes"].append(body.scene.model_dump(mode="json")),
    )


@router.post("/projects/{project_id}/shots", status_code=201)
def create_shot(project_id: UUID, body: ShotCreate, request: Request):
    from app.project_engine import find

    def add(apr, _):
        shot = body.shot.model_dump(mode="json")
        scene = find(apr["scenes"], shot["scene_id"])
        scene["shot_ids"].append(shot["id"])
        apr["shots"].append(shot)
        ids = [i["shot_id"] for i in apr["timeline"]["tracks"][0]["items"]]
        rebuild_timeline(apr, ids + [shot["id"]])
        apr["render_status"] = "stale"

    return engine(request).mutate(project_id, body.base_revision, "Create shot", add)


@router.post("/projects/{project_id}/timeline")
def timeline(project_id: UUID, body: TimelineRequest, request: Request):
    def reorder(apr, _):
        rebuild_timeline(apr, [str(s) for s in body.shot_ids])
        apr["render_status"] = "stale"

    return engine(request).mutate(project_id, body.base_revision, "Reorder timeline", reorder)


@router.post("/projects/{project_id}/director")
def propose(project_id: UUID, body: DirectorRequest, request: Request):
    return director.propose(engine(request), project_id, body)


@router.get("/projects/{project_id}/plans")
def plans(project_id: UUID, request: Request):
    service = engine(request)
    with service.database.engine.connect() as connection:
        service.load(connection, project_id)
        return list(
            connection.execute(
                select(db.plans.c.payload).where(db.plans.c.project_id == str(project_id))
            ).scalars()
        )


@router.post("/projects/{project_id}/plans/{plan_id}/execute")
def execute(project_id: UUID, plan_id: UUID, body: RevisionRequest, request: Request):
    return director.execute(engine(request), project_id, plan_id, body)


class PlanEdit(RevisionRequest):
    shots: list[Shot]


@router.patch("/projects/{project_id}/plans/{plan_id}")
def edit_plan(project_id: UUID, plan_id: UUID, body: PlanEdit, request: Request):
    from sqlalchemy import update

    from app.errors import DomainError

    service = engine(request)
    with service.database.transaction() as connection:
        apr = service.load(connection, project_id)
        service.check_revision(apr, body.base_revision)
        row = (
            connection.execute(
                select(db.plans).where(
                    db.plans.c.id == str(plan_id),
                    db.plans.c.project_id == str(project_id),
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DomainError("NOT_FOUND", "Plan not found", 404)
        plan = row["payload"]
        service.check_revision(apr, plan["base_revision"])
        if plan["state"] != "proposed":
            raise DomainError("PLAN_EXECUTED", "Only proposed plans can be edited", 409)
        edited = [s.model_dump(mode="json") for s in body.shots]
        if {s["id"] for s in edited} != {s["id"] for s in plan["shots"]}:
            raise DomainError("PLAN_INVALID", "Keep the proposed shot identifiers", 422)
        if any(s["scene_id"] != plan["scene"]["id"] for s in edited):
            raise DomainError("PLAN_INVALID", "Shot scene must match the proposal", 422)
        plan["shots"] = edited
        connection.execute(
            update(db.plans).where(db.plans.c.id == str(plan_id)).values(payload=plan)
        )
        return plan


@router.get("/models")
def models(request: Request):
    from app.adapters import providers
    from app.provider_settings import CAPABILITIES, ProviderStore

    result = providers(request.app.state.settings)
    for config in ProviderStore(request.app.state.database).list()["providers"]:
        verified = config.get("protocol") != "comfyui" or (
            config["health"]["status"] == "connected"
            and config["health"].get("generation_tested", False)
        )
        result.append(
            {
                "provider_id": config["provider_id"],
                "display_name": config["display_name"],
                "backend_type": "remote",
                "availability": ("available" if verified else "unverified")
                if config["enabled"]
                else "disabled",
                "capabilities": [CAPABILITIES[config["kind"]]] if verified else [],
                "provisional_capabilities": [] if verified else [CAPABILITIES[config["kind"]]],
                "protocol": config.get("protocol", "openai-compatible"),
                "health": config["health"],
                "pricing": {
                    "mode": "estimated" if config.get("request_cost") is not None else "unknown",
                    "amount": config.get("request_cost"),
                    "currency": config.get("cost_currency", "USD"),
                },
                "adapter_version": "0.2.0",
                "model_id": config["model"],
            }
        )
    return result


@router.post("/projects/{project_id}/generate", status_code=202)
def generate(project_id: UUID, body: GenerateRequest, request: Request):
    from app.execution.queue import Queue

    return Queue(request.app.state.database).generate(project_id, body)


@router.post("/projects/{project_id}/render", status_code=202)
def render(project_id: UUID, body: RevisionRequest, request: Request):
    from app.execution.queue import Queue

    return Queue(request.app.state.database).render(project_id, body)


@router.post("/projects/{project_id}/media", status_code=202)
def generate_media(project_id: UUID, body: MediaGenerateRequest, request: Request):
    from app.execution.queue import Queue

    return Queue(request.app.state.database).generate_media(project_id, body)


@router.post("/projects/{project_id}/jobs/{job_id}/remote")
def recover_remote(project_id: UUID, job_id: UUID, body: RemoteRecoveryRequest, request: Request):
    from app.execution.queue import Queue

    return Queue(request.app.state.database).attach_remote(project_id, job_id, body)


@router.get("/projects/{project_id}/jobs")
def jobs(project_id: UUID, request: Request):
    from app.execution.queue import serialize

    service = engine(request)
    with service.database.engine.connect() as connection:
        service.load(connection, project_id)
        return [
            serialize(row)
            for row in connection.execute(
                select(db.jobs).where(db.jobs.c.project_id == str(project_id))
            ).mappings()
        ]


@router.get("/projects/{project_id}/jobs/{job_id}")
def job(project_id: UUID, job_id: UUID, request: Request):
    from app.execution.queue import Queue

    queue = Queue(request.app.state.database)
    with queue.database.engine.connect() as connection:
        return queue.get(connection, project_id, job_id)


@router.post("/projects/{project_id}/jobs/{job_id}/cancel")
def cancel_job(project_id: UUID, job_id: UUID, request: Request):
    from app.execution.queue import Queue

    return Queue(request.app.state.database).cancel(project_id, job_id)


@router.post("/projects/{project_id}/jobs/{job_id}/retry")
def retry_job(
    project_id: UUID, job_id: UUID, request: Request, body: JobRetryRequest | None = None
):
    from app.execution.queue import Queue

    return Queue(request.app.state.database).retry(project_id, job_id, body)


@router.get("/events")
def events(project_id: UUID, request: Request, after: int = 0):
    service = engine(request)
    with service.database.engine.connect() as connection:
        service.load(connection, project_id)
        return [
            dict(row)
            for row in connection.execute(
                select(db.events)
                .where(
                    db.events.c.project_id == str(project_id),
                    db.events.c.event_id > after,
                )
                .order_by(db.events.c.event_id)
                .limit(200)
            ).mappings()
        ]


@router.get("/projects/{project_id}/assets")
@router.get("/projects/{project_id}/manifest")
def assets(project_id: UUID, request: Request):
    apr = engine(request).read(project_id)
    return {
        "project_id": str(project_id),
        "revision": apr["revision"],
        "assets": apr["assets"],
        "render_asset_id": apr["render_asset_id"],
        "render_status": apr["render_status"],
    }


@router.get("/projects/{project_id}/assets/{asset_id}/file")
def asset_file(project_id: UUID, asset_id: UUID, request: Request, download: bool = False):
    from fastapi.responses import FileResponse

    from app.storage import asset_path

    path = asset_path(request.app.state.database, project_id, asset_id)
    media_type = {".mp4": "video/mp4", ".png": "image/png", ".mp3": "audio/mpeg"}.get(
        path.suffix, "application/octet-stream"
    )
    return FileResponse(
        path, media_type=media_type, filename=f"{asset_id}{path.suffix}" if download else None
    )
