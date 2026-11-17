import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from conftest import project
from test_skill_execution import execute_script, script_preview
from test_skills import package_files

from app import db, skill_sandbox
from app.budget import BudgetService
from app.errors import DomainError
from app.execution.queue import Queue
from app.history_service import HistoryService
from app.operations_service import pulse_worker
from app.project_engine import ProjectEngine
from app.provider_settings import ProviderStore, ProviderUpdate
from app.schemas import ProjectCreate
from app.skill_execution import SkillExecution
from app.skill_packages import SkillLibrary
from app.skill_schema import ProductionCheckRequest, SkillFile
from app.workspace_service import read_workspace


def make_project(database):
    return ProjectEngine(database).create(ProjectCreate(title="DEMO concurrency", brief="DEMO"))


def concurrently(operation):
    barrier = threading.Barrier(2)

    def run(index):
        barrier.wait(timeout=5)
        try:
            return operation(index)
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, range(2)))


def test_two_mutations_commit_one_revision(dialect_database):
    engine = ProjectEngine(dialect_database)
    apr = make_project(dialect_database)

    def mutate(index):
        def change(after, connection):
            time.sleep(0.05)
            after["title"] = f"DEMO author {index}"

        return engine.mutate(apr["project_id"], apr["revision"], "DEMO concurrent edit", change)[
            "revision"
        ]

    outcomes = concurrently(mutate)
    assert sorted(map(str, outcomes)) == [str(apr["revision"] + 1), "REVISION_CONFLICT"]
    assert engine.read(apr["project_id"])["revision"] == apr["revision"] + 1


def test_two_workers_claim_once(dialect_database):
    apr = make_project(dialect_database)
    job = SkillExecution(dialect_database).enqueue_check(
        apr["project_id"], ProductionCheckRequest(base_revision=apr["revision"], stage="story")
    )
    outcomes = concurrently(lambda index: Queue(dialect_database).claim(f"DEMO-worker-{index}"))
    claimed = [value for value in outcomes if value is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == job["id"] and claimed[0]["attempt"] == 1


def test_concurrent_budget_reservations_respect_limit(dialect_database):
    engine = ProjectEngine(dialect_database)
    apr = make_project(dialect_database)
    engine.mutate(
        apr["project_id"],
        apr["revision"],
        "DEMO budget",
        lambda after, connection: after["budget"].update(budget_limit=1, allow_unknown_cost=False),
    )

    def reserve(index):
        with dialect_database.transaction() as connection:
            current = engine.load(connection, apr["project_id"])
            return BudgetService(dialect_database).reserve(
                connection,
                current,
                f"DEMO:{index}",
                {"provider_id": "DEMO-provider", "request_cost": 0.75, "cost_currency": "USD"},
            )

    outcomes = concurrently(reserve)
    assert outcomes.count("BUDGET_EXCEEDED") == 1
    summary = BudgetService(dialect_database).public(apr["project_id"])
    assert summary["reserved"] == "0.750000" and len(summary["entries"]) == 1


def test_failed_transaction_rolls_back_budget_and_events(dialect_database):
    engine = ProjectEngine(dialect_database)
    apr = make_project(dialect_database)
    engine.mutate(
        apr["project_id"],
        apr["revision"],
        "DEMO budget",
        lambda after, connection: after["budget"].update(budget_limit=1),
    )
    with pytest.raises(RuntimeError):
        with dialect_database.transaction() as connection:
            BudgetService(dialect_database).reserve(
                connection,
                engine.load(connection, apr["project_id"]),
                "DEMO-rollback",
                {"provider_id": "DEMO-provider", "request_cost": 0.75},
            )
            raise RuntimeError("DEMO abort")
    assert BudgetService(dialect_database).public(apr["project_id"])["entries"] == []


def test_read_snapshot_keeps_project_and_workspace_consistent(dialect_database):
    engine = ProjectEngine(dialect_database)
    apr = make_project(dialect_database)

    def edit():
        changed = engine.mutate(
            apr["project_id"],
            apr["revision"],
            "DEMO snapshot writer",
            lambda after, connection: after.update(title="DEMO new title"),
        )
        engine.mutate_workspace(
            apr["project_id"],
            0,
            lambda document, current, connection: {
                **document,
                "nodes": [{"id": "note-snapshot", "kind": "note", "text": "DEMO"}],
                "positions": {"note-snapshot": {"x": 0, "y": 0}},
            },
        )
        return changed

    with ThreadPoolExecutor(max_workers=1) as pool:
        with dialect_database.transaction(snapshot=True) as connection:
            original = engine.load(connection, apr["project_id"])
            future = pool.submit(edit)
            future.result(timeout=5)
            assert engine.load(connection, apr["project_id"])["title"] == original["title"]
            assert read_workspace(connection, apr["project_id"])["workspace_revision"] == 0
        assert future.result(timeout=5)["title"] == "DEMO new title"


def test_json_history_skill_filter_and_worker_upsert_are_portable(dialect_database):
    apr = make_project(dialect_database)
    imported = SkillLibrary(dialect_database).import_files(
        [SkillFile.model_validate(value) for value in package_files()], ["shots"]
    )
    assert any(
        item["id"] == imported["id"]
        for item in SkillLibrary(dialect_database).list("shots")["items"]
    )
    job = SkillExecution(dialect_database).enqueue_check(
        apr["project_id"], ProductionCheckRequest(base_revision=apr["revision"], stage="story")
    )
    assert (
        HistoryService(dialect_database).jobs(apr["project_id"], 10)["items"][0]["id"] == job["id"]
    )
    with dialect_database.transaction() as connection:
        pulse_worker(connection, "DEMO-node", 1)
        pulse_worker(connection, "DEMO-node", 2)


def test_concurrent_initial_provider_settings_return_one_revision(dialect_database):
    def save(index):
        return ProviderStore(dialect_database).save(
            "llm",
            ProviderUpdate(
                revision=0, base_url="https://example.com/v1", model=f"DEMO-{index}", enabled=True
            ),
        )["revision"]

    outcomes = concurrently(save)
    assert sorted(map(str, outcomes)) == ["1", "CONFIG_CONFLICT"]


def test_concurrent_skill_imports_merge_stage_classification(dialect_database):
    files = [SkillFile.model_validate(value) for value in package_files()]
    outcomes = concurrently(
        lambda index: SkillLibrary(dialect_database).import_files(
            files, [["story"], ["shots"]][index]
        )
    )
    assert outcomes[0]["id"] == outcomes[1]["id"]
    assert set(SkillLibrary(dialect_database).get(outcomes[0]["id"])["stages"]) == {
        "story",
        "shots",
    }


def test_container_policy_preflight_runs_outside_write_transaction(client, monkeypatch):
    database = client.app.state.database
    transaction = database.transaction
    writing = []

    @contextmanager
    def observed_transaction(**options):
        with transaction(**options) as connection:
            writing.append(not options.get("snapshot", False))
            try:
                yield connection
            finally:
                writing.pop()

    def policy(settings, timeout):
        assert not any(writing), "Docker preflight held a project write transaction"
        return skill_sandbox.ScriptExecutionPolicy(
            image_id="sha256:" + "a" * 64, timeout_seconds=timeout
        )

    monkeypatch.setattr(database, "transaction", observed_transaction)
    monkeypatch.setattr(skill_sandbox, "execution_policy", policy)
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    assert execute_script(client, apr, plan).status_code == 202


def test_postgres_claim_skips_a_project_locked_by_another_writer(dialect_database):
    if dialect_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-level SKIP LOCKED semantics")
    first, second = make_project(dialect_database), make_project(dialect_database)
    service = SkillExecution(dialect_database)
    for apr in (first, second):
        service.enqueue_check(
            apr["project_id"], ProductionCheckRequest(base_revision=apr["revision"], stage="story")
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with dialect_database.transaction() as connection:
            db.lock_project(connection, first["project_id"])
            claimed = pool.submit(Queue(dialect_database).claim, "DEMO-unblocked-worker").result(
                timeout=3
            )
            assert claimed["project_id"] == second["project_id"]


def test_micro_unit_projection_retains_values_larger_than_int32(dialect_database):
    engine = ProjectEngine(dialect_database)
    apr = make_project(dialect_database)
    apr = engine.mutate(
        apr["project_id"],
        apr["revision"],
        "DEMO exact budget",
        lambda after, connection: after["budget"].update(budget_limit=10_000_000),
    )
    with dialect_database.transaction() as connection:
        BudgetService(dialect_database).reserve(
            connection,
            engine.load(connection, apr["project_id"]),
            "DEMO-large-micro-units",
            {"provider_id": "DEMO-provider", "request_cost": "5000000.000001"},
        )
    assert engine.read(apr["project_id"])["budget"]["reserved"] == "5000000.000001"
    assert (
        HistoryService(dialect_database).entries(apr["project_id"], 10)["items"][0]["estimate"]
        == "5000000.000001"
    )
