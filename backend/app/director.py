import copy

from sqlalchemy import insert, select, update

from app import db
from app.errors import DomainError
from app.project_engine import rebuild_timeline
from app.schemas import Scene, Shot, new_id


class MockDirector:
    """Deterministic four-beat proposal, with no network or database access."""

    def propose(self, apr, message):
        scene = Scene(
            order=len(apr["scenes"]), title="四镜头练习", description=f"{apr['brief']}\n{message}"
        ).model_dump(mode="json")
        beats = [
            ("建立环境", "wide", "展示故事发生的环境与主角的位置。"),
            ("人物进入", "medium", "主角进入画面，停下脚步。"),
            ("情绪转折", "close_up", "主角望向远处，流露告别时的情绪。"),
            ("余韵", "wide", "主角离开，镜头停留在空下来的环境。"),
        ]
        duration = round(5 * apr["format"]["fps_num"] / apr["format"]["fps_den"])
        shots = [
            Shot(
                scene_id=scene["id"],
                order=len(apr["shots"]) + index,
                title=title,
                action=f"{apr['brief'][:500]} — {action}",
                camera={"shot_size": size},
                duration_frames=duration,
                status="planned",
            ).model_dump(mode="json")
            for index, (title, size, action) in enumerate(beats)
        ]
        scene["shot_ids"] = [s["id"] for s in shots]
        return {
            "id": new_id(),
            "provider_id": "mock-director",
            "state": "proposed",
            "base_revision": apr["revision"],
            "message": message,
            "scene": scene,
            "shots": shots,
            "estimated_cost": {"status": "known", "amount": 0, "currency": "USD"},
            "explanation": "DEMO/MOCK · 规则式四镜头建议，请审阅后执行。",
        }


def propose(engine, project_id, body):
    from app.llm_adapter import propose_story
    from app.provider_settings import ProviderStore

    store = ProviderStore(engine.database)
    provider_id = body.provider_id or store.list()["defaults"]["llm"]
    if provider_id not in {"mock-director", "openai-llm"}:
        raise DomainError("MODEL_UNAVAILABLE", "Unknown director provider", 422)
    config = None
    if provider_id == "openai-llm":
        if not body.allow_remote:
            raise DomainError(
                "REMOTE_CONSENT_REQUIRED",
                "Approve sharing this brief and unknown provider costs",
                409,
            )
        snapshot = store.snapshot(provider_id, "storyboard")
        config = store.runtime("llm", snapshot["revision"])
    with engine.database.transaction() as connection:
        apr = engine.load(connection, project_id)
        engine.check_revision(apr, body.base_revision)
        if config:
            engine.approve(
                connection,
                project_id,
                "remote_llm",
                {
                    "provider_id": provider_id,
                    "revision": config["revision"],
                    "cost_status": "unknown",
                },
            )
    plan = (
        propose_story(config, apr, body.message)
        if config
        else MockDirector().propose(apr, body.message)
    )
    with engine.database.transaction() as connection:
        engine.check_revision(engine.load(connection, project_id), body.base_revision)
        connection.execute(
            insert(db.plans).values(id=plan["id"], project_id=str(project_id), payload=plan)
        )
        return plan


def execute(engine, project_id, plan_id, body):
    if not body.approved:
        raise DomainError("APPROVAL_REQUIRED", "Approve the storyboard proposal first", 409)

    def apply(apr, connection):
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
        plan = copy.deepcopy(row["payload"])
        engine.check_revision(apr, plan["base_revision"])
        if plan["state"] != "proposed":
            raise DomainError("PLAN_EXECUTED", "This plan has already been executed", 409)
        apr["scenes"].append(plan["scene"])
        apr["shots"].extend(plan["shots"])
        ids = [i["shot_id"] for i in apr["timeline"]["tracks"][0]["items"]]
        rebuild_timeline(apr, ids + [s["id"] for s in plan["shots"]])
        apr["render_status"] = "stale"
        plan["state"] = "executed"
        connection.execute(
            update(db.plans).where(db.plans.c.id == str(plan_id)).values(payload=plan)
        )
        engine.approve(connection, project_id, "storyboard", {"plan_id": str(plan_id)})

    return engine.mutate(project_id, body.base_revision, "Execute approved Mock storyboard", apply)
