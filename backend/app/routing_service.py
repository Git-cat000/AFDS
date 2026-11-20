"""Project-locked routing with immutable previews and pre-submission fallback."""

import copy
import json
import time
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field
from sqlalchemy import JSON, Column, Float, ForeignKey, String, Table, insert, select, update

from app import db
from app.budget import BudgetService
from app.candidate_store import CandidateStore, candidates
from app.errors import DomainError
from app.identity_context import actor_id
from app.project_engine import ProjectEngine
from app.provider_settings import CAPABILITIES, ProviderStore
from app.routing_schema import Capability, ProviderCandidate, RoutingPolicy, RoutingRequirements
from app.routing_selection import rank_candidates
from app.schemas import RevisionRequest, new_id

route_decisions = Table(
    "route_decisions",
    db.metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("created_at", Float, nullable=False),
    Column("payload", JSON, nullable=False),
)
MOCKS = {
    "mock-director": ("llm", "storyboard", "mock-director"),
    "mock-video-local": ("video", "text_to_video", "ffmpeg-test-pattern"),
}


class RoutePreviewRequest(RevisionRequest):
    capability: Capability
    provider_id: str | None = Field(default=None, max_length=100)
    allow_remote: bool = False
    requirements: RoutingRequirements = Field(default_factory=RoutingRequirements)


class RoutingPolicyUpdate(RevisionRequest):
    policies: dict[Capability, RoutingPolicy] = Field(max_length=6)


class RoutingService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)
        self.store = CandidateStore(database)
        self.budget = BudgetService(database)

    def direct_request(self, connection, apr, operation, body):
        identity = str(body.request_id or new_id())
        source_actor = actor_id(self.database.settings)
        reference = f"{operation}:{apr['project_id']}:{source_actor}:{identity}"
        prior = connection.execute(
            select(db.plans.c.payload).where(
                db.plans.c.project_id == str(apr["project_id"]),
                db.plans.c.payload["request_id"].as_string() == identity,
                db.plans.c.payload["actor_id"].as_string() == source_actor,
            )
        ).scalar_one_or_none()
        if prior:
            if prior.get("input") != body.model_dump(mode="json"):
                raise DomainError(
                    "REQUEST_ID_CONFLICT", "Request ID was used for different input", 409
                )
            return identity, reference, prior
        previous = connection.execute(
            select(db.cost_entries.c.payload).where(db.cost_entries.c.reference == reference)
        ).scalar_one_or_none()
        if previous and previous["state"] != "released":
            raise DomainError(
                "REMOTE_SUBMISSION_UNCERTAIN", "Reconcile this request before submitting again", 409
            )
        return identity, reference, None

    def policy(self, apr, capability):
        return RoutingPolicy.model_validate(apr.get("routing_policies", {}).get(capability, {}))

    def update_policy(self, project_id, body):
        def apply(apr, connection):
            apr["routing_policies"] = {
                key: value.model_dump(mode="json") for key, value in body.policies.items()
            }
            self.engine.approve(
                connection, project_id, "model_routing_policy", apr["routing_policies"]
            )

        return self.engine.mutate(
            project_id, body.base_revision, "Update model routing policy", apply
        )

    def inventory(self, project_id):
        result = self.store.candidates(project_id)
        from app.adapters import check_provider

        for identity, (kind, capability, model) in MOCKS.items():
            try:
                check_provider(self.database.settings, identity, capability)
                enabled = True
            except DomainError:
                enabled = False
            result.append(
                ProviderCandidate(
                    id=identity,
                    label="DEMO/MOCK " + kind,
                    kind=kind,
                    config_revision=1,
                    model=model,
                    capabilities=[capability],
                    enabled=enabled,
                    estimated_cost=0,
                    is_mock=True,
                )
            )
        return result

    def evaluate(
        self,
        connection,
        apr,
        capability,
        *,
        provider_id,
        allow_remote,
        requirements,
        policy=None,
        check_remaining_budget=True,
    ):
        if capability not in {*CAPABILITIES.values(), "visual_review"}:
            raise DomainError("CAPABILITY_UNSUPPORTED", "Unknown model capability", 422)
        policy = policy or self.policy(apr, capability)
        requested = provider_id
        if requested is None:
            if capability in apr.get("routing_policies", {}):
                requested = "auto"
            else:
                kind = next(
                    (kind for kind, value in CAPABILITIES.items() if value == capability), "llm"
                )
                requested = ProviderStore(self.database).list()["defaults"][kind]
                if capability == "visual_review":
                    requested = "openai-llm"
        if requested.startswith("openai-"):
            requested = "default-" + requested.removeprefix("openai-")
        values = self.inventory(apr["project_id"])
        if requested != "auto":
            values = [value for value in values if value.id == requested]
            if not values:
                raise DomainError("MODEL_UNAVAILABLE", "Model connection is unavailable", 422)
            if capability not in values[0].capabilities:
                raise DomainError(
                    "CAPABILITY_UNSUPPORTED", "Connection capability does not match", 422
                )
            if not values[0].is_mock and not allow_remote:
                raise DomainError(
                    "REMOTE_CONSENT_REQUIRED", "Approve content sharing and provider charges", 409
                )
        try:
            result = rank_candidates(
                values,
                capability=capability,
                policy=policy,
                project_id=apr["project_id"],
                budget=self.budget.summary(connection, apr, include_entries=False),
                allow_remote=allow_remote,
                requirements=requirements,
                check_remaining_budget=check_remaining_budget or requested == "auto",
            )
        except DomainError as error:
            if requested != "auto" and error.code == "NO_ELIGIBLE_CANDIDATE":
                reasons = error.details.get("candidates", [{}])[0].get("rejections", [])
                for code in reasons:
                    if code in {
                        "BUDGET_EXCEEDED",
                        "UNKNOWN_COST_BLOCKED",
                        "PROJECT_REMOTE_BLOCKED",
                        "BUDGET_CURRENCY_MISMATCH",
                        "REFERENCE_ROLE_UNSUPPORTED",
                    }:
                        reference = code == "REFERENCE_ROLE_UNSUPPORTED"
                        raise DomainError(
                            "REFERENCE_INPUT_UNSUPPORTED" if reference else code,
                            "Selected connection does not meet request requirements",
                            422 if reference else 409,
                            error.details,
                        ) from error
            raise
        payload = {
            **result.model_dump(mode="json"),
            "capability": capability,
            "source_revision": apr["revision"],
            "policy": policy.model_dump(mode="json"),
            "requirements": requirements.model_dump(mode="json"),
            "requested_id": requested,
            "versions": {
                value.id: value.config_revision
                for value in values
                if value.id in result.eligible_ids
            },
            "allow_remote": allow_remote,
        }
        payload["id"] = str(
            uuid5(NAMESPACE_URL, str(apr["project_id"]) + json.dumps(payload, sort_keys=True))
        )
        return payload

    def preview(self, project_id, body):
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            db.lock_named_resource(connection, "provider-store")
            payload = self.evaluate(
                connection,
                apr,
                body.capability,
                provider_id=body.provider_id,
                allow_remote=body.allow_remote,
                requirements=body.requirements,
            )
            payload.update(
                id=new_id(), actor_id=actor_id(self.database.settings), expires_at=time.time() + 600
            )
            connection.execute(
                insert(route_decisions).values(
                    id=payload["id"],
                    project_id=str(project_id),
                    created_at=time.time(),
                    payload=payload,
                )
            )
            return payload

    def choose(
        self,
        connection,
        apr,
        capability,
        *,
        provider_id=None,
        allow_remote=False,
        requirements=None,
        preview_id=None,
        policy=None,
        check_remaining_budget=True,
    ):
        db.lock_project(connection, apr["project_id"])
        db.lock_named_resource(connection, "provider-store")
        requirements = requirements or RoutingRequirements()
        if preview_id:
            payload = connection.execute(
                select(route_decisions.c.payload).where(
                    route_decisions.c.id == str(preview_id),
                    route_decisions.c.project_id == str(apr["project_id"]),
                )
            ).scalar_one_or_none()
            if (
                not payload
                or payload["actor_id"] != actor_id(self.database.settings)
                or payload["expires_at"] < time.time()
                or payload["capability"] != capability
                or payload["source_revision"] != apr["revision"]
            ):
                raise DomainError("ROUTE_PREVIEW_STALE", "Refresh this route preview", 409)
            expected = RoutingRequirements.model_validate(payload["requirements"])
            if requirements != expected or allow_remote != payload["allow_remote"]:
                raise DomainError("ROUTE_PREVIEW_STALE", "Request scope changed after preview", 409)
            live = {value.id: value.config_revision for value in self.inventory(apr["project_id"])}
            if any(
                live.get(identity) != revision for identity, revision in payload["versions"].items()
            ):
                raise DomainError(
                    "ROUTE_PREVIEW_STALE", "Connection versions changed after preview", 409
                )
            policy = RoutingPolicy.model_validate(payload["policy"])
            policy.allowed_ids = payload["eligible_ids"]
            chosen = self.evaluate(
                connection,
                apr,
                capability,
                provider_id=provider_id,
                allow_remote=allow_remote,
                requirements=requirements,
                policy=policy,
            )
            if chosen["selected_id"] != payload["selected_id"]:
                raise DomainError(
                    "ROUTE_PREVIEW_STALE", "Model selection changed after preview", 409
                )
            decision = {**chosen, "id": payload["id"]}
        else:
            decision = self.evaluate(
                connection,
                apr,
                capability,
                provider_id=provider_id,
                allow_remote=allow_remote,
                requirements=requirements,
                policy=policy,
                check_remaining_budget=check_remaining_budget,
            )
        identity = decision["selected_id"]
        if identity in MOCKS:
            kind, _, model = MOCKS[identity]
            return {
                "provider_id": identity,
                "kind": kind,
                "model": model,
                "is_mock": True,
                "route": decision,
            }
        config = self.store.snapshot(identity, capability)
        if config["revision"] != decision["versions"][identity]:
            raise DomainError(
                "ROUTE_PREVIEW_STALE", "Connection version differs from the approved route", 409
            )
        config["route"] = decision
        return config

    def reserve(self, connection, apr, reference, config):
        if config.get("is_mock"):
            return None
        route = config.get("route")
        if route:
            db.lock_named_resource(connection, "provider-store")
            current = connection.execute(
                select(candidates.c.revision).where(candidates.c.id == config["connection_id"])
            ).scalar_one_or_none()
            if (
                current != config["revision"]
                or route["versions"].get(config["connection_id"]) != config["revision"]
            ):
                raise DomainError(
                    "ROUTE_PREVIEW_STALE", "Connection changed before reservation", 409
                )
        entry_id = self.budget.reserve(connection, apr, reference, config)
        if route:
            payload = connection.execute(
                select(db.cost_entries.c.payload).where(db.cost_entries.c.id == entry_id)
            ).scalar_one()
            if "route" not in payload:
                payload = {**payload, "route": {**route, "budget_entry_id": entry_id}}
                connection.execute(
                    update(db.cost_entries)
                    .where(db.cost_entries.c.id == entry_id)
                    .values(payload=payload)
                )
        return entry_id

    def runtime(self, snapshot, project_id, *, capability=None):
        identity = snapshot.get("connection_id")
        if not identity:
            store = ProviderStore(self.database)
            current = store.runtime(snapshot["kind"])
            if not current["enabled"] or (
                capability == "visual_review" and not current.get("vision_enabled")
            ):
                raise DomainError("MODEL_UNAVAILABLE", "Provider was disabled", 422)
            return store.runtime(snapshot["kind"], snapshot["revision"])
        current = self.store.profile(identity)
        descriptor = self.store.descriptor({**current, "id": identity})
        if (
            not descriptor.enabled
            or descriptor.health == "unhealthy"
            or (
                not descriptor.all_projects
                and str(project_id) not in map(str, descriptor.project_ids)
            )
        ):
            raise DomainError(
                "MODEL_UNAVAILABLE", "Connection is disabled or no longer authorized", 422
            )
        if capability and capability not in descriptor.capabilities:
            raise DomainError("MODEL_UNAVAILABLE", "Connection capability was disabled", 422)
        config = self.store.runtime(identity, snapshot["revision"])
        return {**config, "route": snapshot.get("route")}

    def fallback(self, queue, job, owner, reason):
        with self.database.transaction() as connection:
            current = queue.check_owned(connection, job, owner)
            entry = connection.execute(
                select(db.cost_entries.c.payload).where(
                    db.cost_entries.c.id == current.get("budget_entry_id")
                )
            ).scalar_one_or_none()
            if (
                current.get("submission_started")
                or current.get("external_task_id")
                or (entry and entry["state"] == "pending")
            ):
                raise DomainError(
                    "REMOTE_SUBMISSION_UNCERTAIN",
                    "Keep the submitted route for reconciliation",
                    409,
                )
            config = current["request"]["provider_config"]
            route = config.get("route") or {}
            if not route.get("policy", {}).get("allow_fallback"):
                raise DomainError(
                    "ROUTE_FALLBACK_DISABLED", "This request has no approved fallback", 409
                )
            apr = self.engine.load(connection, job["project_id"])
            identities = [
                identity for identity in route["eligible_ids"] if identity != route["selected_id"]
            ]
            for identity in identities:
                if identity in MOCKS:
                    kind, _, model = MOCKS[identity]
                    candidate = {
                        "kind": kind,
                        "model": model,
                        "provider_id": identity,
                        "is_mock": True,
                        "request_cost": "0",
                    }
                else:
                    try:
                        candidate = self.store.runtime(identity, route["versions"][identity])
                        self.runtime(candidate, apr["project_id"], capability=route["capability"])
                    except DomainError:
                        continue
                if candidate.get("request_cost") is None and config.get("request_cost") is not None:
                    continue
                if (
                    candidate.get("request_cost") is not None
                    and config.get("request_cost") is not None
                ):
                    from decimal import Decimal

                    if Decimal(str(candidate["request_cost"])) > Decimal(
                        str(config["request_cost"])
                    ):
                        continue
                next_route = {
                    **route,
                    "selected_id": identity,
                    "fallback_from": route["selected_id"],
                    "fallback_reason": reason,
                }
                candidate = {key: value for key, value in candidate.items() if key != "api_key"}
                candidate["route"] = next_route
                self.budget.finalize(connection, current["budget_entry_id"], False)
                entry_id = self.reserve(connection, apr, f"job:{job['id']}", candidate)
                request = copy.deepcopy(current["request"])
                request.update(
                    provider_config=candidate,
                    model_id=candidate["model"],
                    provider_id=candidate["provider_id"],
                )
                if candidate.get("is_mock"):
                    request.pop("provider_config")
                    request["route"] = next_route
                from app.execution.queue import request_digest

                unknown = {"status": "unknown", "amount": None, "currency": None}
                estimated = (
                    {"status": "known", "amount": 0, "currency": "USD"}
                    if candidate.get("is_mock")
                    else unknown
                )
                if not candidate.get("is_mock") and candidate.get("request_cost") is not None:
                    estimated = {
                        "status": "estimated",
                        "amount": candidate["request_cost"],
                        "currency": candidate.get("cost_currency", "USD"),
                    }
                current.update(
                    request=request,
                    model_id=candidate["model"],
                    provider_id=candidate["provider_id"],
                    budget_entry_id=entry_id,
                    estimated_cost=estimated,
                    actual_cost=estimated if candidate.get("is_mock") else unknown,
                    fingerprint=request_digest(request),
                    input_summary={
                        **current["input_summary"],
                        "request_hash": request_digest(request),
                    },
                )
                connection.execute(
                    update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=current)
                )
                job.update(current)
                return candidate
            raise DomainError(
                "NO_ELIGIBLE_FALLBACK",
                "Approved fallback connections are unavailable within the cost bound",
                409,
            )

    def prepare_job(self, queue, job, owner):
        snapshot = job["request"]["provider_config"]
        if job.get("submission_started") or job.get("external_task_id"):
            return
        try:
            self.runtime(snapshot, job["project_id"], capability=snapshot["route"]["capability"])
        except DomainError as error:
            if not snapshot["route"]["policy"].get("allow_fallback"):
                raise
            self.fallback(queue, job, owner, error.code)
