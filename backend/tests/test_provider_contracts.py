import base64
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import project, storyboard
from PIL import Image
from test_provider_settings import save_provider


@pytest.fixture
def remote_server():
    state = {
        "requests": [],
        "error": None,
        "video_bytes": b"",
        "polls": 0,
        "video_status": "completed",
        "story": {
            "scene": {"title": "Generated scene", "description": "Test scene"},
            "shots": [
                {
                    "title": "First",
                    "action": "Walk across the room",
                    "duration_frames": 120,
                    "camera": {"shot_size": "wide"},
                }
            ],
        },
    }
    png = io.BytesIO()
    Image.new("RGB", (160, 90), "#334455").save(png, format="PNG")
    wav = io.BytesIO()
    with wave.open(wav, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            status, content_type = 200, "application/json"
            if state["error"]:
                status = state["error"]
                data = {"error": {"message": "test-only-credential upstream confidential echo"}}
            elif self.path == "/v1/models":
                data = {"data": [{"id": "test-model"}, {"id": "second-model"}]}
            elif self.path == "/v1/chat/completions":
                content = state["story"]
                data = {
                    "choices": [
                        {"message": {"content": state.get("story_raw") or json.dumps(content)}}
                    ],
                    "usage": {"total_tokens": 100},
                }
            elif self.path == "/v1/images/generations":
                data = (
                    {"data": [{"url": state["image_url"]}]}
                    if state.get("image_url")
                    else {"data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}]}
                )
            elif self.path == "/v1/audio/speech":
                data, content_type = wav.getvalue(), "audio/wav"
            elif self.path == "/v1/videos" and self.command == "POST":
                data = {"id": "video-contract-123", "status": "queued"}
            elif self.path == "/v1/videos/video-contract-123":
                state["polls"] += 1
                data = {
                    "id": "video-contract-123",
                    "status": state["video_status"],
                    "progress": 100,
                }
            elif self.path == "/v1/videos/video-contract-123/content":
                data, content_type = state["video_bytes"], "video/mp4"
            else:
                data, status = {"error": "Unknown test endpoint"}, 404
            payload = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def configure(client, remote, kind="llm", **changes):
    response = save_provider(
        client, kind, base_url=remote["base_url"], allow_private_network=True, **changes
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_connection_test_only_lists_models_and_redacts_errors(client, remote_server):
    configure(client, remote_server)
    response = client.post("/api/v1/settings/providers/llm/test", json={})
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["second-model", "test-model"]
    assert [r["path"] for r in remote_server["requests"]] == ["/v1/models"]
    assert remote_server["requests"][0]["authorization"] == "Bearer test-only-credential"
    remote_server["error"] = 401
    failed = client.post("/api/v1/settings/providers/llm/test", json={})
    assert failed.status_code == 422
    assert failed.json()["error"]["code"] == "PROVIDER_AUTH_FAILED"
    assert "test-only-credential" not in failed.text


def test_real_llm_plan_is_validated_and_requires_consent(client, remote_server):
    configure(client, remote_server)
    apr = project(client)
    body = {"base_revision": apr["revision"], "provider_id": "openai-llm", "message": "A farewell"}
    path = f"/api/v1/projects/{apr['project_id']}/director"
    assert client.post(path, json=body).status_code == 409
    assert remote_server["requests"] == []
    response = client.post(path, json={**body, "allow_remote": True})
    assert response.status_code == 200, response.text
    assert response.json()["provider_id"] == "openai-llm"
    assert response.json()["estimated_cost"]["status"] == "unknown"
    assert response.json()["shots"][0]["action"] == "Walk across the room"
    assert "test-only-credential" not in response.text
    remote_server["story"] = {"shots": [{"action": "unvalidated"}]}
    assert client.post(path, json={**body, "allow_remote": True}).status_code == 422


@pytest.mark.media
@pytest.mark.parametrize(
    "kind,operation,media_type",
    [
        ("image", "generate_image", "image"),
        ("speech", "generate_speech", "audio"),
    ],
)
def test_auxiliary_media_enters_real_asset_store(
    client, remote_server, kind, operation, media_type
):
    from app.execution.worker import Worker

    configure(client, remote_server, kind)
    apr = project(client)
    pid = apr["project_id"]
    body = {
        "base_revision": apr["revision"],
        "provider_id": f"openai-{kind}",
        "operation": operation,
        "prompt": "A rainy station",
        "allow_remote": True,
    }
    response = client.post(f"/api/v1/projects/{pid}/media", json=body)
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(f"/api/v1/projects/{pid}/jobs/{response.json()['id']}").json()
    assert job["state"] == "completed", job
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    asset = apr["assets"][0]
    assert asset["media_type"] == media_type
    assert asset["provenance"]["is_mock"] is False
    assert asset["license"] == "unverified"
    assert "test-only-credential" not in json.dumps(apr)
    assert client.get(f"/api/v1/projects/{pid}/assets/{asset['id']}/file").status_code == 200


@pytest.mark.media
@pytest.mark.parametrize("resume", [False, True])
def test_video_submit_resume_and_normalization(client, remote_server, tmp_path, resume):
    from sqlalchemy import select, update

    from app import db
    from app.execution.queue import shot_request
    from app.execution.worker import Worker
    from app.media import MockVideo

    configure(client, remote_server, "video", video_seconds=8)
    apr = storyboard(client)
    pid, shot = apr["project_id"], apr["shots"][0]
    fixture = MockVideo(client.app.state.settings).generate(
        shot_request(apr, shot), tmp_path / "fixture.mp4", lambda: True
    )
    remote_server["video_bytes"] = fixture["video"].read_bytes()
    response = client.post(
        f"/api/v1/projects/{pid}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [shot["id"]],
            "provider_id": "openai-video",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()[0]["id"]
    database = client.app.state.database
    # Recover an externally accepted task without submitting again.
    if resume:
        with database.transaction() as connection:
            payload = connection.execute(
                select(db.jobs.c.payload).where(db.jobs.c.id == job_id)
            ).scalar_one()
            payload.update(submission_started=True, external_task_id="video-contract-123")
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job_id).values(payload=payload)
            )
    assert Worker(database).run_once()
    job = client.get(f"/api/v1/projects/{pid}/jobs/{job_id}").json()
    assert job["state"] == "completed", job
    submissions = [r for r in remote_server["requests"] if r["method"] == "POST"]
    assert len(submissions) == (0 if resume else 1)
    if submissions:
        assert b"test-model" in submissions[0]["body"]
        assert b'name="seconds"' in submissions[0]["body"]
    result = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert result["shots"][0]["status"] == "ready"
    assert result["assets"][0]["duration_frames"] == 120
    assert result["assets"][0]["provenance"]["is_mock"] is False


def test_ambiguous_remote_submission_never_auto_resubmits(client, remote_server):
    from sqlalchemy import select, update

    from app import db
    from app.execution.worker import Worker

    configure(client, remote_server, "video")
    apr = storyboard(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "provider_id": "openai-video",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()[0]["id"]
    database = client.app.state.database
    with database.transaction() as connection:
        payload = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == job_id)
        ).scalar_one()
        payload["submission_started"] = True
        connection.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(payload=payload))
    Worker(database).run_once()
    job = client.get(f"/api/v1/projects/{pid}/jobs/{job_id}").json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "REMOTE_SUBMISSION_UNCERTAIN"
    assert remote_server["requests"] == []


def test_remote_cancel_reports_local_only_and_preserves_external_id(client, remote_server):
    import time

    from app.execution.queue import Queue
    from app.execution.worker import Worker

    configure(client, remote_server, "video")
    remote_server["video_status"] = "queued"
    apr = storyboard(client)
    pid = apr["project_id"]
    job = client.post(
        f"/api/v1/projects/{pid}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [apr["shots"][0]["id"]],
            "provider_id": "openai-video",
            "allow_remote": True,
        },
    ).json()[0]
    worker = Worker(client.app.state.database)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while remote_server["polls"] == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert remote_server["polls"] > 0
        Queue(client.app.state.database).cancel(pid, job["id"])
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()
    current = client.get(f"/api/v1/projects/{pid}/jobs/{job['id']}").json()
    assert current["state"] == "cancelled"
    assert current["external_task_id"] == "video-contract-123"
    assert current["remote_cancel_supported"] is False
    assert current["remote_notice"]
