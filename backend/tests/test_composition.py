import copy

import pytest
from fastapi.testclient import TestClient
from test_exchange import demo_media, export_project  # noqa: F401

from app.main import create_app
from app.project_engine import ProjectEngine, rebuild_timeline
from app.schemas import new_id


@pytest.fixture
def film(request):
    database, apr = request.getfixturevalue("export_project")
    second_id, version_id = new_id(), new_id()

    def add_second(value, connection):
        shot = copy.deepcopy(value["shots"][0])
        shot.update(id=second_id, order=1, title="DEMO second", active_asset_version_id=version_id)
        version = copy.deepcopy(value["asset_versions"][0])
        version.update(id=version_id, shot_id=second_id)
        value["shots"].append(shot)
        value["asset_versions"].append(version)
        value["scenes"][0]["shot_ids"].append(second_id)
        rebuild_timeline(value, [item["id"] for item in value["shots"]])

    apr = ProjectEngine(database).mutate(
        apr["project_id"], apr["revision"], "DEMO two clips", add_second
    )
    with TestClient(create_app(database.settings)) as client:
        yield client, database, apr


def overlay(apr):
    image = next(asset["id"] for asset in apr["assets"] if asset["media_type"] == "image")
    return {
        "id": "overlay-1",
        "name": "DEMO picture in picture",
        "hidden": False,
        "items": [
            {
                "id": new_id(),
                "asset_id": image,
                "start_frame": 5,
                "duration_frames": 20,
                "source_start_frame": 0,
                "x": 0.5,
                "y": 0.5,
                "width": 0.5,
                "height": 0.5,
                "opacity": 0.7,
            }
        ],
    }


def body(apr):
    return {
        "base_revision": apr["revision"],
        "video_layers": [overlay(apr)],
        "transitions": [],
        "audio_tracks": [],
        "captions": [],
        "markers": [],
    }


def test_legacy_composition_update_preserves_layers(film):
    client, _, apr = film
    root = f"/api/v1/projects/{apr['project_id']}/timeline"
    response = client.put(f"{root}/composition-v2", json=body(apr))
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["render_status"] == "stale"
    assert changed["shots"] == apr["shots"]
    old = client.put(
        f"{root}/composition",
        json={
            "base_revision": changed["revision"],
            "audio_tracks": [],
            "captions": [],
            "markers": [],
        },
    )
    assert old.status_code == 200, old.text
    assert old.json()["timeline"]["video_layers"] == changed["timeline"]["video_layers"]


def test_render_fingerprint_includes_video_layers(film):
    from app.execution.queue import digest, render_request

    client, _, apr = film
    path = f"/api/v1/projects/{apr['project_id']}/timeline/composition-v2"
    first = client.put(path, json=body(apr)).json()
    changed = body(first)
    changed["video_layers"] = copy.deepcopy(first["timeline"]["video_layers"])
    changed["video_layers"][0]["items"][0]["opacity"] = 0.2
    second = client.put(path, json=changed).json()
    assert digest(render_request(first)) != digest(render_request(second))


def test_rebuild_preserves_imported_clip_identity(film):
    _, _, apr = film
    original = new_id()
    apr["timeline"]["tracks"][0]["items"][0]["id"] = original
    rebuild_timeline(apr)
    assert apr["timeline"]["tracks"][0]["items"][0]["id"] == original


def test_transition_overlap_changes_duration(film):
    client, _, apr = film
    clips = apr["timeline"]["tracks"][0]["items"]
    assert all(clip.get("id") for clip in clips)
    proposal = body(apr)
    proposal["transitions"] = [
        {
            "id": new_id(),
            "from_clip_id": clips[0]["id"],
            "to_clip_id": clips[1]["id"],
            "kind": "dissolve",
            "duration_frames": 12,
        }
    ]
    root = f"/api/v1/projects/{apr['project_id']}/timeline"
    blocked = client.put(f"{root}/composition-v2", json=proposal)
    assert blocked.status_code == 409
    preview = client.post(f"{root}/composition/preview", json=proposal)
    assert preview.status_code == 200, preview.text
    assert preview.json()["total_frames"] == 84
    proposal.update(approved=True, preview_token=preview.json()["preview_token"])
    saved = client.put(f"{root}/composition-v2", json=proposal)
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["timeline"]["tracks"][0]["items"][1]["start_frame"] == 36
    assert result["shots"] == apr["shots"]
    from app.composition import build_composition

    spec = build_composition(result)
    assert spec["total_frames"] == 84
    assert spec["video_layers"][0]["items"][0]["sha256"]


@pytest.mark.parametrize(
    "case", ["geometry", "foreign", "duration", "version", "track_limit", "source"]
)
def test_invalid_layer_is_rejected(film, case):
    client, _, apr = film
    proposal = body(apr)
    item = proposal["video_layers"][0]["items"][0]
    if case == "geometry":
        item["x"] = 0.8
    elif case == "foreign":
        item["asset_id"] = new_id()
    elif case == "duration":
        item["duration_frames"] = 200
    elif case == "version":
        item["asset_version_id"] = apr["asset_versions"][0]["id"]
    elif case == "track_limit":
        proposal["video_layers"] = [{**overlay(apr), "id": f"layer-{i}"} for i in range(8)]
    else:
        item["asset_id"] = next(
            asset["id"] for asset in apr["assets"] if asset["media_type"] == "video"
        )
        item["source_start_frame"] = 30
    response = client.put(
        f"/api/v1/projects/{apr['project_id']}/timeline/composition-v2", json=proposal
    )
    assert response.status_code == 422, response.text
    assert client.get(f"/api/v1/projects/{apr['project_id']}").json()["revision"] == apr["revision"]


def test_fractional_fps_round_trip(film):
    client, database, apr = film

    def fractional(value, connection):
        value["format"].update(fps_num=30000, fps_den=1001)

    apr = ProjectEngine(database).mutate(
        apr["project_id"], apr["revision"], "DEMO frame rate", fractional
    )
    response = client.put(
        f"/api/v1/projects/{apr['project_id']}/timeline/composition-v2", json=body(apr)
    )
    assert response.status_code == 200, response.text
    from app.composition import build_composition

    spec = build_composition(response.json())
    assert spec["format"]["fps_num"] == 30000
    assert spec["format"]["fps_den"] == 1001
    assert spec["total_frames"] == 96


def test_draft_storyboard_accepts_transition_plan(client):
    from conftest import storyboard

    apr = storyboard(client)
    clips = apr["timeline"]["tracks"][0]["items"]
    assert all(item.get("id") for item in clips)
    proposal = {
        "base_revision": apr["revision"],
        "video_layers": [],
        "transitions": [
            {
                "id": new_id(),
                "from_clip_id": clips[0]["id"],
                "to_clip_id": clips[1]["id"],
                "kind": "fadeblack",
                "duration_frames": 12,
            }
        ],
    }
    root = f"/api/v1/projects/{apr['project_id']}/timeline"
    preview = client.post(f"{root}/composition/preview", json=proposal)
    assert preview.status_code == 200, preview.text
    proposal.update(approved=True, preview_token=preview.json()["preview_token"])
    saved = client.put(f"{root}/composition-v2", json=proposal)
    assert saved.status_code == 200, saved.text
    assert saved.json()["assets"] == []


def test_composition_preview_detects_stale_or_changed_plan(film):
    client, _, apr = film
    root = f"/api/v1/projects/{apr['project_id']}/timeline"
    proposal = body(apr)
    preview = client.post(f"{root}/composition/preview", json=proposal)
    assert preview.status_code == 200
    proposal.update(approved=True, preview_token=preview.json()["preview_token"])
    proposal["video_layers"][0]["items"][0]["opacity"] = 0.2
    assert client.put(f"{root}/composition-v2", json=proposal).status_code == 409


def test_transition_plan_requires_explicit_overflow_adjustment(film):
    client, _, apr = film
    clips = apr["timeline"]["tracks"][0]["items"]
    assert all(clip.get("id") for clip in clips)
    proposal = body(apr)
    audio = next(asset["id"] for asset in apr["assets"] if asset["media_type"] == "audio")
    proposal["audio_tracks"] = [
        {
            "id": "music",
            "type": "audio",
            "items": [
                {
                    "asset_id": audio,
                    "start_frame": 48,
                    "duration_frames": 48,
                }
            ],
        }
    ]
    proposal["transitions"] = [
        {
            "id": new_id(),
            "from_clip_id": clips[0]["id"],
            "to_clip_id": clips[1]["id"],
            "kind": "wipeleft",
            "duration_frames": 12,
        }
    ]
    root = f"/api/v1/projects/{apr['project_id']}/timeline"
    preview = client.post(f"{root}/composition/preview", json=proposal).json()
    assert preview["can_apply"] is False
    assert preview["overflows"][0]["after_frames"] == 36
    proposal["trim_overflow"] = True
    preview = client.post(f"{root}/composition/preview", json=proposal).json()
    assert preview["can_apply"] is True
    proposal.update(approved=True, preview_token=preview["preview_token"])
    saved = client.put(f"{root}/composition-v2", json=proposal)
    assert saved.status_code == 200, saved.text
    assert saved.json()["timeline"]["tracks"][1]["items"][0]["duration_frames"] == 36
