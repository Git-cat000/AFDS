"""Durable proposal generation. Publication creates a plan awaiting human approval."""

import copy
from uuid import UUID

from sqlalchemy import insert, select, update

from app import db
from app.budget import BudgetService
from app.continuity import ContinuityService, _remote_semantic
from app.continuity_schema import SemanticEditRequest, SemanticProposal
from app.director import MockDirector
from app.errors import DomainError
from app.execution.queue import Queue, public_job
from app.history_service import owned_header
from app.llm_adapter import propose_story
from app.media import Cancelled
from app.project_engine import ProjectEngine, emit, find
from app.provider_settings import ProviderStore
from app.schemas import DirectorRequest, new_id
from app.skill_schema import add_guidance


class StoryPlanningRequest(DirectorRequest):
    request_id: UUID


class SemanticPlanningRequest(SemanticEditRequest):
    request_id: UUID


class PlanningService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)
        self.queue = Queue(database)

    def enqueue(self, project_id, kind, body):
        submitted = body.model_dump(mode="json")
        key = f"{project_id}:planning:{body.request_id}"
        store = ProviderStore(self.database)
        with self.database.transaction() as connection:
            owned_header(connection, project_id)
            prior = (
                connection.execute(select(db.jobs).where(db.jobs.c.idempotency_key == key))
                .mappings()
                .first()
            )
            if prior:
                from app.execution.queue import serialize

                job = serialize(prior)
                if job["request"]["input"] != submitted or job["operation"] != f"plan_{kind}":
                    raise DomainError(
                        "REQUEST_ID_CONFLICT",
                        "Planning request ID was used for different input",
                        409,
                    )
                return public_job(job)
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            provider_id = (
                (body.provider_id or store.list()["defaults"]["llm"])
                if kind == "storyboard"
                else ("openai-llm" if body.mode == "llm" else "deterministic-fields")
            )
            if provider_id not in {"mock-director", "openai-llm", "deterministic-fields"}:
                raise DomainError("MODEL_UNAVAILABLE", "Unknown planning provider", 422)
            if kind == "semantic":
                shots = [find(apr["shots"], sid) for sid in body.shot_ids]
                if any(shot["status"] == "archived" for shot in shots):
                    raise DomainError("SHOT_ARCHIVED", "Select active shots", 409)
            snapshot = None
            if provider_id == "openai-llm":
                if not body.allow_remote:
                    raise DomainError(
                        "REMOTE_CONSENT_REQUIRED", "Approve sharing and provider charges", 409
                    )
                snapshot = store.snapshot(provider_id, "storyboard")
            context = {
                key: copy.deepcopy(value)
                for key, value in apr.items()
                if key
                not in {
                    "assets",
                    "asset_versions",
                    "budget",
                    "timeline",
                    "render_asset_id",
                    "render_status",
                }
            }
            request = {
                "input": submitted,
                "context": context,
                "provider_id": provider_id,
                "model_id": snapshot["model"] if snapshot else "local-planning",
                "adapter_version": "0.6.0",
            }
            if snapshot:
                request["provider_config"] = snapshot
            add_guidance(
                request,
                apr,
                ["shots", "continuity"] if kind == "semantic" else ["story", "shots", "continuity"],
            )
            job = self.queue.enqueue(connection, apr, f"plan_{kind}", request, idempotency_key=key)
            if snapshot:
                self.engine.approve(
                    connection,
                    project_id,
                    "remote_planning",
                    {
                        "job_id": job["id"],
                        "provider_revision": snapshot["revision"],
                        "kind": kind,
                    },
                )
            return public_job(job)

    def execute(self, job, owner, checkpoint):
        request = job["request"]
        semantic = job["operation"] == "plan_semantic"
        model = SemanticPlanningRequest if semantic else StoryPlanningRequest
        body = model.model_validate(request["input"])
        apr = request["context"]
        config = None
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            live = self.engine.load(connection, job["project_id"])
            self.engine.check_revision(live, body.base_revision)
            if request.get("provider_config"):
                if current.get("submission_started"):
                    raise DomainError(
                        "REMOTE_SUBMISSION_UNCERTAIN",
                        "Check provider usage and approve a new request before resubmitting",
                        409,
                    )
                store = ProviderStore(self.database)
                if not store.runtime("llm")["enabled"]:
                    raise DomainError("MODEL_UNAVAILABLE", "Provider was disabled", 422)
                config = store.runtime("llm", request["provider_config"]["revision"])
                budget = BudgetService(self.database)
                budget.enforce(live, config)
                budget.begin_submission(
                    connection, current["budget_entry_id"], config["timeout_seconds"]
                )
                current["submission_started"] = True
                payload = {
                    key: value
                    for key, value in current.items()
                    if key
                    not in {
                        "id",
                        "project_id",
                        "state",
                        "attempt",
                        "lease_owner",
                        "lease_expires_at",
                        "available_at",
                    }
                }
                connection.execute(
                    update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
                )
                job["submission_started"] = True
        if not checkpoint():
            raise Cancelled()
        try:
            if not semantic:
                plan = (
                    propose_story(config, apr, body.message, checkpoint=checkpoint)
                    if config
                    else MockDirector().propose(apr, body.message)
                )
            else:
                shots = [find(apr["shots"], sid) for sid in body.shot_ids]
                proposal = (
                    _remote_semantic(config, apr, shots, body.message, checkpoint=checkpoint)
                    if config
                    else SemanticProposal.model_validate(
                        {
                            "edits": [
                                {
                                    "shot_id": shot["id"],
                                    "fields": body.fields.model_dump(exclude_none=True),
                                }
                                for shot in shots
                            ]
                        }
                    )
                )
                edits = proposal.model_dump(mode="json", exclude_none=True)["edits"]
                returned = [edit["shot_id"] for edit in edits]
                if len(set(returned)) != len(returned) or not set(returned) <= {
                    shot["id"] for shot in shots
                }:
                    raise DomainError(
                        "PROVIDER_OUTPUT_INVALID", "Edit proposal escaped selected shots", 422
                    )
                plan = {
                    "id": new_id(),
                    "type": "semantic_edit",
                    "state": "proposed",
                    "base_revision": apr["revision"],
                    "provider_id": job["provider_id"],
                    "message": body.message,
                    "context_shot_ids": [shot["id"] for shot in shots],
                    "edits": edits,
                    "explanation": "请审阅修改差异后批准应用。",
                }
            plan["planning_job_id"] = job["id"]
            if request.get("production_guidance"):
                plan["production_guidance"] = request["production_guidance"]
            return plan
        except DomainError as error:
            if config and getattr(error, "rejected", False):
                with self.database.transaction() as connection:
                    current = self.queue.check_owned(connection, job, owner)
                    payload = dict(current)
                    for key in (
                        "id",
                        "project_id",
                        "state",
                        "attempt",
                        "lease_owner",
                        "lease_expires_at",
                        "available_at",
                    ):
                        payload.pop(key, None)
                    payload["submission_started"] = False
                    connection.execute(
                        update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
                    )
                    job["submission_started"] = False
            raise

    def publish(self, job, owner, plan, elapsed_ms):
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            apr = self.engine.load(connection, job["project_id"])
            self.engine.check_revision(apr, plan["base_revision"])
            if plan.get("type") == "semantic_edit":
                plan.update(
                    ContinuityService(self.database)._semantic_summary(
                        copy.deepcopy(apr), plan["edits"]
                    )
                )
            connection.execute(
                insert(db.plans).values(id=plan["id"], project_id=job["project_id"], payload=plan)
            )
            emit(connection, job["project_id"], "plan.proposed", {"plan_id": plan["id"]}, job["id"])
            self.queue.set_state(connection, current, "verifying", progress=90)
            self.queue.set_state(
                connection,
                current,
                "completed",
                progress=100,
                result_plan_id=plan["id"],
                elapsed_ms=elapsed_ms,
            )
