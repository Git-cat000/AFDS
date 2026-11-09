import json
from uuid import uuid4

import pytest
from test_continuity import fixture_apr
from test_provider_contracts import configure, remote_server  # noqa: F401
from test_reviews import ready


def test_invalidation_preserves_protected_downstream_selection():
    from app.project_engine import ProjectEngine

    apr = fixture_apr()
    source, downstream = apr["shots"][:2]
    downstream.update(status="approved", locked=True, active_asset_version_id="selected")
    apr["asset_versions"] = [{"id": "selected", "shot_id": downstream["id"], "status": "ready"}]
    edges = [
        {"source": source["id"], "target": downstream["id"]},
        {"source": downstream["id"], "target": "selected"},
    ]

    class Rows:
        def mappings(self):
            return edges

    class Connection:
        def execute(self, statement):
            return Rows()

    ProjectEngine(None).invalidate(Connection(), apr, [source["id"]])
    assert downstream["status"] == "needs_review"
    assert apr["asset_versions"][0]["status"] == "ready"


def test_anchor_preview_lists_protected_downstream_impact():
    from app.project_engine import rebuild_timeline
    from app.timeline_service import AnchorRequest, preview_anchors

    apr = fixture_apr()
    first, second, last = apr["shots"]
    apr["continuity_links"][0]["target_shot_id"] = last["id"]
    last["locked"] = True
    rebuild_timeline(apr, [s["id"] for s in apr["shots"]])
    apr["timeline"]["markers"] = [
        {"id": str(uuid4()), "frame": 110, "kind": "beat", "label": "beat"}
    ]
    result = preview_anchors(
        apr, AnchorRequest(base_revision=apr["revision"], boundary_shot_ids=[first["id"]])
    )
    assert set(result["affected_shot_ids"]) == {first["id"], second["id"], last["id"]}
    assert last["id"] in result["protected_shot_ids"]


def test_world_state_and_link_defaults_are_normalized_before_import(client):
    from app.schemas import APR

    apr = fixture_apr()
    apr["world_states"] = [{"scene_id": apr["scenes"][0]["id"].upper()}]
    apr["continuity_links"][0].pop("id")
    apr["continuity_links"][0]["source_shot_id"] = apr["shots"][0]["id"].upper()
    state = APR.model_validate(apr).model_dump(mode="json")
    assert state["world_states"][0].get("id")
    assert state["world_states"][0]["scene_id"] == apr["scenes"][0]["id"]
    assert state["continuity_links"][0].get("id")
    response = client.post("/api/v1/projects/import", json=state)
    assert response.status_code == 201, response.text
    assert response.json()["world_states"][0]["scene_id"] == response.json()["scenes"][0]["id"]


@pytest.mark.parametrize("operation", ["storyboard", "semantic", "vision"])
def test_encoded_provider_credentials_are_rejected_after_json_decode(
    client,
    remote_server,  # noqa: F811
    operation,
):
    from app.execution.worker import Worker

    configure(client, remote_server, vision_enabled=True)
    apr = ready(client)
    pid = apr["project_id"]
    key = "test-only-credential"
    if operation == "storyboard":
        result = {
            "scene": {"title": "Scene"},
            "shots": [{"title": "Shot", "action": key, "duration_frames": 120}],
        }
    elif operation == "semantic":
        result = {"edits": [{"shot_id": apr["shots"][0]["id"], "fields": {"action": key}}]}
    else:
        result = {
            "checks": [
                {
                    "code": "CHARACTER_CONTINUITY",
                    "status": "warning",
                    "severity": "warning",
                    "description": key,
                    "confidence": 0.8,
                    "frame_indices": [0],
                }
            ]
        }
    remote_server["story_raw"] = json.dumps(result).replace(
        key, "".join(f"\\u{ord(c):04x}" for c in key)
    )
    base = {"base_revision": apr["revision"], "allow_remote": True}
    if operation == "storyboard":
        response = client.post(
            f"/api/v1/projects/{pid}/director",
            json={**base, "provider_id": "openai-llm", "message": "Plan"},
        )
        assert response.status_code == 422
    elif operation == "semantic":
        response = client.post(
            f"/api/v1/projects/{pid}/semantic-edits",
            json={**base, "shot_ids": [apr["shots"][0]["id"]], "mode": "llm", "message": "Edit"},
        )
        assert response.status_code == 422
    else:
        response = client.post(
            f"/api/v1/projects/{pid}/reviews",
            json={**base, "shot_ids": [apr["shots"][0]["id"]], "mode": "vision"},
        )
        Worker(client.app.state.database).run_once()
        job = client.get(f"/api/v1/projects/{pid}/jobs/{response.json()[0]['id']}").json()
        assert job["state"] == "failed"
        assert client.get(f"/api/v1/projects/{pid}/reviews").json() == []
    assert key not in client.get(f"/api/v1/projects/{pid}/plans").text


def test_export_retry_after_cancelled_publication_uses_fresh_workdir(client, monkeypatch):
    from app.execution.worker import Worker
    from app.media import Cancelled

    apr = ready(client)
    pid = apr["project_id"]
    apr = client.post(
        f"/api/v1/projects/{pid}/timeline",
        json={"base_revision": apr["revision"], "shot_ids": [apr["shots"][0]["id"]]},
    ).json()
    response = client.post(
        f"/api/v1/projects/{pid}/exports", json={"base_revision": apr["revision"], "format": "zip"}
    )
    job_id = response.json()["id"]
    worker = Worker(client.app.state.database)
    publish = worker.publish_export

    def interrupt(job, result, elapsed):
        worker.queue.cancel(pid, job["id"])
        raise Cancelled()

    monkeypatch.setattr(worker, "publish_export", interrupt)
    worker.run_once()
    assert client.get(f"/api/v1/projects/{pid}/jobs/{job_id}").json()["state"] == "cancelled"
    client.post(f"/api/v1/projects/{pid}/jobs/{job_id}/retry")
    monkeypatch.setattr(worker, "publish_export", publish)
    worker.run_once()
    final = client.get(f"/api/v1/projects/{pid}/jobs/{job_id}").json()
    assert final["state"] == "completed", final.get("error")
