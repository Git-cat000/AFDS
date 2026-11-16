import copy
import json

import pytest
from PIL import Image, ImageDraw
from test_composition import body, demo_media, export_project, film  # noqa: F401

from app.execution.queue import Queue
from app.execution.worker import Worker
from app.media import run_process, verify
from app.project_engine import ProjectEngine
from app.schemas import new_id
from app.storage import asset_path, import_asset


@pytest.fixture
def media_film(request, tmp_path):
    client, database, apr = request.getfixturevalue("film")
    sources = []
    for name, color in (("red", "red"), ("blue", "blue")):
        poster = tmp_path / f"DEMO-{name}.png"
        image = Image.new("RGB", (160, 90), color)
        ImageDraw.Draw(image).text((4, 4), "DEMO/MOCK", fill="white")
        image.save(poster)
        sources.append((poster, {"width": 160, "height": 90}, "image"))
    video = tmp_path / "DEMO-blue.mp4"
    run_process(
        [
            database.settings.binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(sources[1][0]),
            "-r",
            "24",
            "-frames:v",
            "48",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        30,
        lambda: True,
    )
    sources.append((video, verify(database.settings, video, apr["format"], 48), "video"))

    def add_colors(value, connection):
        added = [
            import_asset(
                connection,
                database.settings,
                apr["project_id"],
                path,
                metadata,
                {"is_mock": True, "provider_id": "mock-composition", "name": path.stem},
                kind,
            )
            for path, metadata, kind in sources
        ]
        value["assets"].extend(added)
        version = copy.deepcopy(value["asset_versions"][1])
        version.update(id=new_id(), asset_id=added[2]["id"], poster_asset_id=added[1]["id"])
        value["asset_versions"].append(version)
        value["shots"][1]["active_asset_version_id"] = version["id"]

    apr = ProjectEngine(database).mutate(
        apr["project_id"], apr["revision"], "DEMO colors", add_colors
    )
    red = next(
        asset["id"] for asset in apr["assets"] if asset["provenance"].get("name") == "DEMO-red"
    )
    return client, database, apr, red


def completed(database, job):
    assert Worker(database).run_once()
    with database.engine.connect() as connection:
        result = Queue(database).get(connection, job["project_id"], job["id"])
    assert result["state"] == "completed", result.get("error")
    return result


def render_film(client, database, apr):
    queued = client.post(
        f"/api/v1/projects/{apr['project_id']}/render", json={"base_revision": apr["revision"]}
    )
    assert queued.status_code == 202, queued.text
    job = completed(database, queued.json())
    path = asset_path(database, apr["project_id"], job["output_asset_ids"][0])
    return path, ProjectEngine(database).read(apr["project_id"])


def pixel(settings, path, frame, x, y):
    content = run_process(
        [
            settings.binary("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{frame})",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        30,
        lambda: True,
    )
    offset = (y * 160 + x) * 3
    return tuple(content[offset : offset + 3])


def test_pip_pixels_and_end_frame(media_film):
    client, database, apr, red = media_film
    proposal = body(apr)
    proposal["video_layers"][0]["items"][0].update(asset_id=red, opacity=1)
    saved = client.put(
        f"/api/v1/projects/{apr['project_id']}/timeline/composition-v2", json=proposal
    )
    assert saved.status_code == 200, saved.text
    path, result = render_film(client, database, saved.json())
    assert (
        next(asset for asset in result["assets"] if asset["id"] == result["render_asset_id"])[
            "duration_frames"
        ]
        == 96
    )
    inside = pixel(database.settings, path, 10, 120, 70)
    assert inside[0] > 230 and inside[1] < 20 and inside[2] < 20
    outside = pixel(database.settings, path, 10, 8, 80)
    assert abs(outside[0] - 35) < 12
    after = pixel(database.settings, path, 30, 120, 70)
    assert abs(after[0] - 35) < 12


@pytest.mark.parametrize("fps", [(24, 1), (30000, 1001)])
def test_transition_frames_are_blended_with_audio(media_film, fps):
    client, database, apr, _ = media_film
    if fps != (24, 1):

        def change_rate(value, connection):
            value["format"].update(fps_num=fps[0], fps_den=fps[1])

        apr = ProjectEngine(database).mutate(
            apr["project_id"], apr["revision"], "DEMO fractional fps", change_rate
        )
    clips = apr["timeline"]["tracks"][0]["items"]
    proposal = body(apr)
    proposal["video_layers"] = []
    proposal["transitions"] = [
        {
            "id": new_id(),
            "from_clip_id": clips[0]["id"],
            "to_clip_id": clips[1]["id"],
            "kind": "dissolve",
            "duration_frames": 12,
        }
    ]
    audio = next(asset["id"] for asset in apr["assets"] if asset["media_type"] == "audio")
    proposal["audio_tracks"] = [
        {
            "id": "music",
            "type": "audio",
            "items": [{"asset_id": audio, "start_frame": 0, "duration_frames": 24}],
        }
    ]
    root = f"/api/v1/projects/{apr['project_id']}/timeline"
    preview = client.post(f"{root}/composition/preview", json=proposal).json()
    proposal.update(approved=True, preview_token=preview["preview_token"])
    saved = client.put(f"{root}/composition-v2", json=proposal)
    assert saved.status_code == 200, saved.text
    path, result = render_film(client, database, saved.json())
    asset = next(asset for asset in result["assets"] if asset["id"] == result["render_asset_id"])
    assert asset["duration_frames"] == 84
    middle = pixel(database.settings, path, 42, 120, 70)
    assert 90 < middle[2] < 235
    raw = run_process(
        [
            database.settings.binary("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        30,
        lambda: True,
    )
    audio_stream = next(
        stream for stream in json.loads(raw)["streams"] if stream["codec_type"] == "audio"
    )
    assert audio_stream["sample_rate"] == "48000" and audio_stream["channels"] == 2


def test_preview_is_owned_and_preserves_the_final_render(media_film):
    client, database, apr, _ = media_film
    _, apr = render_film(client, database, apr)
    selected = apr["render_asset_id"]
    path = f"/api/v1/projects/{apr['project_id']}/timeline/preview"
    queued = client.post(
        path, json={"base_revision": apr["revision"], "start_frame": 12, "duration_frames": 24}
    )
    assert queued.status_code == 202, queued.text
    job = completed(database, queued.json())
    after = ProjectEngine(database).read(apr["project_id"])
    assert after["render_asset_id"] == selected and after["render_status"] == "ready"
    asset = next(asset for asset in after["assets"] if asset["id"] == job["output_asset_ids"][0])
    assert asset["duration_frames"] == 24
    assert (
        client.get(f"/api/v1/projects/{apr['project_id']}/assets/{asset['id']}/file").status_code
        == 200
    )
    repeated = client.post(
        path, json={"base_revision": after["revision"], "start_frame": 12, "duration_frames": 24}
    )
    assert repeated.status_code == 202
    assert repeated.json()["id"] == job["id"]
