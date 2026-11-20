import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, update

from app import db, skill_sandbox
from app.config import Settings
from app.db import Database
from app.execution.queue import Queue
from app.media import Cancelled
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate
from app.skill_execution import SkillExecution
from app.skill_schema import ProductionCheckRequest
from app.worker_registry import WorkerRegistry, eligible


def check_job(database):
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO worker registry"))
    return SkillExecution(database).enqueue_check(
        apr["project_id"], ProductionCheckRequest(base_revision=apr["revision"], stage="story")
    )


def register(database, identity="demo-node", operations=None, slots=1):
    registry = WorkerRegistry(database)
    registry.initialize_store()
    return registry.register(
        {
            "id": identity,
            "label": "DEMO worker",
            "operations": operations or ["check_production"],
            "slots": slots,
        }
    )


def test_worker_capability_filters_claim(dialect_database):
    job = check_job(dialect_database)
    wrong = register(dialect_database, "demo-media", ["render_timeline"])
    assert Queue(dialect_database, node=wrong).claim("demo-media-slot") is None
    node = register(dialect_database, "demo-checks")
    claimed = Queue(dialect_database, node=node).claim("demo-check-slot")
    assert claimed["id"] == job["id"]
    assert claimed["worker_node_id"] == node["id"]
    assert eligible(node, claimed)


def test_missing_shared_storage_blocks_claim(dialect_database, tmp_path):
    job = check_job(dialect_database)
    register(dialect_database, "demo-original")
    other_settings = Settings(
        data_dir=tmp_path / "wrong-mount", database_url=dialect_database.settings.database_url
    )
    other = Database(other_settings)
    try:
        node = WorkerRegistry(other).register(
            {
                "id": "demo-wrong-mount",
                "label": "DEMO missing shared store",
                "operations": ["check_production"],
                "slots": 1,
            }
        )
        assert node["storage"]["status"] == "unavailable"
        assert Queue(other, node=node).claim("demo-missing-slot") is None
        with dialect_database.engine.connect() as connection:
            assert (
                Queue(dialect_database).get(connection, job["project_id"], job["id"])["state"]
                == "queued"
            )
    finally:
        other.engine.dispose()


def test_stale_worker_cannot_publish(dialect_database):
    node = register(dialect_database)
    job = check_job(dialect_database)
    queue = Queue(dialect_database, node=node)
    claimed = queue.claim("demo-old-slot")
    with dialect_database.transaction() as connection:
        current = queue.get(connection, job["project_id"], job["id"])
        queue.set_state(connection, current, "running")
    replacement = WorkerRegistry(dialect_database).register(
        {
            "id": node["id"],
            "label": "DEMO restarted worker",
            "operations": ["check_production"],
            "slots": 1,
        }
    )
    assert replacement["generation"] != node["generation"]
    assert not queue.heartbeat(job["id"], "demo-old-slot")
    with dialect_database.transaction() as connection, pytest.raises(Cancelled):
        queue.check_owned(connection, claimed, "demo-old-slot")


def test_drain_stops_claims_and_allows_inflight_completion(dialect_database):
    node = register(dialect_database)
    job = check_job(dialect_database)
    queue = Queue(dialect_database, node=node)
    queue.claim("demo-draining-slot")
    WorkerRegistry(dialect_database).drain(node["id"], True)
    assert queue.heartbeat(job["id"], "demo-draining-slot")
    assert queue.claim("demo-another-slot") is None
    with dialect_database.transaction() as connection:
        current = queue.get(connection, job["project_id"], job["id"])
        queue.set_state(connection, current, "running")
        queue.check_owned(connection, current, "demo-draining-slot")


def test_worker_slots_are_atomic(dialect_database):
    if dialect_database.engine.dialect.name != "postgresql":
        pytest.skip("SQLite retains one globally active task")
    node = register(dialect_database, slots=2)
    for _ in range(3):
        check_job(dialect_database)
    barrier = Barrier(3)

    def claim(index):
        barrier.wait()
        return Queue(dialect_database, node=node).claim(f"demo-slot-{index}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(claim, range(3)))
    assert len([job for job in jobs if job]) == 2
    assert len({job["id"] for job in jobs if job}) == 2


def test_lost_storage_after_registration_prevents_claim(dialect_database):
    node = register(dialect_database)
    check_job(dialect_database)
    marker = dialect_database.settings.data_dir / "state" / "worker-store.json"
    marker.unlink()
    assert Queue(dialect_database, node=node).claim("demo-lost-mount") is None


def test_offline_nodes_and_global_inventory(dialect_database, monkeypatch):
    node = register(dialect_database)
    job = check_job(dialect_database)
    assert eligible(node, job)
    now = time.time()
    monkeypatch.setattr("app.worker_registry.time.time", lambda: now + 16)
    assert not eligible(node, job)
    listed = WorkerRegistry(dialect_database).list()
    assert listed[0]["status"] == "offline"
    with dialect_database.engine.connect() as connection:
        assert connection.execute(select(db.worker_nodes.c.id)).scalar_one() == node["id"]


def test_superseded_node_cannot_fail_a_live_job(dialect_database):
    node = register(dialect_database)
    check_job(dialect_database)
    queue = Queue(dialect_database, node=node)
    job = queue.claim("demo-old-slot")
    with dialect_database.transaction() as connection:
        queue.set_state(connection, job, "running")
    WorkerRegistry(dialect_database).register(
        {
            "id": node["id"],
            "label": "DEMO replacement",
            "operations": ["check_production"],
            "slots": 1,
        }
    )
    queue.fail(job, "demo-old-slot", "DEMO_STALE", "DEMO stale failure")
    with dialect_database.engine.connect() as connection:
        assert queue.get(connection, job["project_id"], job["id"])["state"] == "running"


def test_superseded_node_cannot_record_a_remote_submission(dialect_database):
    from app.media import Cancelled

    node = register(dialect_database)
    check_job(dialect_database)
    queue = Queue(dialect_database, node=node)
    job = queue.claim("demo-old-slot")
    with dialect_database.transaction() as connection:
        queue.set_state(connection, job, "running")
    WorkerRegistry(dialect_database).register(
        {
            "id": node["id"],
            "label": "DEMO replacement",
            "operations": ["check_production"],
            "slots": 1,
        }
    )
    with pytest.raises(Cancelled):
        queue.update_remote(job, "demo-old-slot", submission_started=True)


def test_reaper_preserves_other_docker_controller_handles(dialect_database, monkeypatch):
    job = check_job(dialect_database)
    payload = {
        **job,
        "sandbox_cleanup_pending": True,
        "sandbox_run": {
            "container_id": "demo-container",
            "run_id": "a" * 32,
            "controller_id": "demo-other-daemon",
        },
    }
    with dialect_database.transaction() as connection:
        connection.execute(
            update(db.jobs).where(db.jobs.c.id == job["id"]).values(state="failed", payload=payload)
        )
    calls = []
    monkeypatch.setattr(
        skill_sandbox, "controller_identity", lambda: "demo-current-daemon", raising=False
    )
    monkeypatch.setattr(skill_sandbox, "cleanup_run", lambda nonce: calls.append(nonce) or True)
    SkillExecution(dialect_database).reap_containers()
    assert calls == []
    with dialect_database.engine.connect() as connection:
        assert connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
        ).scalar_one()["sandbox_cleanup_pending"]


def test_idle_node_refreshes_newly_available_runtime_capabilities(dialect_database, monkeypatch):
    registry = WorkerRegistry(dialect_database)
    registry.initialize_store()
    monkeypatch.setattr(registry, "capabilities", lambda _: ["python"])
    node = registry.register(
        {
            "id": "demo-live-runtime",
            "label": "DEMO dynamic capability",
            "operations": ["run_skill_script"],
            "slots": 1,
        }
    )
    now = time.time()
    monkeypatch.setattr("app.worker_registry.time.time", lambda: now + 6)
    monkeypatch.setattr(registry, "capabilities", lambda _: ["python", "node"])
    refreshed = registry.refresh(node, runtime=True)
    assert "node" in refreshed["capabilities"]
