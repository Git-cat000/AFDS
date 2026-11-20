"""Final-review workflow regressions; no Docker or paid provider calls."""

from uuid import uuid4

import pytest
from test_access import team as team
from test_collaboration import canvas as canvas
from test_collaboration import operation
from test_routing_service import prepare
from test_skill_execution import execute_script, script_preview

from app import skill_sandbox
from app.errors import DomainError
from app.identity_context import LOCAL_USER_ID
from app.routing_service import RoutePreviewRequest, RoutingService
from app.worker_registry import WorkerRegistry


def test_consecutive_own_undo_can_restore_prior_entity_states(canvas):
    service, pid = canvas
    first = operation("note-a", "add_node", {"id": "note-a", "kind": "note", "text": "one"})
    second = operation("note-a", "edit_note", "two", 1)
    third = operation("note-a", "edit_note", "three", 2)
    for item in (first, second, third):
        service.apply_operations(pid, [item], LOCAL_USER_ID)
    assert (
        service.undo_operation(pid, third.operation_id, LOCAL_USER_ID)["document"]["nodes"][0][
            "text"
        ]
        == "two"
    )
    assert (
        service.undo_operation(pid, second.operation_id, LOCAL_USER_ID)["document"]["nodes"][0][
            "text"
        ]
        == "one"
    )
    assert service.undo_operation(pid, first.operation_id, LOCAL_USER_ID)["document"]["nodes"] == []


def test_route_snapshot_must_match_the_approved_candidate_version(dialect_database, monkeypatch):
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
    original = service.store.snapshot

    def newer_snapshot(*args, **kwargs):
        value = original(*args, **kwargs)
        return {**value, "revision": value["revision"] + 1, "model": "DEMO concurrent update"}

    monkeypatch.setattr(service.store, "snapshot", newer_snapshot)
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


def test_reviewer_can_preview_only_review_routes(team):
    owner = team.as_user("owner")
    saved = owner.put(
        "/api/v1/settings/candidates/demo-vision",
        json={
            "kind": "llm",
            "connection": {
                "label": "DEMO vision",
                "base_url": "https://example.com/v1",
                "model": "DEMO",
                "enabled": True,
                "vision_enabled": True,
                "request_cost": "0",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    body = {
        "base_revision": team.apr["revision"],
        "capability": "visual_review",
        "provider_id": "demo-vision",
        "allow_remote": True,
    }
    reviewer = team.as_user("reviewer")
    result = reviewer.post(team.root + "/routing/preview", json=body)
    assert result.status_code == 200, result.text
    body["capability"] = "storyboard"
    assert reviewer.post(team.root + "/routing/preview", json=body).status_code == 403
    body["capability"] = "visual_review"
    assert team.as_user("viewer").post(team.root + "/routing/preview", json=body).status_code == 403


def test_script_approval_uses_registered_controller_without_api_docker(client, monkeypatch):
    from conftest import project

    database = client.app.state.database
    registry = WorkerRegistry(database)
    registry.initialize_store()
    capability = "sandbox:sha256:" + "a" * 64 + ":afds-container-v1"
    monkeypatch.setattr(registry, "capabilities", lambda operations: ["python", capability])
    registry.register(
        {
            "id": "demo-script-controller",
            "label": "DEMO controller",
            "operations": ["run_skill_script"],
        }
    )

    def no_api_docker(*args):
        raise DomainError("SANDBOX_CLIENT_MISSING", "DEMO API has no Docker", 422)

    monkeypatch.setattr(skill_sandbox, "execution_policy", no_api_docker)
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    assert plan["runtime"]["available"], plan["runtime"]
    assert plan["request"]["execution_policy"]["image_id"] == "sha256:" + "a" * 64
    queued = execute_script(client, apr, plan)
    assert queued.status_code == 202, queued.text
    registry.drain("demo-script-controller", True)
    newer = script_preview(client, apr, execution_mode="container")
    assert not newer["runtime"]["available"]


def test_container_staging_accepts_only_its_configured_external_cache(tmp_path):
    from app.config import Settings

    settings = Settings(data_dir=tmp_path / "data", worker_cache_dir=tmp_path / "cache")
    package = settings.cache_root / "work" / "demo-job"
    package.mkdir(parents=True)
    (package / "run.py").write_text("print('DEMO')", encoding="utf-8")
    policy = skill_sandbox.ScriptExecutionPolicy(image_id="sha256:" + "a" * 64, timeout_seconds=5)
    staged = skill_sandbox.prepare_run(settings, package, "run.py", [], {}, policy, "a" * 32)
    assert staged
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(DomainError, match="outside"):
        skill_sandbox.prepare_run(settings, unrelated, "run.py", [], {}, policy, "b" * 32)


def test_lost_review_response_reuses_its_original_route_and_cost(client):
    from conftest import storyboard
    from test_routing_api import save_candidate

    from app.execution.worker import Worker

    apr = storyboard(client)
    root = f"/api/v1/projects/{apr['project_id']}"
    queued = client.post(
        root + "/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "provider_id": "mock-video-local",
        },
    )
    assert queued.status_code == 202, queued.text
    assert Worker(client.app.state.database).run_once()
    apr = client.get(root + "/apr").json()
    save_candidate(client, "llm", "https://example.com", vision_enabled=True)
    body = {
        "base_revision": apr["revision"],
        "shot_ids": [apr["shots"][0]["id"]],
        "mode": "vision",
        "allow_remote": True,
        "provider_id": "demo-llm",
        "request_id": str(uuid4()),
    }
    first = client.post(root + "/reviews", json=body)
    assert first.status_code == 202, first.text
    save_candidate(
        client, "llm", "https://example.com", vision_enabled=True, revision=1, model="DEMO changed"
    )
    again = client.post(root + "/reviews", json=body)
    assert again.status_code == 202, again.text
    assert again.json()[0]["id"] == first.json()[0]["id"]
    assert again.json()[0]["request"]["provider_config"]["revision"] == 1
    assert len(client.get(root + "/budget").json()["entries"]) == 1
    changed = client.post(root + "/reviews", json={**body, "frame_count": 4})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
    other_shot = client.post(root + "/reviews", json={**body, "shot_ids": [apr["shots"][1]["id"]]})
    assert (
        other_shot.status_code == 409
        and other_shot.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
    )
