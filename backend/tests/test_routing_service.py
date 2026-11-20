"""Project-locked routing, reviewable previews and durable submission identities."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app import db
from app.budget import BudgetService, BudgetUpdate
from app.candidate_store import CandidateStore, CandidateUpdate
from app.errors import DomainError
from app.project_engine import ProjectEngine
from app.routing_schema import RoutingPolicy, RoutingRequirements
from app.routing_service import RoutePreviewRequest, RoutingService
from app.schemas import ProjectCreate


def prepare(database, limit="1"):
    engine = ProjectEngine(database)
    apr = engine.create(ProjectCreate(title="DEMO routing", brief="DEMO contract"))
    apr = BudgetService(database).update(
        apr["project_id"], BudgetUpdate(base_revision=apr["revision"], budget_limit=limit)
    )
    store = CandidateStore(database)
    for identity in ("demo-a", "demo-b"):
        store.save(
            identity,
            "llm",
            CandidateUpdate(
                label=identity,
                base_url="https://example.com/v1",
                model="DEMO model",
                enabled=True,
                request_cost="0.75",
            ),
        )
    return apr


def reserve(database, project_id, identity):
    service = RoutingService(database)
    with database.transaction() as connection:
        apr = service.engine.load(connection, project_id)
        config = service.choose(
            connection, apr, "storyboard", provider_id=identity, allow_remote=True
        )
        return service.reserve(connection, apr, "DEMO request " + uuid4().hex, config)


def test_two_routes_share_budget_atomically(dialect_database):
    database = dialect_database
    apr = prepare(database)

    def attempt(identity):
        try:
            return reserve(database, apr["project_id"], identity)
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["demo-a", "demo-b"]))
    assert len([value for value in results if value == "BUDGET_EXCEEDED"]) == 1
    assert BudgetService(database).public(apr["project_id"])["reserved"] == "0.750000"


def test_preview_pins_candidate_versions_scope_and_source_revision(dialect_database):
    database = dialect_database
    apr = prepare(database, "10")
    service = RoutingService(database)
    body = RoutePreviewRequest(
        base_revision=apr["revision"],
        capability="storyboard",
        provider_id="demo-a",
        allow_remote=True,
    )
    preview = service.preview(apr["project_id"], body)
    assert preview["selected_id"] == "demo-a"
    assert preview["source_revision"] == apr["revision"]
    store = CandidateStore(database)
    store.save(
        "demo-a",
        "llm",
        CandidateUpdate(
            label="DEMO changed",
            revision=1,
            base_url="https://example.com/v1",
            model="DEMO changed",
            enabled=True,
            request_cost="0.75",
        ),
    )
    with database.transaction() as connection, pytest.raises(DomainError) as caught:
        service.choose(
            connection,
            apr,
            "storyboard",
            provider_id="demo-a",
            allow_remote=True,
            preview_id=preview["id"],
        )
    assert caught.value.code == "ROUTE_PREVIEW_STALE"


def test_uncertain_submission_preserves_route(dialect_database):
    database = dialect_database
    apr = prepare(database, "10")
    from app.execution.queue import Queue

    queue = Queue(database)
    service = RoutingService(database)
    with database.transaction() as connection:
        config = service.choose(
            connection,
            apr,
            "storyboard",
            provider_id="auto",
            allow_remote=True,
            policy=RoutingPolicy(allowed_ids=["demo-a", "demo-b"], allow_fallback=True),
        )
        job = queue.enqueue(
            connection,
            apr,
            "plan_storyboard",
            {
                "provider_id": "openai-llm",
                "provider_config": config,
                "model_id": config["model"],
                "input": {"request_id": str(uuid4())},
            },
        )
    claimed = queue.claim("DEMO owner")
    assert claimed["id"] == job["id"]
    with database.transaction() as connection:
        current = queue.get(connection, apr["project_id"], job["id"])
        queue.set_state(connection, current, "running")
        current["submission_started"] = True
        connection.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=current))
        BudgetService(database).begin_submission(connection, current["budget_entry_id"], 5)
    with pytest.raises(DomainError) as caught:
        service.fallback(queue, claimed, "DEMO owner", "DEMO unavailable")
    assert caught.value.code == "REMOTE_SUBMISSION_UNCERTAIN"
    with database.engine.connect() as connection:
        pinned = queue.get(connection, apr["project_id"], job["id"])
        assert pinned["request"]["provider_config"]["connection_id"] == "demo-a"
    state = BudgetService(database).public(apr["project_id"])
    assert len(state["entries"]) == 1 and state["reserved"] == "0.750000"


def test_preview_cannot_expand_requirements_or_transfer_projects(dialect_database):
    database = dialect_database
    apr = prepare(database, "10")
    service = RoutingService(database)
    preview = service.preview(
        apr["project_id"],
        RoutePreviewRequest(
            base_revision=apr["revision"],
            capability="storyboard",
            allow_remote=True,
            provider_id="demo-a",
        ),
    )
    with database.transaction() as connection, pytest.raises(DomainError):
        service.choose(
            connection,
            apr,
            "storyboard",
            provider_id="demo-a",
            allow_remote=True,
            preview_id=preview["id"],
            requirements=RoutingRequirements(units=2),
        )
    other = service.engine.create(ProjectCreate(title="DEMO other", brief="DEMO other"))
    with database.transaction() as connection, pytest.raises(DomainError):
        service.choose(
            connection,
            other,
            "storyboard",
            provider_id="demo-a",
            allow_remote=True,
            preview_id=preview["id"],
        )


def test_route_audit_contains_no_connection_secrets(dialect_database):
    database = dialect_database
    apr = prepare(database)
    reserve(database, apr["project_id"], "demo-a")
    with database.engine.connect() as connection:
        entry = connection.execute(select(db.cost_entries.c.payload)).scalar_one()
    assert entry["route"]["selected_id"] == "demo-a"
    assert "base_url" not in str(entry["route"]) and "api_key" not in str(entry["route"])


def test_pre_submission_fallback_uses_approved_scope_and_same_cost_entry(dialect_database):
    from app.execution.queue import Queue

    database = dialect_database
    apr = prepare(database, "10")
    CandidateStore(database).save(
        "demo-b",
        "llm",
        CandidateUpdate(
            revision=1,
            label="DEMO cheaper fallback",
            base_url="https://example.com/v1",
            model="DEMO cheaper model",
            enabled=True,
            request_cost="0.25",
        ),
    )
    queue = Queue(database)
    service = RoutingService(database)
    with database.transaction() as connection:
        config = service.choose(
            connection,
            apr,
            "storyboard",
            provider_id="auto",
            allow_remote=True,
            policy=RoutingPolicy(
                allowed_ids=["demo-a", "demo-b"], preferred_ids=["demo-a"], allow_fallback=True
            ),
        )
        job = queue.enqueue(
            connection,
            apr,
            "plan_storyboard",
            {"provider_id": "openai-llm", "provider_config": config, "model_id": config["model"]},
        )
    claimed = queue.claim("DEMO owner")
    with database.transaction() as connection:
        queue.set_state(connection, claimed, "running")
    fallback = service.fallback(queue, claimed, "DEMO owner", "MODEL_UNAVAILABLE")
    assert fallback["connection_id"] == "demo-b"
    assert claimed["id"] == job["id"]
    assert fallback["route"]["fallback_from"] == "demo-a"
    assert "api_key" not in fallback
    assert claimed["estimated_cost"] == {"status": "estimated", "amount": "0.25", "currency": "USD"}
    assert claimed["actual_cost"]["status"] == "unknown"
    entries = BudgetService(database).public(apr["project_id"])["entries"]
    assert len(entries) == 1 and entries[0]["estimate"] == "0.250000"
    assert entries[0]["route"]["selected_id"] == "demo-b"


def test_fallback_cannot_release_a_persisted_submission_intent(dialect_database):
    from app.execution.queue import Queue

    database = dialect_database
    apr = prepare(database, "10")
    queue = Queue(database)
    service = RoutingService(database)
    with database.transaction() as connection:
        config = service.choose(
            connection,
            apr,
            "storyboard",
            provider_id="auto",
            allow_remote=True,
            policy=RoutingPolicy(allowed_ids=["demo-a", "demo-b"], allow_fallback=True),
        )
        job = queue.enqueue(
            connection,
            apr,
            "plan_storyboard",
            {"provider_id": "openai-llm", "provider_config": config, "model_id": config["model"]},
        )
    claimed = queue.claim("DEMO owner")
    with database.transaction() as connection:
        queue.set_state(connection, claimed, "running")
        BudgetService(database).begin_submission(connection, job["budget_entry_id"], 5)
    with pytest.raises(DomainError) as caught:
        service.fallback(queue, claimed, "DEMO owner", "MODEL_UNAVAILABLE")
    assert caught.value.code == "REMOTE_SUBMISSION_UNCERTAIN"
