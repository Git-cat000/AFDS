import json
import time
from uuid import uuid4

from sqlalchemy import insert, select, update

from app import db
from app.execution.queue import Queue
from app.execution.worker import Worker
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate, new_id


def test_diagnostics_truthfully_tracks_idle_active_and_stale_worker(client):
    database = client.app.state.database
    initial = client.get("/api/v1/system/status")
    assert initial.status_code == 200
    assert initial.json()["worker"]["status"] == "offline"
    worker = Worker(database)
    assert not worker.run_once()
    status = client.get("/api/v1/system/status").json()
    assert status["worker"]["online_count"] == 1
    assert status["database"]["schema_version"] == "0005"
    assert status["storage"]["free_bytes"] > 0
    assert all(value == "available" for value in status["tools"].values())
    assert str(database.settings.data_dir) not in json.dumps(status)
    with database.transaction() as connection:
        connection.execute(update(db.worker_heartbeats).values(last_seen_at=time.time() - 60))
    assert client.get("/api/v1/system/status").json()["worker"]["status"] == "offline"
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO liveness"))
    with database.transaction() as connection:
        Queue(database).enqueue(
            connection, apr, "import_audio", {"provider_id": "local-audio-import"}
        )
    job = worker.queue.claim(worker.owner)
    assert worker.queue.heartbeat(job["id"], worker.owner)
    assert client.get("/api/v1/system/status").json()["worker"]["status"] == "online"


def test_diagnostics_handles_unavailable_tools_without_leaking_paths(client, monkeypatch):
    import app.operations_service as operations

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("PRIVATE/path")

    monkeypatch.setattr(type(client.app.state.settings), "binary", unavailable)
    monkeypatch.setattr(operations.shutil, "disk_usage", unavailable)
    monkeypatch.setattr(operations, "native_otio_available", lambda: False)
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["tools"]["otio"] == "unavailable"
    assert data["storage"]["status"] == "unavailable" and data["storage"]["free_bytes"] is None
    assert "PRIVATE" not in json.dumps(data)


def fixture_jobs(database, count=3):
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO graph"))
    first, second, third, aid = (new_id() for _ in range(4))
    with database.transaction() as connection:
        for index in range(count):
            jid = [first, second, third][index] if index < 3 else new_id()
            payload = {
                "operation": "generate_video" if index == 0 else "render_timeline",
                "created_at": time.time() + index,
                "elapsed_ms": 1000 + index,
                "request": {
                    "items": [{"asset_id": aid}] if index else [],
                    "provider_config": {"api_key": "PRIVATE"},
                    "prompt": "PRIVATE",
                },
                "output_asset_ids": [aid] if index == 0 else [],
                "dependencies": [first] if index == 2 else [],
                "error": {"code": "MEDIA_TIMEOUT", "message": "PRIVATE"} if index == 1 else None,
            }
            connection.execute(
                insert(db.jobs).values(
                    id=jid,
                    project_id=apr["project_id"],
                    idempotency_key=jid,
                    state="failed" if index == 1 else "completed",
                    attempt=1,
                    available_at=0,
                    payload=payload,
                )
            )
    return apr, first, second, third


def test_project_metrics_and_graph_show_actual_lineage_and_sanitized_fields(client):
    apr, first, second, third = fixture_jobs(client.app.state.database)
    response = client.get(f"/api/v1/projects/{apr['project_id']}/operations")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["jobs"]["total"] == 3
    assert data["jobs"]["state_counts"] == {"completed": 2, "failed": 1}
    assert data["jobs"]["elapsed_ms"]["sample_count"] == 3
    assert {"source": first, "target": second, "kind": "media"} in data["graph"]["edges"]
    assert {"source": first, "target": third, "kind": "dependency"} in data["graph"]["edges"]
    assert "PRIVATE" not in json.dumps(data) and "provider_config" not in json.dumps(data)
    assert data["budget"]["spent"] == "0.000000"
    assert not data["graph"]["truncated"]
    assert client.get(f"/api/v1/projects/{uuid4()}/operations").status_code == 404


def test_large_job_history_keeps_complete_counts_but_bounds_graph(client):
    apr, *_ = fixture_jobs(client.app.state.database, 65)
    data = client.get(f"/api/v1/projects/{apr['project_id']}/operations").json()
    assert data["jobs"]["total"] == 65
    assert len([n for n in data["graph"]["nodes"] if n["type"] == "job"]) == 50
    assert data["graph"]["truncated"]
    assert len(data["graph"]["edges"]) <= 300
    assert len([n for n in data["graph"]["nodes"] if n["type"] == "asset"]) <= 100
    with client.app.state.database.engine.connect() as connection:
        assert (
            connection.execute(
                select(db.projects.c.revision).where(db.projects.c.id == apr["project_id"])
            ).scalar_one()
            == 1
        )
