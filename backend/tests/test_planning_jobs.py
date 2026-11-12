from uuid import uuid4

import pytest
from conftest import storyboard
from sqlalchemy import update
from test_provider_contracts import configure
from test_provider_contracts import remote_server as remote_server

from app import db
from app.execution.queue import Queue
from app.execution.worker import Worker
from app.media import Cancelled
from app.project_engine import ProjectEngine


def queue_story(client, apr, **changes):
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/planning/storyboard",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "message": "DEMO story",
            "provider_id": "mock-director",
            **changes,
        },
    )


def read_job(client, pid, jid):
    return client.get(f"/api/v1/projects/{pid}/jobs/{jid}").json()


def test_queued_story_survives_reload_and_requires_approval(client):
    apr = client.post("/api/v1/projects", json={"title": "DEMO queued story"}).json()
    response = queue_story(client, apr)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["state"] == "queued"
    assert client.get(f"/api/v1/projects/{apr['project_id']}/plans").json() == []
    assert Worker(client.app.state.database).run_once()
    result = read_job(client, apr["project_id"], job["id"])
    assert result["state"] == "completed", result.get("error")
    plans = client.get(f"/api/v1/projects/{apr['project_id']}/plans").json()
    assert len(plans) == 1 and plans[0]["id"] == result["result_plan_id"]
    assert client.get(f"/api/v1/projects/{apr['project_id']}/apr").json()["shots"] == []
    approved = client.post(
        f"/api/v1/projects/{apr['project_id']}/plans/{plans[0]['id']}/execute",
        json={"base_revision": apr["revision"], "approved": True},
    )
    assert approved.status_code == 200 and len(approved.json()["shots"]) == 4


def test_planning_request_id_is_stable_across_revision_changes(client):
    apr = client.post("/api/v1/projects", json={"title": "DEMO identity"}).json()
    request_id = str(uuid4())
    first = queue_story(client, apr, request_id=request_id)
    assert first.status_code == 202
    ProjectEngine(client.app.state.database).mutate(
        apr["project_id"],
        apr["revision"],
        "Edit",
        lambda value, connection: value.update(brief="changed"),
    )
    repeated = queue_story(client, apr, request_id=request_id)
    assert repeated.status_code == 202 and repeated.json()["id"] == first.json()["id"]
    assert queue_story(client, apr, request_id=request_id, message="different").status_code == 409


def test_cancelled_and_stale_planning_never_publish(client):
    apr = client.post("/api/v1/projects", json={"title": "DEMO fencing"}).json()
    response = queue_story(client, apr)
    assert response.status_code == 202
    job = response.json()
    client.post(f"/api/v1/projects/{apr['project_id']}/jobs/{job['id']}/cancel")
    assert not Worker(client.app.state.database).run_once()
    second = queue_story(client, apr).json()
    ProjectEngine(client.app.state.database).mutate(
        apr["project_id"],
        apr["revision"],
        "Edit",
        lambda value, connection: value.update(brief="changed"),
    )
    Worker(client.app.state.database).run_once()
    assert read_job(client, apr["project_id"], second["id"])["error"]["code"] == "REVISION_CONFLICT"
    assert client.get(f"/api/v1/projects/{apr['project_id']}/plans").json() == []


def test_queued_remote_llm_uses_existing_budget_and_saves_one_plan(client, remote_server):
    configure(client, remote_server)
    apr = client.post("/api/v1/projects", json={"title": "DEMO queued LLM"}).json()
    response = queue_story(client, apr, provider_id="openai-llm", allow_remote=True)
    assert response.status_code == 202
    assert not any(r["path"].endswith("chat/completions") for r in remote_server["requests"])
    Worker(client.app.state.database).run_once()
    job = read_job(client, apr["project_id"], response.json()["id"])
    assert job["state"] == "completed", job.get("error")
    assert (
        len([r for r in remote_server["requests"] if r["path"].endswith("chat/completions")]) == 1
    )
    budget = client.get(f"/api/v1/projects/{apr['project_id']}/budget").json()
    assert len(budget["entries"]) == 1 and budget["entries"][0]["state"] == "pending"
    assert "test-only-credential" not in str(job)


def test_uncertain_planning_intent_is_not_replayed_after_worker_recovery(client, remote_server):
    configure(client, remote_server)
    apr = client.post("/api/v1/projects", json={"title": "DEMO uncertain LLM"}).json()
    response = queue_story(client, apr, provider_id="openai-llm", allow_remote=True)
    assert response.status_code == 202
    job = response.json()
    database = client.app.state.database
    with database.transaction() as connection:
        current = Queue(database).get(connection, apr["project_id"], job["id"])
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(
                payload={
                    **{
                        k: v
                        for k, v in current.items()
                        if k
                        not in {
                            "id",
                            "project_id",
                            "state",
                            "attempt",
                            "lease_owner",
                            "lease_expires_at",
                            "available_at",
                        }
                    },
                    "submission_started": True,
                }
            )
        )
    Worker(database).run_once()
    final = read_job(client, apr["project_id"], job["id"])
    assert final["error"]["code"] == "REMOTE_SUBMISSION_UNCERTAIN"
    assert not any(r["path"].endswith("chat/completions") for r in remote_server["requests"])


def test_queued_semantic_proposal_keeps_edit_scope_and_requires_approval(client):
    apr = storyboard(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/planning/semantic",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "mode": "structured",
            "shot_ids": [apr["shots"][0]["id"]],
            "fields": {"action": "DEMO changed"},
        },
    )
    assert response.status_code == 202, response.text
    Worker(client.app.state.database).run_once()
    job = read_job(client, pid, response.json()["id"])
    assert job["state"] == "completed", job.get("error")
    assert client.get(f"/api/v1/projects/{pid}/apr").json()["shots"][0]["action"] != "DEMO changed"
    result = client.post(
        f"/api/v1/projects/{pid}/semantic-edits/{job['result_plan_id']}/execute",
        json={"base_revision": apr["revision"], "approved": True},
    )
    assert result.status_code == 200, result.text
    assert result.json()["shots"][0]["action"] == "DEMO changed"
    assert result.json()["shots"][1]["action"] == apr["shots"][1]["action"]


def test_expired_planning_attempt_cannot_publish(client):
    from app.planning_service import PlanningService

    apr = client.post("/api/v1/projects", json={"title": "DEMO old attempt"}).json()
    response = queue_story(client, apr)
    assert response.status_code == 202
    database = client.app.state.database
    queue = Queue(database)
    job = queue.claim("old-owner")
    with database.transaction() as connection:
        queue.set_state(connection, job, "running")
    service = PlanningService(database)
    plan = service.execute(job, "old-owner", lambda: True)
    with database.transaction() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(attempt=job["attempt"] + 1, lease_owner="replacement")
        )
    with pytest.raises(Cancelled):
        service.publish(job, "old-owner", plan, 1)
    assert client.get(f"/api/v1/projects/{apr['project_id']}/plans").json() == []
