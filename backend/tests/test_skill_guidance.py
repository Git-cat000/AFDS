import copy
import json
from uuid import uuid4

from conftest import storyboard
from test_provider_contracts import configure
from test_provider_contracts import remote_server as remote_server
from test_skills import import_package

from app.execution.queue import digest, shot_request
from app.execution.worker import Worker
from app.remote_media import prompt_for_shot


def bind_guidance(client, apr, stages):
    skill = import_package(client).json()
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/skills/guidance/commit",
        json={
            "base_revision": apr["revision"],
            "approved": True,
            "selections": {stage: [{"package_id": skill["id"]}] for stage in stages},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_video_guidance_changes_new_request_identity_without_changing_existing_shots(client):
    apr = storyboard(client)
    before = shot_request(apr, apr["shots"][0])
    updated = bind_guidance(client, apr, ["shots", "video"])
    after = shot_request(updated, updated["shots"][0])
    assert updated["shots"] == apr["shots"]
    assert digest(before) != digest(after)
    assert "Preserve screen direction" in prompt_for_shot(after)
    assert len(after["production_guidance"]) == 1
    legacy = copy.deepcopy(apr)
    legacy.pop("production_guidance", None)
    assert shot_request(legacy, legacy["shots"][0]) == before


def test_selected_story_guidance_reaches_actual_llm_request(client, remote_server):
    apr = bind_guidance(client, storyboard(client), ["story", "shots"])
    configure(client, remote_server)
    root = f"/api/v1/projects/{apr['project_id']}"
    response = client.post(
        f"{root}/planning/storyboard",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "message": "DEMO storyboard",
            "provider_id": "openai-llm",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    Worker(client.app.state.database).run_once()
    posted = next(
        item for item in remote_server["requests"] if item["path"] == "/v1/chat/completions"
    )
    context = json.loads(json.loads(posted["body"])["messages"][1]["content"])
    assert "Preserve screen direction" in context["production_guidance"][0]["instructions"]
    job = client.get(f"{root}/jobs/{response.json()['id']}").json()
    assert job["state"] == "completed", job
    plan = next(
        value
        for value in client.get(f"{root}/plans").json()
        if value["id"] == job["result_plan_id"]
    )
    assert (
        plan["production_guidance"][0]["package_id"]
        == context["production_guidance"][0]["package_id"]
    )


def test_image_and_speech_guidance_reach_their_separate_model_fields(client, remote_server):
    apr = bind_guidance(client, storyboard(client), ["image", "audio"])
    root = f"/api/v1/projects/{apr['project_id']}"
    for kind, operation in [("image", "generate_image"), ("speech", "generate_speech")]:
        configure(client, remote_server, kind)
        response = client.post(
            f"{root}/media",
            json={
                "base_revision": apr["revision"],
                "operation": operation,
                "prompt": "DEMO exact words",
                "provider_id": f"openai-{kind}",
                "allow_remote": True,
            },
        )
        assert response.status_code == 202, response.text
        Worker(client.app.state.database).run_once()
        job = client.get(f"{root}/jobs/{response.json()['id']}").json()
        assert job["state"] == "completed", job
        apr = client.get(f"{root}/apr").json()
    image = next(
        item for item in remote_server["requests"] if item["path"] == "/v1/images/generations"
    )
    speech = next(item for item in remote_server["requests"] if item["path"] == "/v1/audio/speech")
    assert "Preserve screen direction" in json.loads(image["body"])["prompt"]
    assert json.loads(speech["body"])["input"] == "DEMO exact words"
    assert "Preserve screen direction" in json.loads(speech["body"])["instructions"]


def test_guidance_survives_project_import_and_legacy_projects_default_to_empty(client):
    apr = bind_guidance(client, storyboard(client), ["editing", "delivery"])
    restored = client.post("/api/v1/projects/import", json=apr)
    assert restored.status_code == 201, restored.text
    assert restored.json()["production_guidance"] == apr["production_guidance"]
    legacy = copy.deepcopy(apr)
    legacy.pop("production_guidance")
    legacy["apr_schema_version"] = "0.3.0"
    imported = client.post("/api/v1/projects/import", json=legacy)
    assert imported.status_code == 201, imported.text
    assert imported.json()["production_guidance"] == {}


def test_known_speech_model_without_instructions_rejects_before_submission(client, remote_server):
    apr = bind_guidance(client, storyboard(client), ["audio"])
    configure(client, remote_server, "speech", model="tts-1")
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/media",
        json={
            "base_revision": apr["revision"],
            "operation": "generate_speech",
            "prompt": "DEMO exact words",
            "provider_id": "openai-speech",
            "allow_remote": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CAPABILITY_UNSUPPORTED"
    assert remote_server["requests"] == []
