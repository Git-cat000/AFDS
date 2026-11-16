import copy
import importlib
import wave
from uuid import uuid4

import numpy as np
import pytest
from conftest import storyboard

from app.execution.queue import Queue
from app.execution.worker import Worker


@pytest.fixture
def analysis_film(client, tmp_path):
    apr = storyboard(client)
    path = tmp_path / "DEMO-clicks.wav"
    samples = np.zeros(16000 * 4, dtype=np.int16)
    for offset in range(0, len(samples), 8000):
        samples[offset : offset + 100] = 24000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.tobytes())
    root = f"/api/v1/projects/{apr['project_id']}"
    imported = client.post(
        f"{root}/timeline/audio/import?base_revision={apr['revision']}&rights_confirmed=true",
        content=path.read_bytes(),
    )
    assert imported.status_code == 202, imported.text
    assert Worker(client.app.state.database).run_once()
    apr = client.get(root).json()
    return client, root, apr, apr["assets"][0]["id"]


def test_click_track_frames_within_one_frame():
    module = importlib.import_module("app.audio_analysis")
    samples = np.zeros(16000 * 5, dtype=np.float32)
    for second in np.arange(0.25, 5, 0.5):
        start = round(second * 16000)
        samples[start : start + 80] = 0.8
    for numerator, denominator in [(24, 1), (30000, 1001)]:
        result = module.detect_beats(samples, 16000, numerator, denominator)
        expected = [round(s * numerator / denominator) for s in np.arange(0.25, 5, 0.5)]
        assert len(result.frames) == len(expected)
        assert max(abs(a - b) for a, b in zip(result.frames, expected, strict=True)) <= 1
        assert result.bpm == pytest.approx(120, abs=1)


def test_silence_has_no_beats():
    module = importlib.import_module("app.audio_analysis")
    result = module.detect_beats(np.zeros(16000), 16000, 24, 1)
    assert result.frames == [] and result.bpm is None
    with pytest.raises(ValueError):
        module.detect_beats(np.array([float("nan")]), 16000, 24, 1)


def test_local_analysis_publishes_owned_result_and_requires_approval(analysis_film):
    client, root, apr, asset_id = analysis_film
    body = {
        "base_revision": apr["revision"],
        "request_id": str(uuid4()),
        "kind": "beats",
        "asset_id": asset_id,
        "duration_frames": 96,
    }
    queued = client.post(f"{root}/timeline/analysis", json=body)
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert client.post(f"{root}/timeline/analysis", json=body).json()["id"] == job["id"]
    database = client.app.state.database
    assert Worker(database).run_once()
    with database.engine.connect() as connection:
        result = Queue(database).get(connection, apr["project_id"], job["id"])
    assert result["state"] == "completed", result.get("error")
    assert client.get(root).json()["revision"] == apr["revision"]
    response = client.get(f"{root}/timeline/analysis/{job['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["result"]["frames"] == list(range(0, 96, 12))
    assert "relative_path" not in response.text
    apply = {"base_revision": apr["revision"], "selected_indices": [1, 2]}
    preview = client.post(f"{root}/timeline/analysis/{job['id']}/preview", json=apply)
    assert preview.status_code == 200, preview.text
    assert preview.json()["add_markers"][0]["frame"] == 12
    refused = client.post(f"{root}/timeline/analysis/{job['id']}/commit", json=apply)
    assert refused.status_code == 409
    saved = client.post(
        f"{root}/timeline/analysis/{job['id']}/commit",
        json={**apply, "approved": True, "preview_token": preview.json()["preview_token"]},
    )
    assert saved.status_code == 200, saved.text
    assert [m["frame"] for m in saved.json()["timeline"]["markers"]] == [12, 24]
    stale = client.post(
        f"{root}/timeline/analysis/{job['id']}/commit",
        json={**apply, "approved": True, "preview_token": preview.json()["preview_token"]},
    )
    assert stale.status_code == 409


def test_anchor_snap_accounts_for_transition_overlap():
    from test_audio_timeline import apr_fixture

    from app.composition import position_backbone
    from app.timeline_models import stable_clip_id
    from app.timeline_service import AnchorRequest, preview_anchors

    apr = apr_fixture()
    clips = apr["timeline"]["tracks"][0]["items"]
    for clip in clips:
        clip["id"] = str(stable_clip_id(apr["project_id"], clip["shot_id"]))
    apr["timeline"]["transitions"] = [
        {
            "id": str(uuid4()),
            "from_clip_id": clips[0]["id"],
            "to_clip_id": clips[1]["id"],
            "kind": "dissolve",
            "duration_frames": 8,
        }
    ]
    position_backbone(apr["timeline"])
    apr["timeline"]["markers"] = [
        {"id": str(uuid4()), "frame": 24, "label": "beat", "kind": "beat"}
    ]
    original = copy.deepcopy(apr)
    plan = preview_anchors(
        apr, AnchorRequest(base_revision=1, boundary_shot_ids=[clips[0]["shot_id"]])
    )
    assert plan["total_frames"] == 64
    assert plan["boundaries"] == [
        {"shot_id": clips[0]["shot_id"], "before_frame": 16, "after_frame": 24}
    ]
    assert {v["shot_id"]: v["after"] for v in plan["changes"]} == {
        clips[0]["shot_id"]: 32,
        clips[1]["shot_id"]: 16,
    }
    assert apr == original


def test_analysis_result_is_project_owned_and_integrity_checked(analysis_film):
    from conftest import project

    from app.storage import contained

    client, root, apr, asset_id = analysis_film
    queued = client.post(
        f"{root}/timeline/analysis",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "kind": "beats",
            "asset_id": asset_id,
            "duration_frames": 96,
        },
    )
    assert queued.status_code == 202, queued.text
    database = client.app.state.database
    assert Worker(database).run_once()
    job_id = queued.json()["id"]
    other = project(client)
    assert (
        client.get(f"/api/v1/projects/{other['project_id']}/timeline/analysis/{job_id}").status_code
        == 404
    )
    with database.engine.connect() as connection:
        job = Queue(database).get(connection, apr["project_id"], job_id)
    result = contained(database.settings.data_dir, job["analysis_result"]["relative_path"])
    result.write_bytes(b"tampered")
    assert client.get(f"{root}/timeline/analysis/{job_id}").status_code == 422
    assert (
        client.post(
            f"{root}/timeline/analysis/{job_id}/preview",
            json={
                "base_revision": apr["revision"],
                "selected_indices": [0],
            },
        ).status_code
        == 422
    )


def test_cancelled_analysis_never_publishes_a_result(analysis_film, monkeypatch):
    from app.analysis_service import AnalysisService

    client, root, apr, asset_id = analysis_film
    queued = client.post(
        f"{root}/timeline/analysis",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "kind": "beats",
            "asset_id": asset_id,
            "duration_frames": 96,
        },
    )
    assert queued.status_code == 202, queued.text
    original = AnalysisService.publish

    def cancelled(service, job, owner, result, elapsed):
        service.queue.cancel(job["project_id"], job["id"])
        return original(service, job, owner, result, elapsed)

    monkeypatch.setattr(AnalysisService, "publish", cancelled)
    assert Worker(client.app.state.database).run_once()
    job = client.get(f"{root}/jobs/{queued.json()['id']}").json()
    assert job["state"] == "cancelled" and "analysis_result" not in job
    assert client.get(root).json()["revision"] == apr["revision"]


def test_analysis_requires_the_source_frame_clock(analysis_film):
    from app.project_engine import ProjectEngine

    client, root, apr, asset_id = analysis_film
    queued = client.post(
        f"{root}/timeline/analysis",
        json={
            "base_revision": apr["revision"],
            "request_id": str(uuid4()),
            "kind": "beats",
            "asset_id": asset_id,
            "duration_frames": 96,
        },
    )
    assert queued.status_code == 202, queued.text
    database = client.app.state.database
    assert Worker(database).run_once()

    def change_clock(value, connection):
        value["format"]["fps_num"] = 30

    after = ProjectEngine(database).mutate(
        apr["project_id"], apr["revision"], "Change clock", change_clock
    )
    preview = client.post(
        f"{root}/timeline/analysis/{queued.json()['id']}/preview",
        json={
            "base_revision": after["revision"],
            "selected_indices": [1],
        },
    )
    assert preview.status_code == 409
    assert preview.json()["error"]["code"] == "ANALYSIS_STALE"
