import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from test_audio_analysis import analysis_film  # noqa: F401
from test_provider_settings import save_provider

from app.execution.worker import Worker


@pytest.fixture
def transcription_server():
    state = {
        "requests": [],
        "status": 200,
        "response": {
            "language": "en",
            "segments": [
                {"start": 0.25, "end": 1.0, "text": "DEMO first"},
                {"start": 1.25, "end": 2.0, "text": "DEMO second"},
            ],
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append({"path": self.path, "body": raw})
            payload = json.dumps(state["response"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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


def queue_transcription(client, root, apr, asset_id, remote, **changes):
    saved = save_provider(
        client,
        "transcription",
        base_url=remote["base_url"],
        allow_private_network=True,
        transcription_timestamps=True,
    )
    assert saved.status_code == 200, saved.text
    return client.post(
        f"{root}/timeline/analysis",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "kind": "transcription",
            "asset_id": asset_id,
            "duration_frames": 96,
            "language": "en",
            "allow_remote": True,
            **changes,
        },
    )


def test_transcription_preserves_manual_captions(request, transcription_server):
    client, root, apr, asset_id = request.getfixturevalue("analysis_film")
    manual = {"id": str(uuid4()), "start_frame": 0, "duration_frames": 12, "text": "Manual"}
    saved = client.put(
        f"{root}/timeline/composition",
        json={
            "base_revision": apr["revision"],
            "captions": [manual],
            "audio_tracks": [],
            "markers": [],
        },
    )
    assert saved.status_code == 200, saved.text
    apr = saved.json()
    response = queue_transcription(client, root, apr, asset_id, transcription_server)
    assert response.status_code == 202, response.text
    job = response.json()
    assert Worker(client.app.state.database).run_once()
    result = client.get(f"{root}/timeline/analysis/{job['id']}")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["segments"][0]["start_ms"] == 250
    request = transcription_server["requests"][0]
    assert request["path"] == "/v1/audio/transcriptions"
    assert b'name="file"' in request["body"] and b"RIFF" in request["body"]
    assert b"verbose_json" in request["body"] and b"test-model" in request["body"]
    apply = {"base_revision": apr["revision"], "selected_indices": [0, 1]}
    preview = client.post(f"{root}/timeline/analysis/{job['id']}/preview", json=apply)
    assert preview.status_code == 200, preview.text
    assert preview.json()["remove_captions"] == []
    saved = client.post(
        f"{root}/timeline/analysis/{job['id']}/commit",
        json={
            **apply,
            "approved": True,
            "preview_token": preview.json()["preview_token"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["timeline"]["captions"][0] == manual
    assert len(saved.json()["timeline"]["captions"]) == 3


@pytest.mark.parametrize(
    "segments",
    [
        [{"text": "missing timestamps"}],
        [{"start": 2, "end": 1, "text": "reverse"}],
        [{"start": 0, "end": 5, "text": "outside source"}],
        [{"start": 0, "end": 1, "text": "one"}, {"start": 0.5, "end": 2, "text": "overlap"}],
        [{"start": 0, "end": 1, "text": "too many"}] * 501,
        [{"start": -0.0000001, "end": 1, "text": "negative start"}],
    ],
)
def test_transcription_rejects_invalid_timestamp_results(request, transcription_server, segments):
    client, root, apr, asset_id = request.getfixturevalue("analysis_film")
    transcription_server["response"]["segments"] = segments
    response = queue_transcription(client, root, apr, asset_id, transcription_server)
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(f"{root}/jobs/{response.json()['id']}").json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "TRANSCRIPTION_TIMESTAMPS_INVALID"
    assert client.get(root).json()["timeline"]["captions"] == []


def test_transcription_requires_per_request_remote_consent(request, transcription_server):
    client, root, apr, asset_id = request.getfixturevalue("analysis_film")
    response = queue_transcription(
        client, root, apr, asset_id, transcription_server, allow_remote=False
    )
    assert response.status_code == 409 and not transcription_server["requests"]


def test_uncertain_transcription_retains_charge_and_never_automatically_resubmits(
    request, transcription_server
):
    client, root, apr, asset_id = request.getfixturevalue("analysis_film")
    transcription_server["status"] = 500
    response = queue_transcription(client, root, apr, asset_id, transcription_server)
    assert response.status_code == 202, response.text
    database = client.app.state.database
    assert Worker(database).run_once()
    job_id = response.json()["id"]
    job = client.get(f"{root}/jobs/{job_id}").json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "REMOTE_SUBMISSION_UNCERTAIN"
    assert job["submission_started"] is True
    summary = client.get(f"{root}/budget/summary").json()
    assert summary["unknown_count"] == 1
    retried = client.post(f"{root}/jobs/{job_id}/retry", json={})
    assert retried.status_code == 200, retried.text
    assert Worker(database).run_once()
    assert len(transcription_server["requests"]) == 1


def test_caption_replacement_is_bound_to_the_approved_selection(request, transcription_server):
    client, root, apr, asset_id = request.getfixturevalue("analysis_film")
    manual = {"id": str(uuid4()), "start_frame": 0, "duration_frames": 12, "text": "Manual"}
    apr = client.put(
        f"{root}/timeline/composition",
        json={
            "base_revision": apr["revision"],
            "captions": [manual],
            "audio_tracks": [],
            "markers": [],
        },
    ).json()
    response = queue_transcription(client, root, apr, asset_id, transcription_server)
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    route = f"{root}/timeline/analysis/{response.json()['id']}"
    body = {"base_revision": apr["revision"], "selected_indices": [0]}
    preview = client.post(f"{route}/preview", json=body).json()
    changed = {**body, "replace_caption_ids": [manual["id"]]}
    refused = client.post(
        f"{route}/commit",
        json={**changed, "approved": True, "preview_token": preview["preview_token"]},
    )
    assert refused.status_code == 409
    preview = client.post(f"{route}/preview", json=changed).json()
    assert preview["remove_captions"] == [manual]
    saved = client.post(
        f"{route}/commit",
        json={**changed, "approved": True, "preview_token": preview["preview_token"]},
    )
    assert saved.status_code == 200, saved.text
    assert [c["text"] for c in saved.json()["timeline"]["captions"]] == ["DEMO first"]


def test_large_normalized_audio_is_compressed_before_submission(
    request, transcription_server, monkeypatch
):
    from app import analysis_service

    client, root, apr, asset_id = request.getfixturevalue("analysis_film")
    monkeypatch.setattr(analysis_service, "TRANSCRIPTION_WAV_LIMIT", 64 * 1024)
    queued = queue_transcription(client, root, apr, asset_id, transcription_server)
    assert queued.status_code == 202, queued.text
    assert Worker(client.app.state.database).run_once()
    job_id = queued.json()["id"]
    result = client.get(f"{root}/timeline/analysis/{job_id}")
    assert result.status_code == 200, result.text
    assert b"audio/mpeg" in transcription_server["requests"][0]["body"]
