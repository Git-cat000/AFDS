"""Normal HTTP entry points select and retain candidate connection versions."""

from uuid import uuid4

import pytest
from conftest import project, storyboard
from test_audio_analysis import analysis_film as analysis_film
from test_provider_contracts import remote_server as remote_server
from test_transcription_contracts import transcription_server as transcription_server

from app.execution.worker import Worker


def save_candidate(client, kind, url, **values):
    response = client.put(
        f"/api/v1/settings/candidates/demo-{kind}",
        json={
            "kind": kind,
            "connection": {
                "label": "DEMO alternate " + kind,
                "base_url": url + "/v1",
                "allow_private_network": True,
                "model": "test-model",
                "api_key": "DEMO-contract-only-key",
                "enabled": True,
                "request_cost": "0",
                **values,
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "kind,operation", [("image", "generate_image"), ("speech", "generate_speech")]
)
def test_candidate_media_uses_pinned_version_and_keeps_request_identity(
    client, remote_server, kind, operation
):
    state = remote_server
    address = state["base_url"].removesuffix("/v1")
    save_candidate(client, kind, address)
    apr = project(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    body = {
        "base_revision": apr["revision"],
        "operation": operation,
        "prompt": "DEMO candidate",
        "provider_id": "demo-" + kind,
        "allow_remote": True,
        "request_id": str(uuid4()),
    }
    queued = client.post(root + "/media", json=body)
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["request"]["provider_config"]["connection_id"] == "demo-" + kind
    save_candidate(client, kind, address, revision=1, model="DEMO future model")
    replay = client.post(root + "/media", json=body)
    assert replay.status_code == 202 and replay.json()["id"] == job["id"], replay.text
    assert Worker(client.app.state.database).run_once()
    finished = client.get(root + "/jobs/" + job["id"]).json()
    assert finished["state"] == "completed", finished
    assert finished["request"]["provider_config"]["revision"] == 1
    apr = client.get(root + "/apr").json()
    assert apr["assets"][0]["provenance"]["model_route"]["selected_id"] == "demo-" + kind
    assert len(client.get(root + "/budget").json()["entries"]) == 1
    assert len([request for request in state["requests"] if request["method"] == "POST"]) == 1


def test_candidate_llm_is_used_by_synchronous_and_queued_planning(client, remote_server):
    state = remote_server
    address = state["base_url"].removesuffix("/v1")
    save_candidate(client, "llm", address)
    apr = project(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    body = {
        "base_revision": apr["revision"],
        "message": "DEMO plan",
        "allow_remote": True,
        "provider_id": "demo-llm",
        "request_id": str(uuid4()),
    }
    planned = client.post(root + "/director", json=body)
    assert planned.status_code == 200, planned.text
    assert planned.json()["model_route"]["selected_id"] == "demo-llm"
    assert client.post(root + "/director", json=body).json()["id"] == planned.json()["id"]
    body["request_id"] = str(uuid4())
    queued = client.post(root + "/planning/storyboard", json=body)
    assert queued.status_code == 202, queued.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(root + "/jobs/" + queued.json()["id"]).json()
    assert job["state"] == "completed", job
    assert job["request"]["provider_config"]["connection_id"] == "demo-llm"
    assert len([request for request in state["requests"] if request["method"] == "POST"]) == 2


def test_project_policy_preview_and_generation_use_the_same_candidate(client, remote_server):
    address = remote_server["base_url"].removesuffix("/v1")
    save_candidate(client, "image", address)
    apr = project(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    response = client.put(
        root + "/routing/policy",
        json={
            "base_revision": apr["revision"],
            "policies": {
                "text_to_image": {
                    "strategy": "lowest_estimated_cost",
                    "allowed_ids": ["demo-image"],
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    apr = response.json()
    preview = client.post(
        root + "/routing/preview",
        json={
            "base_revision": apr["revision"],
            "capability": "text_to_image",
            "allow_remote": True,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["selected_id"] == "demo-image"
    queued = client.post(
        root + "/media",
        json={
            "base_revision": apr["revision"],
            "operation": "generate_image",
            "prompt": "DEMO routed",
            "allow_remote": True,
            "route_preview_id": preview.json()["id"],
        },
    )
    assert queued.status_code == 202, queued.text
    config = queued.json()["request"]["provider_config"]
    assert config["route"]["id"] == preview.json()["id"]
    assert config["connection_id"] == "demo-image"


def test_video_candidate_can_be_selected_at_generation_entry(client, remote_server):
    address = remote_server["base_url"].removesuffix("/v1")
    save_candidate(client, "video", address)
    apr = storyboard(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    response = client.post(
        root + "/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "provider_id": "demo-video",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()[0]["request"]["provider_config"]["connection_id"] == "demo-video"


def test_video_request_identity_survives_candidate_changes(client, remote_server):
    address = remote_server["base_url"].removesuffix("/v1")
    save_candidate(client, "video", address)
    apr = storyboard(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    body = {
        "base_revision": apr["revision"],
        "shot_ids": [apr["shots"][0]["id"]],
        "provider_id": "demo-video",
        "allow_remote": True,
        "request_id": str(uuid4()),
    }
    first = client.post(root + "/generate", json=body)
    assert first.status_code == 202, first.text
    save_candidate(client, "video", address, revision=1, model="DEMO future video")
    replay = client.post(root + "/generate", json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()[0]["id"] == first.json()[0]["id"]
    assert len(client.get(root + "/budget").json()["entries"]) == 1


def test_video_request_identity_preserves_shot_freshness(client):
    apr = storyboard(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    response = client.post(
        root + "/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(root + "/jobs/" + response.json()[0]["id"]).json()
    assert job["state"] == "completed", job


def test_transcription_candidate_reaches_the_timestamp_contract(
    analysis_film, transcription_server
):
    client, root, apr, asset_id = analysis_film
    save_candidate(
        client,
        "transcription",
        transcription_server["base_url"].removesuffix("/v1"),
        transcription_timestamps=True,
    )
    response = client.post(
        root + "/timeline/analysis",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "kind": "transcription",
            "asset_id": asset_id,
            "duration_frames": 96,
            "provider_id": "demo-transcription",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(root + "/jobs/" + response.json()["id"]).json()
    assert job["state"] == "completed", job
    assert job["request"]["provider_config"]["route"]["selected_id"] == "demo-transcription"
    assert len(transcription_server["requests"]) == 1


def test_vision_candidate_reaches_actual_review_contract(client, remote_server):
    from test_reviews import ready

    apr = ready(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    save_candidate(
        client, "llm", remote_server["base_url"].removesuffix("/v1"), vision_enabled=True
    )
    remote_server["story"] = {
        "checks": [
            {
                "code": "CHARACTER_CONTINUITY",
                "status": "pass",
                "severity": "info",
                "description": "DEMO frame",
                "confidence": 0.8,
                "frame_indices": [0],
            }
        ]
    }
    response = client.post(
        root + "/reviews",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "mode": "vision",
            "provider_id": "demo-llm",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(root + "/jobs/" + response.json()[0]["id"]).json()
    assert job["state"] == "completed", job
    assert job["request"]["provider_config"]["route"]["capability"] == "visual_review"


@pytest.mark.parametrize("path", ["/semantic-edits", "/planning/semantic"])
def test_semantic_candidate_reaches_actual_llm_contract(client, remote_server, path):
    save_candidate(client, "llm", remote_server["base_url"].removesuffix("/v1"))
    apr = storyboard(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    remote_server["story"] = {
        "edits": [{"shot_id": apr["shots"][0]["id"], "fields": {"action": "DEMO routed action"}}]
    }
    response = client.post(
        root + path,
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "shot_ids": [apr["shots"][0]["id"]],
            "mode": "llm",
            "message": "DEMO edit",
            "provider_id": "demo-llm",
            "allow_remote": True,
        },
    )
    assert response.status_code in {200, 202}, response.text
    if path == "/planning/semantic":
        assert Worker(client.app.state.database).run_once()
        response = client.get(root + "/jobs/" + response.json()["id"])
        assert response.json()["state"] == "completed", response.text
    assert len(remote_server["requests"]) == 1


def test_worker_falls_back_before_posting_when_credential_cannot_be_decrypted(
    client, remote_server
):
    from sqlalchemy import update

    from app.candidate_store import CandidateStore, CandidateUpdate, candidate_revisions

    database = client.app.state.database
    store = CandidateStore(database)
    for identity in ("demo-first", "demo-backup"):
        store.save(
            identity,
            "image",
            CandidateUpdate(
                label=identity,
                base_url=remote_server["base_url"],
                allow_private_network=True,
                enabled=True,
                model="test-model",
                request_cost="0",
                api_key="DEMO-contract-only-key",
            ),
        )
    apr = project(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    saved = client.put(
        root + "/routing/policy",
        json={
            "base_revision": apr["revision"],
            "policies": {
                "text_to_image": {
                    "preferred_ids": ["demo-first"],
                    "allowed_ids": ["demo-first", "demo-backup"],
                    "allow_fallback": True,
                }
            },
        },
    )
    assert saved.status_code == 200
    response = client.post(
        root + "/media",
        json={
            "base_revision": saved.json()["revision"],
            "operation": "generate_image",
            "prompt": "DEMO fallback",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    with database.transaction() as connection:
        connection.execute(
            update(candidate_revisions)
            .where(candidate_revisions.c.candidate_id == "demo-first")
            .values(secret="DEMO invalid ciphertext")
        )
    assert Worker(database).run_once()
    job = client.get(root + "/jobs/" + response.json()["id"]).json()
    assert job["state"] == "completed", job
    assert job["request"]["provider_config"]["connection_id"] == "demo-backup"
    assert len(remote_server["requests"]) == 1


def test_candidate_health_test_controls_routing_eligibility(client, remote_server):
    save_candidate(client, "llm", remote_server["base_url"].removesuffix("/v1"))
    path = "/api/v1/settings/candidates/demo-llm/test"
    healthy = client.post(path)
    assert healthy.status_code == 200, healthy.text
    assert client.get("/api/v1/settings/candidates").json()[0]["health"] == "healthy"
    remote_server["error"] = 401
    assert client.post(path).status_code == 422
    apr = project(client)
    denied = client.post(
        f"/api/v1/projects/{apr['project_id']}/routing/preview",
        json={
            "base_revision": apr["revision"],
            "capability": "storyboard",
            "provider_id": "demo-llm",
            "allow_remote": True,
        },
    )
    assert denied.status_code == 409
    assert "CANDIDATE_UNHEALTHY" in denied.text


def test_candidate_model_discovery_respects_project_grants(client, remote_server):
    apr = project(client)
    other = project(client)
    save_candidate(
        client,
        "image",
        remote_server["base_url"].removesuffix("/v1"),
        all_projects=False,
        project_ids=[apr["project_id"]],
    )
    visible = client.get(f"/api/v1/models?project_id={apr['project_id']}")
    assert visible.status_code == 200, visible.text
    assert any(value["provider_id"] == "demo-image" for value in visible.json())
    assert all(
        value["provider_id"] != "demo-image" for value in client.get("/api/v1/models").json()
    )
    assert all(
        value["provider_id"] != "demo-image"
        for value in client.get(f"/api/v1/models?project_id={other['project_id']}").json()
    )
    assert "base_url" not in visible.text and "api_key" not in visible.text


def test_legacy_planning_request_accepts_new_optional_defaults(client):
    from sqlalchemy import select, update

    from app import db

    apr = project(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    body = {
        "base_revision": apr["revision"],
        "request_id": str(uuid4()),
        "message": "DEMO legacy",
        "provider_id": "mock-director",
    }
    first = client.post(root + "/planning/storyboard", json=body)
    assert first.status_code == 202
    database = client.app.state.database
    with database.transaction() as connection:
        payload = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == first.json()["id"])
        ).scalar_one()
        payload["request"]["input"].pop("route_preview_id")
        connection.execute(
            update(db.jobs).where(db.jobs.c.id == first.json()["id"]).values(payload=payload)
        )
    replay = client.post(root + "/planning/storyboard", json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]


def test_legacy_media_request_reuses_its_original_budget_obligation(client, remote_server):
    from sqlalchemy import select, update
    from test_provider_settings import save_provider

    from app import db

    saved = save_provider(
        client,
        "image",
        base_url=remote_server["base_url"],
        allow_private_network=True,
        request_cost="0",
    )
    assert saved.status_code == 200
    apr = project(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    body = {
        "base_revision": apr["revision"],
        "request_id": str(uuid4()),
        "prompt": "DEMO legacy",
        "operation": "generate_image",
        "provider_id": "openai-image",
        "allow_remote": True,
    }
    first = client.post(root + "/media", json=body)
    assert first.status_code == 202, first.text
    database = client.app.state.database
    with database.transaction() as connection:
        payload = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == first.json()["id"])
        ).scalar_one()
        payload["request"].pop("input")
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == first.json()["id"])
            .values(payload=payload, idempotency_key="DEMO legacy fingerprint " + uuid4().hex)
        )
    replay = client.post(root + "/media", json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert len(client.get(root + "/budget").json()["entries"]) == 1
