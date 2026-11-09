import copy
import importlib.util
import math
import struct
import wave
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.errors import DomainError
from app.media import MockVideo, run_process


def domain():
    assert importlib.util.find_spec("app.timeline_service"), "Timeline/audio domain is missing"
    from app import timeline_service

    return timeline_service


def apr_fixture():
    scene_id = str(uuid4())
    shots = [
        {
            "id": str(uuid4()),
            "scene_id": scene_id,
            "duration_frames": 24,
            "status": "ready",
            "locked": False,
        }
        for _ in range(3)
    ]
    aid = str(uuid4())
    return {
        "project_id": str(uuid4()),
        "revision": 1,
        "format": {"fps_num": 24, "fps_den": 1, "width": 320, "height": 180},
        "shots": shots,
        "scenes": [{"id": scene_id, "location_id": None}],
        "assets": [{"id": aid, "media_type": "audio", "sha256": "a" * 64, "duration_seconds": 2}],
        "timeline": {
            "version": 1,
            "tracks": [
                {
                    "id": "v1",
                    "type": "video",
                    "items": [
                        {"shot_id": s["id"], "start_frame": i * 24, "duration_frames": 24}
                        for i, s in enumerate(shots)
                    ],
                },
                {
                    "id": "music",
                    "type": "audio",
                    "items": [
                        {
                            "asset_id": aid,
                            "start_frame": 12,
                            "source_start_frame": 12,
                            "duration_frames": 24,
                            "gain_db": -6,
                        }
                    ],
                },
            ],
            "captions": [],
            "markers": [],
        },
    }


@pytest.mark.parametrize(
    "failure",
    ["foreign", "overrun", "nonfinite", "gap", "wrong_track", "duplicate_track", "caption_overrun"],
)
def test_rejects_invalid_composition_references_and_bounds(failure):
    service = domain()
    apr = apr_fixture()
    item = apr["timeline"]["tracks"][1]["items"][0]
    if failure == "foreign":
        item["asset_id"] = str(uuid4())
    elif failure == "overrun":
        item["source_start_frame"] = 25
    elif failure == "nonfinite":
        item["gain_db"] = math.inf
    elif failure == "gap":
        apr["timeline"]["tracks"][0]["items"][1]["start_frame"] += 1
    elif failure == "wrong_track":
        apr["timeline"]["tracks"][1]["items"] = apr["timeline"]["tracks"][0]["items"]
    elif failure == "duplicate_track":
        apr["timeline"]["tracks"][1]["id"] = "v1"
    else:
        apr["timeline"]["captions"] = [
            {"id": str(uuid4()), "start_frame": 70, "duration_frames": 24, "text": "hello"}
        ]
    with pytest.raises(ValueError):
        service.validate_timeline(apr)


def test_composition_cache_contains_trim_gain_mute_text_and_hash():
    service = domain()
    apr = apr_fixture()
    service.validate_timeline(apr)
    original = service.composition_request(apr)
    item = original["audio_items"][0]
    assert item["sha256"] == "a" * 64
    assert item["source_start_frame"] == 12
    assert item["gain_db"] == -6
    apr["timeline"]["tracks"][1]["muted"] = True
    assert service.composition_request(apr) != original
    apr["timeline"]["tracks"][1]["muted"] = False
    apr["timeline"]["captions"] = [
        {"id": str(uuid4()), "start_frame": 0, "duration_frames": 24, "text": "hello"}
    ]
    assert service.composition_request(apr) != original


def test_audio_anchors_preview_protects_shots_and_requires_same_revision():
    service = domain()
    apr = apr_fixture()
    apr["shots"][0]["locked"] = True
    apr["timeline"]["markers"] = [
        {"id": str(uuid4()), "frame": 30, "label": "line", "kind": "dialogue"}
    ]
    body = service.AnchorRequest(base_revision=1, boundary_shot_ids=[apr["shots"][0]["id"]])
    preview = service.preview_anchors(apr, body)
    assert not preview["changes"]
    assert apr["shots"][0]["id"] in preview["protected_shot_ids"]
    body = service.AnchorRequest(
        base_revision=1, boundary_shot_ids=[apr["shots"][1]["id"]], bpm=100
    )
    before = copy.deepcopy(apr)
    preview = service.preview_anchors(apr, body)
    assert preview["changes"]
    assert apr == before
    assert sum(x["after"] for x in preview["changes"]) == sum(
        x["before"] for x in preview["changes"]
    )
    apr["revision"] += 1
    with pytest.raises(DomainError, match="Project changed"):
        service.preview_anchors(apr, body)


def sine(path, frequency=440, seconds=2, amplitude=0.3):
    rate = 48000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(
            b"".join(
                struct.pack(
                    "<h", int(amplitude * 32767 * math.sin(2 * math.pi * frequency * i / rate))
                )
                for i in range(int(seconds * rate))
            )
        )


def test_audio_import_probes_actual_media_and_rejects_foreign_staging(tmp_path):
    domain()
    from app.audio_pipeline import process_audio_import

    settings = Settings(data_dir=tmp_path)
    pid = str(uuid4())
    relative = Path("uploads") / pid / f"{uuid4()}.upload"
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    sine(source)
    work = tmp_path / "work"
    work.mkdir()
    request = {"project_id": pid, "staging_path": relative.as_posix(), "rights_confirmed": True}
    result = process_audio_import(settings, request, work, lambda: True)
    assert result["media_type"] == "audio"
    assert result["metadata"]["duration_seconds"] == pytest.approx(2, abs=0.01)
    assert result["metadata"]["sample_rate"] == 48000
    assert result["video"].suffix == ".wav"
    with pytest.raises(DomainError):
        process_audio_import(settings, {**request, "project_id": str(uuid4())}, work, lambda: True)
    source.write_text("not really audio")
    with pytest.raises(DomainError):
        process_audio_import(settings, request, work, lambda: True)


def test_real_mix_has_aac_delay_trim_gain_silence_and_exact_video_frames(tmp_path):
    domain()
    from app.audio_pipeline import finish_audio, probe_audio

    settings = Settings(data_dir=tmp_path)
    apr = apr_fixture()
    video = tmp_path / "silent.mp4"
    MockVideo(settings).generate(
        {
            "format": apr["format"],
            "order": 0,
            "camera": {"shot_size": "wide"},
            "duration_frames": 72,
        },
        video,
        lambda: True,
    )
    source = tmp_path / "source.wav"
    sine(source)
    request = {
        "format": apr["format"],
        "items": apr["timeline"]["tracks"][0]["items"],
        **domain().composition_request(apr),
    }
    output = tmp_path / "mixed.mp4"
    metadata = finish_audio(settings, request, [source], video, output, lambda: True)
    assert metadata["duration_frames"] == 72
    audio = probe_audio(settings, output, lambda: True)
    assert audio["codec"] == "aac"
    assert audio["sample_rate"] == 48000
    assert audio["channels"] == 2
    raw = run_process(
        [
            settings.binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        30,
        lambda: True,
    )
    samples = struct.unpack(f"<{len(raw) // 4}f", raw)

    def rms(start, end):
        segment = samples[int(start * 48000) : int(end * 48000)]
        return math.sqrt(sum(x * x for x in segment) / len(segment))

    assert rms(0.1, 0.4) < 0.001
    assert 0.06 < rms(0.7, 1.3) < 0.12
    assert rms(1.8, 2.8) < 0.001


def test_caption_text_is_literal_and_audio_mute_is_respected(tmp_path):
    domain()
    from app.audio_pipeline import finish_audio, probe_audio

    settings = Settings(data_dir=tmp_path)
    apr = apr_fixture()
    apr["timeline"]["tracks"][1]["muted"] = True
    apr["timeline"]["captions"] = [
        {
            "id": str(uuid4()),
            "start_frame": 0,
            "duration_frames": 72,
            "text": r"中文 {\pos(1,1)} ' : [x]; movie=evil\Nnext",
        }
    ]
    video = tmp_path / "silent.mp4"
    MockVideo(settings).generate(
        {
            "format": apr["format"],
            "order": 0,
            "camera": {"shot_size": "wide"},
            "duration_frames": 72,
        },
        video,
        lambda: True,
    )
    source = tmp_path / "source.wav"
    sine(source)
    request = {
        "format": apr["format"],
        "items": apr["timeline"]["tracks"][0]["items"],
        **domain().composition_request(apr),
    }
    result = finish_audio(
        settings, request, [source], video, tmp_path / "captioned.mp4", lambda: True
    )
    assert result["duration_frames"] == 72
    assert probe_audio(settings, tmp_path / "captioned.mp4", lambda: True)["codec"] == "aac"
    subtitle = next(tmp_path.glob("captions-*.ass")).read_text(encoding="utf-8")
    assert r"{\pos(1,1)}" not in subtitle
    raw = run_process(
        [
            settings.binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(tmp_path / "captioned.mp4"),
            "-vn",
            "-f",
            "f32le",
            "pipe:1",
        ],
        30,
        lambda: True,
    )
    values = struct.unpack(f"<{len(raw) // 4}f", raw)
    assert max(abs(x) for x in values) < 0.001


def mount_timeline(client):
    assert importlib.util.find_spec("app.timeline_api"), "Timeline API is missing"
    from app.timeline_api import router

    client.app.include_router(router, prefix="/api/v1")


def test_import_api_requires_rights_and_revision_and_only_enqueues(client, tmp_path):
    mount_timeline(client)
    from conftest import project

    created = project(client)
    source = tmp_path / "source.wav"
    sine(source, seconds=0.1)
    base = f"/api/v1/projects/{created['project_id']}/timeline/audio/import"
    query = {"base_revision": 1, "name": "music.wav", "rights_confirmed": False}
    response = client.post(
        base,
        params=query,
        content=source.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 422
    response = client.post(
        base,
        params={**query, "rights_confirmed": True},
        content=source.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["operation"] == "import_audio"
    assert job["state"] == "queued"
    assert "staging_path" not in job.get("request", {})
    assert not client.get(f"/api/v1/projects/{created['project_id']}/apr").json()["assets"]
    assert (
        client.post(base, params={**query, "rights_confirmed": True}, content=b"x").status_code
        == 409
    )


def test_composition_service_keeps_shot_assets_and_rejects_stale_revision(client):
    mount_timeline(client)
    from conftest import storyboard

    apr = storyboard(client)
    pid = apr["project_id"]
    before = copy.deepcopy(apr["shots"])
    url = f"/api/v1/projects/{pid}/timeline/composition"
    response = client.put(
        url,
        json={
            "base_revision": apr["revision"],
            "audio_tracks": [{"id": "music", "type": "audio", "items": []}],
            "captions": [{"start_frame": 0, "duration_frames": 24, "text": "片头"}],
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["shots"] == before
    assert updated["render_status"] == "stale"
    assert len(updated["timeline"]["captions"]) == 1
    assert client.put(url, json={"base_revision": apr["revision"]}).status_code == 409


def test_anchor_commit_requires_preview_and_marks_only_changed_shots_stale(client):
    mount_timeline(client)
    from conftest import storyboard

    apr = storyboard(client)
    pid = apr["project_id"]
    base = f"/api/v1/projects/{pid}/timeline"
    body = {
        "base_revision": apr["revision"],
        "boundary_shot_ids": [apr["shots"][0]["id"]],
        "bpm": 110,
    }
    response = client.post(f"{base}/anchors/preview", json=body)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["changes"]
    assert client.post(f"{base}/anchors/commit", json=body).status_code == 409
    response = client.post(
        f"{base}/anchors/commit",
        json={**body, "approved": True, "preview_token": plan["preview_token"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    affected = set(plan["affected_shot_ids"])
    for old, shot in zip(apr["shots"], result["shots"], strict=True):
        if shot["id"] in affected:
            assert shot["status"] == "stale"
        else:
            assert shot == old


def test_audio_overlaps_across_tracks_and_trim_uses_the_selected_source_region(tmp_path):
    service = domain()
    from app.audio_pipeline import finish_audio

    settings = Settings(data_dir=tmp_path)
    apr = apr_fixture()
    # Half a second of silence followed by tone: trimming past silence is audible.
    source = tmp_path / "source.wav"
    sine(source, seconds=2, amplitude=0.1)
    with wave.open(str(source), "rb") as audio:
        pcm = audio.readframes(audio.getnframes())
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(bytes(24000 * 2) + pcm[24000 * 2 :])
    item = apr["timeline"]["tracks"][1]["items"][0]
    item.update(start_frame=0, source_start_frame=12, duration_frames=24, gain_db=0)
    second = copy.deepcopy(apr["timeline"]["tracks"][1])
    second.update(id="voice", gain_db=-6)
    apr["timeline"]["tracks"].append(second)
    request = {
        "format": apr["format"],
        "items": apr["timeline"]["tracks"][0]["items"],
        **service.composition_request(apr),
    }
    video = tmp_path / "silent.mp4"
    MockVideo(settings).generate(
        {
            "format": apr["format"],
            "order": 0,
            "camera": {"shot_size": "wide"},
            "duration_frames": 72,
        },
        video,
        lambda: True,
    )
    output = tmp_path / "overlaps.mp4"
    finish_audio(settings, request, [source, source], video, output, lambda: True)
    raw = run_process(
        [
            settings.binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-ac",
            "1",
            "-f",
            "f32le",
            "pipe:1",
        ],
        30,
        lambda: True,
    )
    values = struct.unpack(f"<{len(raw) // 4}f", raw)[4800:19200]
    rms = math.sqrt(sum(x * x for x in values) / len(values))
    assert 0.09 < rms < 0.12


def test_caption_finishing_supports_quoted_storage_directories(tmp_path):
    domain()
    from app.audio_pipeline import finish_audio

    directory = tmp_path / "project's [media]; safe"
    directory.mkdir()
    settings = Settings(data_dir=tmp_path)
    apr = apr_fixture()
    apr["timeline"]["tracks"] = apr["timeline"]["tracks"][:1]
    apr["timeline"]["captions"] = [
        {"id": str(uuid4()), "start_frame": 0, "duration_frames": 72, "text": "Visible caption"}
    ]
    video = directory / "silent.mp4"
    MockVideo(settings).generate(
        {
            "format": apr["format"],
            "order": 0,
            "camera": {"shot_size": "wide"},
            "duration_frames": 72,
        },
        video,
        lambda: True,
    )
    request = {
        "format": apr["format"],
        "items": apr["timeline"]["tracks"][0]["items"],
        **domain().composition_request(apr),
    }
    result = finish_audio(settings, request, [], video, directory / "result.mp4", lambda: True)
    assert result["caption_count"] == 1
    assert result["duration_frames"] == 72


def test_import_worker_publishes_audio_with_owned_provenance(client, tmp_path):
    mount_timeline(client)
    from conftest import project

    from app.execution.worker import Worker

    apr = project(client)
    source = tmp_path / "source.wav"
    sine(source, seconds=0.1)
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/timeline/audio/import",
        params={"base_revision": apr["revision"], "rights_confirmed": True, "name": "owned.wav"},
        content=source.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.database).run_once()
    jobs = client.get(f"/api/v1/projects/{apr['project_id']}/jobs").json()
    job = next(j for j in jobs if j["id"] == response.json()["id"])
    assert job["state"] == "completed", job
    updated = client.get(f"/api/v1/projects/{apr['project_id']}/apr").json()
    asset = updated["assets"][0]
    assert asset["media_type"] == "audio"
    assert asset["sample_rate"] == 48000
    assert asset["consent"] == "explicit_upload_rights_confirmation"


def test_final_audio_probe_can_exceed_the_source_upload_duration_cap(monkeypatch, tmp_path):
    from app import audio_pipeline

    payload = (
        b'{"streams":[{"codec_name":"aac","sample_rate":"48000",'
        b'"channels":2,"duration":"7200"}],"format":{"duration":"7200"}}'
    )
    monkeypatch.setattr(audio_pipeline, "run_process", lambda *args: payload)
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(DomainError):
        audio_pipeline.probe_audio(settings, tmp_path / "source.wav", lambda: True)
    assert (
        audio_pipeline.probe_audio(
            settings, tmp_path / "final.mp4", lambda: True, max_seconds=7201
        )["duration_seconds"]
        == 7200
    )
