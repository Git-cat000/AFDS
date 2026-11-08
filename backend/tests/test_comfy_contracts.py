import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import project, storyboard
from PIL import Image
from test_provider_settings import save_provider


def template(media="image"):
    return {
        "graph": {
            "1": {"class_type": "Text", "inputs": {"text": "old"}},
            "2": {"class_type": "Save", "inputs": {"source": ["1", 0]}},
        },
        "bindings": {"prompt": {"node_id": "1", "input_name": "text"}},
        "output_node": "2",
        "output_field": "images" if media == "image" else "gifs",
        "media_type": media,
    }


@pytest.fixture
def comfy_server():
    buf = io.BytesIO()
    Image.new("RGB", (160, 90), "#445566").save(buf, "PNG")
    state = {
        "requests": [],
        "output": buf.getvalue(),
        "missing": False,
        "filename": "output.png",
        "output_field": "images",
        "failed": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.command, self.path, json.loads(raw) if raw else None))
            path = urlsplit(self.path).path
            if path == "/object_info":
                data = {
                    "Text": {"input": {"required": {"text": ["STRING", {}]}}},
                    "Save": {"output_node": not state["missing"]},
                }
            elif path == "/prompt":
                data = {"prompt_id": "comfy-task-123", "number": 1, "node_errors": {}}
            elif path == "/history/comfy-task-123":
                data = {
                    "comfy-task-123": {
                        "status": {
                            "completed": not state["failed"],
                            "status_str": "error" if state["failed"] else "success",
                        },
                        "outputs": {
                            "2": {
                                state["output_field"]: [
                                    {
                                        "filename": state["filename"],
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
            elif path == "/view":
                assert parse_qs(urlsplit(self.path).query)["type"] == ["output"]
                data = state["output"]
            else:
                self.send_error(404)
                return
            payload = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def configure(client, state, kind="image", **changes):
    response = save_provider(
        client,
        kind,
        base_url=state["url"],
        api_key="",
        allow_private_network=True,
        protocol="comfyui",
        workflow=template("image" if kind == "image" else "video"),
        **changes,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_workflow_health_checks_nodes_without_submitting(client, comfy_server):
    configure(client, comfy_server)
    result = client.post("/api/v1/settings/providers/image/test", json={})
    assert result.status_code == 200, result.text
    assert result.json()["capabilities"] == []
    assert result.json()["provisional_capabilities"] == ["text_to_image"]
    assert result.json()["generation_tested"] is False
    assert not any(r[0] == "POST" for r in comfy_server["requests"])
    comfy_server["missing"] = True
    assert client.post("/api/v1/settings/providers/image/test", json={}).status_code == 422


@pytest.mark.media
@pytest.mark.parametrize("resume", [False, True])
def test_comfy_image_maps_prompt_imports_and_resumes(client, comfy_server, resume):
    from sqlalchemy import update

    from app import db
    from app.execution.worker import Worker

    configure(client, comfy_server)
    apr = project(client)
    pid = apr["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/media",
        json={
            "base_revision": apr["revision"],
            "operation": "generate_image",
            "prompt": "A station",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    if resume:
        payload = {
            k: v
            for k, v in job.items()
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
        }
        payload.update(external_task_id="comfy-task-123", submission_started=True)
        with client.app.state.database.transaction() as connection:
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
            )
    assert Worker(client.app.state.database).run_once()
    result = client.get(f"/api/v1/projects/{pid}/jobs/{job['id']}").json()
    assert result["state"] == "completed", result
    submits = [r for r in comfy_server["requests"] if r[0] == "POST"]
    assert len(submits) == (0 if resume else 1)
    if not resume:
        assert submits[0][2]["prompt"]["1"]["inputs"]["text"] == "A station"
    asset = client.get(f"/api/v1/projects/{pid}/apr").json()["assets"][0]
    assert asset["media_type"] == "image"
    assert asset["provenance"]["is_mock"] is False
    assert asset["provenance"]["protocol"] == "comfyui"
    ready = next(
        p for p in client.get("/api/v1/models").json() if p["provider_id"] == "openai-image"
    )
    assert ready["availability"] == "available"
    assert ready["capabilities"] == ["text_to_image"]


@pytest.mark.parametrize(
    "change", [{"output_node": "99"}, {"bindings": {}}, {"media_type": "audio"}]
)
def test_invalid_workflow_is_rejected(client, comfy_server, change):
    body = {**template(), **change}
    response = save_provider(
        client,
        "image",
        base_url=comfy_server["url"],
        api_key="",
        allow_private_network=True,
        protocol="comfyui",
        workflow=body,
    )
    assert response.status_code == 422


def test_comfy_rejects_path_traversal_and_keeps_pending_charge(client, comfy_server):
    from app.execution.worker import Worker

    configure(client, comfy_server)
    comfy_server["filename"] = "../secret.png"
    apr = project(client)
    pid = apr["project_id"]
    job = client.post(
        f"/api/v1/projects/{pid}/media",
        json={
            "base_revision": apr["revision"],
            "operation": "generate_image",
            "prompt": "A station",
            "allow_remote": True,
        },
    ).json()
    Worker(client.app.state.database).run_once()
    result = client.get(f"/api/v1/projects/{pid}/jobs/{job['id']}").json()
    assert result["state"] == "failed", result
    assert result["error"]["code"] == "PROVIDER_OUTPUT_INVALID"
    assert not any(r[1].startswith("/view") for r in comfy_server["requests"])
    assert client.get(f"/api/v1/projects/{pid}/budget").json()["entries"][0]["state"] == "pending"


@pytest.mark.media
def test_comfy_video_is_normalized_and_recoverable(client, comfy_server, tmp_path):
    from app.execution.queue import shot_request
    from app.execution.worker import Worker
    from app.media import MockVideo

    configure(client, comfy_server, "video")
    apr = storyboard(client)
    source = tmp_path / "source.mp4"
    MockVideo(client.app.state.settings).generate(
        shot_request(apr, apr["shots"][0]), source, lambda: True
    )
    comfy_server.update(output=source.read_bytes(), filename="video.mp4", output_field="gifs")
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
    Worker(client.app.state.database).run_once()
    result = client.get(f"/api/v1/projects/{pid}/jobs/{job['id']}").json()
    assert result["state"] == "completed", result
    asset = client.get(f"/api/v1/projects/{pid}/apr").json()["assets"][0]
    assert asset["duration_frames"] == 120
    assert asset["provenance"]["protocol"] == "comfyui"
    assert asset["provenance"]["normalization"]["audio_policy"] == "silent"


def test_comfy_is_not_advertised_ready_until_template_checked(client, comfy_server):
    configure(client, comfy_server)
    providers = client.get("/api/v1/models").json()
    entry = next(p for p in providers if p["provider_id"] == "openai-image")
    assert entry["availability"] == "unverified"
    assert entry["capabilities"] == []
    client.post("/api/v1/settings/providers/image/test", json={})
    entry = next(
        p for p in client.get("/api/v1/models").json() if p["provider_id"] == "openai-image"
    )
    assert entry["availability"] == "unverified"
    assert entry["capabilities"] == []
    assert entry["provisional_capabilities"] == ["text_to_image"]
