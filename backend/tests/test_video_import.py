import asyncio
from types import SimpleNamespace

import pytest
from conftest import project
from test_exchange import demo_media  # noqa: F401

from app.execution.queue import Queue
from app.execution.worker import Worker
from app.media import run_process


def test_video_upload_requires_rights(client):
    apr = project(client)
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/timeline/video/import?base_revision=1",
        content=b"invalid",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VIDEO_RIGHTS_REQUIRED"


@pytest.mark.parametrize("with_audio", [False, True])
def test_video_import_normalizes_media_and_extracts_owned_audio(
    client, request, tmp_path, with_audio
):
    video, _, _ = request.getfixturevalue("demo_media")
    database = client.app.state.database
    if with_audio:
        source = tmp_path / "DEMO-sound.mp4"
        run_process(
            [
                database.settings.binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-i",
                str(video),
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                str(source),
            ],
            30,
            lambda: True,
        )
    else:
        source = video
    apr = project(client)
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/timeline/video/import?base_revision=1&name=DEMO-source.mp4&rights_confirmed=true",
        content=source.read_bytes(),
        headers={"Content-Type": "video/mp4"},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert "staging_path" not in str(job)
    assert Worker(database).run_once()
    with database.engine.connect() as connection:
        result = Queue(database).get(connection, apr["project_id"], job["id"])
    assert result["state"] == "completed", result.get("error")
    after = client.get(f"/api/v1/projects/{apr['project_id']}").json()
    assert len(after["assets"]) == (2 if with_audio else 1)
    imported = next(asset for asset in after["assets"] if asset["media_type"] == "video")
    assert imported["width"] == 1280 and imported["height"] == 720
    assert imported["duration_frames"] == 48
    assert imported["provenance"]["rights_confirmed"] is True
    assert imported["provenance"]["is_mock"] is False
    assert (
        client.get(f"/api/v1/projects/{apr['project_id']}/assets/{imported['id']}/file").status_code
        == 200
    )
    if with_audio:
        audio = next(asset for asset in after["assets"] if asset["media_type"] == "audio")
        assert audio["sample_rate"] == 48000 and audio["channels"] == 2
        assert audio["provenance"]["derived_from_asset_id"] == imported["id"]


def test_video_import_rejects_playlists(client):
    apr = project(client)
    database = client.app.state.database
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/timeline/video/import?base_revision=1&rights_confirmed=true",
        content=b"#EXTM3U\nfile:///private.mp4\n",
    )
    assert response.status_code == 202
    assert Worker(database).run_once()
    with database.engine.connect() as connection:
        result = Queue(database).get(connection, apr["project_id"], response.json()["id"])
    assert result["state"] == "failed"
    assert result["error"]["code"] == "INVALID_VIDEO"
    assert client.get(f"/api/v1/projects/{apr['project_id']}").json()["assets"] == []


def test_malformed_movie_is_a_typed_input_error(client):
    apr = project(client)
    database = client.app.state.database
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/timeline/video/import?base_revision=1&rights_confirmed=true",
        content=b"\x00\x00\x00\x18ftypisom" + b"\x00" * 40,
    )
    assert response.status_code == 202
    assert Worker(database).run_once()
    with database.engine.connect() as connection:
        job = Queue(database).get(connection, apr["project_id"], response.json()["id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] == "INVALID_VIDEO"


def test_interrupted_video_upload_cleans_staging_without_revision_change(client):
    from app.timeline_api import stage_media_import

    apr = project(client)

    async def stream():
        yield b"partial upload"
        raise ConnectionError("Disconnected")

    request = SimpleNamespace(app=client.app, headers={}, stream=stream)
    with pytest.raises(ConnectionError):
        asyncio.run(stage_media_import(apr["project_id"], request, 1, "DEMO", True, "video"))
    root = client.app.state.database.settings.data_dir / "uploads" / apr["project_id"]
    assert list(root.glob("*.upload")) == []
    after = client.get(f"/api/v1/projects/{apr['project_id']}").json()
    assert after["revision"] == 1 and not after["assets"]


def test_video_import_cancellation_publishes_no_assets(client, request, monkeypatch):
    video, _, _ = request.getfixturevalue("demo_media")
    apr = project(client)
    database = client.app.state.database
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/timeline/video/import?base_revision=1&rights_confirmed=true",
        content=video.read_bytes(),
    )
    assert response.status_code == 202
    monkeypatch.setattr(Queue, "heartbeat", lambda *args: False)
    assert Worker(database).run_once()
    assert client.get(f"/api/v1/projects/{apr['project_id']}").json()["assets"] == []
