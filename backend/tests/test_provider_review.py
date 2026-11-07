import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from conftest import storyboard
from sqlalchemy import select, update
from test_provider_settings import save_provider

from app import db
from app.provider_http import ProviderError, ProviderHTTP
from app.remote_media import generate_image


def test_http_connection_uses_validated_ip_and_preserves_host_and_sni(monkeypatch):
    from app import provider_http

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handle)
    sync_client, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(
        provider_http.httpx, "Client", lambda **kwargs: sync_client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(
        provider_http.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        provider_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ],
    )
    ProviderHTTP(
        {
            "base_url": "https://provider.example/v1",
            "allow_private_network": False,
            "api_key": "test-only-key",
            "timeout_seconds": 5,
        }
    ).json("GET", "models")
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].headers["host"] == "provider.example"
    assert requests[0].extensions["sni_hostname"] == "provider.example"


def test_total_deadline_stops_a_trickling_response():
    class SlowHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "12")
            self.end_headers()
            try:
                for chunk in b"          {}":
                    self.wfile.write(bytes([chunk]))
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        config = {
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "allow_private_network": True,
            "api_key": "",
            "timeout_seconds": 0.25,
        }
        with pytest.raises(ProviderError, match="timed out"):
            ProviderHTTP(config).json("GET", "models")
        assert time.monotonic() - started < 0.9
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_noop_save_preserves_provider_revision_and_cached_identity(client):
    first = save_provider(client).json()
    again = save_provider(client, revision=1, api_key="").json()
    assert again["revision"] == first["revision"]
    same_key = save_provider(client, revision=1).json()
    assert same_key["revision"] == first["revision"]


def test_malformed_image_is_a_sanitized_provider_error(tmp_path):
    class MalformedProvider:
        def json(self, *args, **kwargs):
            return {"data": [None]}

    with pytest.raises(ProviderError) as error:
        generate_image(
            MalformedProvider(),
            {"model": "test-model", "image_size": "1024x1024"},
            {"prompt": "Test"},
            tmp_path,
            lambda: True,
        )
    assert error.value.code == "PROVIDER_OUTPUT_INVALID"


def test_terminal_remote_retry_requires_explicit_new_submission_consent(client):
    save_provider(client, "video")
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
    database = client.app.state.database
    with database.transaction() as connection:
        payload = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
        ).scalar_one()
        payload.update(
            external_task_id="failed-video-123",
            submission_started=True,
            error={"code": "PROVIDER_JOB_FAILED", "message": "Remote task failed"},
        )
        connection.execute(
            update(db.jobs).where(db.jobs.c.id == job["id"]).values(state="failed", payload=payload)
        )
    path = f"/api/v1/projects/{pid}/jobs/{job['id']}/retry"
    assert client.post(path, json={}).status_code == 409
    assert client.post(path, json={"restart_remote": True}).status_code == 409
    restarted = client.post(path, json={"restart_remote": True, "allow_remote": True})
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["state"] == "queued"
    assert restarted.json()["external_task_id"] is None
    assert restarted.json()["submission_started"] is False
    assert restarted.json()["remote_attempts"][0]["external_task_id"] == "failed-video-123"
