import json

import pytest
from conftest import storyboard
from test_provider_contracts import configure, remote_server  # noqa: F401


def ready(client):
    from app.execution.worker import Worker

    apr = storyboard(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "provider_id": "mock-video-local",
        },
    )
    assert response.status_code == 202
    Worker(client.app.state.database).run_once()
    return client.get(f"/api/v1/projects/{pid}/apr").json()


@pytest.mark.media
def test_deterministic_review_has_owned_frame_evidence_and_semantic_not_run(client):
    from app.execution.worker import Worker

    apr = ready(client)
    pid = apr["project_id"]
    shot = apr["shots"][0]
    response = client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={"base_revision": apr["revision"], "shot_ids": [shot["id"]]},
    )
    assert response.status_code == 202, response.text
    Worker(client.app.state.database).run_once()
    job = client.get(f"/api/v1/projects/{pid}/jobs/{response.json()[0]['id']}").json()
    assert job["state"] == "completed", job
    reports = client.get(f"/api/v1/projects/{pid}/reviews").json()
    assert len(reports) == 1
    report = reports[0]
    assert report["asset_version_id"] == shot["active_asset_version_id"]
    assert report["stale"] is False
    assert any(c["code"] == "MEDIA_PROBE_VALID" and c["status"] == "pass" for c in report["checks"])
    assert any(
        c["code"] == "SEMANTIC_CONTINUITY" and c["status"] == "not_run" for c in report["checks"]
    )
    assert len(report["frames"]) == 3
    for frame in report["frames"]:
        assert (
            client.get(f"/api/v1/projects/{pid}/assets/{frame['asset_id']}/file").status_code == 200
        )
    fresh = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert fresh["shots"][0]["active_asset_version_id"] == shot["active_asset_version_id"]
    assert fresh["shots"][0]["status"] == shot["status"]


def test_missing_asset_review_rejected_without_job(client):
    apr = storyboard(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]]},
    )
    assert response.status_code == 409
    assert client.get(f"/api/v1/projects/{pid}/jobs").json() == []


@pytest.mark.media
def test_vision_review_requires_vision_opt_in_and_content_consent(client, remote_server):  # noqa: F811
    apr = ready(client)
    pid = apr["project_id"]
    configure(client, remote_server)
    body = {"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]], "mode": "vision"}
    assert client.post(f"/api/v1/projects/{pid}/reviews", json=body).status_code == 409
    assert (
        client.post(
            f"/api/v1/projects/{pid}/reviews", json={**body, "allow_remote": True}
        ).status_code
        == 422
    )
    assert remote_server["requests"] == []


@pytest.mark.media
def test_vision_review_validates_evidence_and_bills_once(client, remote_server):  # noqa: F811
    from app.execution.worker import Worker

    configure(client, remote_server, vision_enabled=True)
    remote_server["story"] = {
        "checks": [
            {
                "code": "CHARACTER_CONTINUITY",
                "status": "warning",
                "severity": "warning",
                "description": "Coat should be checked",
                "confidence": 0.7,
                "frame_indices": [0],
            }
        ]
    }
    apr = ready(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "mode": "vision",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    Worker(client.app.state.database).run_once()
    job = client.get(f"/api/v1/projects/{pid}/jobs/{response.json()[0]['id']}").json()
    assert job["state"] == "completed", job
    report = client.get(f"/api/v1/projects/{pid}/reviews").json()[0]
    finding = next(c for c in report["checks"] if c["code"] == "CHARACTER_CONTINUITY")
    assert finding["confidence"] == 0.7
    assert finding["evidence"]["frame_asset_ids"] == [report["frames"][0]["asset_id"]]
    assert "test-only-credential" not in json.dumps(report)
    sent = [r for r in remote_server["requests"] if r["path"].endswith("chat/completions")]
    assert len(sent) == 1
    assert any(
        c.get("type") == "image_url" for c in json.loads(sent[0]["body"])["messages"][1]["content"]
    )
    assert client.get(f"/api/v1/projects/{pid}/budget").json()["entries"][0]["state"] == "pending"


@pytest.mark.media
def test_review_marks_changed_shot_stale_and_does_not_overwrite(client, monkeypatch):
    from app.execution.worker import Worker
    from app.project_engine import ProjectEngine
    from app.review_service import ReviewService

    apr = ready(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]]},
    )
    assert response.status_code == 202
    execute = ReviewService.execute

    def change_after_review(service, job, work, checkpoint):
        result = execute(service, job, work, checkpoint)
        engine = ProjectEngine(service.database)
        current = engine.read(pid)
        engine.mutate(
            pid,
            current["revision"],
            "Change during review",
            lambda state, _: state["shots"][0].update(action="Changed after review"),
        )
        return result

    monkeypatch.setattr(ReviewService, "execute", change_after_review)
    Worker(client.app.state.database).run_once()
    report = client.get(f"/api/v1/projects/{pid}/reviews").json()[0]
    assert report["stale"] is True
    assert (
        client.get(f"/api/v1/projects/{pid}/apr").json()["shots"][0]["action"]
        == "Changed after review"
    )


@pytest.mark.media
def test_review_history_detects_later_source_changes(client):
    from app.execution.worker import Worker

    apr = ready(client)
    pid = apr["project_id"]
    client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]]},
    )
    Worker(client.app.state.database).run_once()
    assert client.get(f"/api/v1/projects/{pid}/reviews").json()[0]["stale"] is False
    current = client.get(f"/api/v1/projects/{pid}/apr").json()
    changed = client.patch(
        f"/api/v1/projects/{pid}/shots/{apr['shots'][0]['id']}",
        json={"base_revision": current["revision"], "patch": {"action": "A new action"}},
    )
    assert changed.status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/reviews").json()[0]["stale"] is True


@pytest.mark.media
def test_stale_review_worker_cannot_publish_evidence(client, monkeypatch):
    from sqlalchemy import update

    from app import db
    from app.execution.worker import Worker
    from app.review_service import ReviewService

    apr = ready(client)
    pid = apr["project_id"]
    client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]]},
    )
    original = ReviewService.execute

    def replacement(service, job, work, checkpoint):
        result = original(service, job, work, checkpoint)
        with service.database.transaction() as connection:
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job["id"]).values(lease_owner="new-worker")
            )
        return result

    monkeypatch.setattr(ReviewService, "execute", replacement)
    Worker(client.app.state.database).run_once()
    assert client.get(f"/api/v1/projects/{pid}/reviews").json() == []
    assert len(client.get(f"/api/v1/projects/{pid}/apr").json()["assets"]) == len(apr["assets"])


@pytest.mark.media
def test_vision_rejects_invented_frame_evidence_without_repeating_request(client, remote_server):  # noqa: F811
    from app.execution.worker import Worker

    configure(client, remote_server, vision_enabled=True)
    remote_server["story"] = {
        "checks": [
            {
                "code": "CHARACTER_CONTINUITY",
                "status": "pass",
                "severity": "info",
                "description": "Invented frame",
                "confidence": 1,
                "frame_indices": [99],
            }
        ]
    }
    apr = ready(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/reviews",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "mode": "vision",
            "allow_remote": True,
        },
    )
    Worker(client.app.state.database).run_once()
    job = client.get(f"/api/v1/projects/{pid}/jobs/{response.json()[0]['id']}").json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "PROVIDER_OUTPUT_INVALID"
    assert client.get(f"/api/v1/projects/{pid}/reviews").json() == []
    assert client.get(f"/api/v1/projects/{pid}/budget").json()["entries"][0]["state"] == "pending"
