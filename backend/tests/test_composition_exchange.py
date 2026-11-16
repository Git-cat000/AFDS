import copy
import io
import json
from zipfile import ZipFile

import opentimelineio as otio
import pytest
from test_composition import body, demo_media, export_project, film  # noqa: F401
from test_composition_media import media_film, pixel, render_film  # noqa: F401
from test_exchange import run_export
from test_restore import job_state, upload

from app.composition import backbone_duration
from app.execution.worker import Worker
from app.schemas import new_id
from app.storage import file_hash


def test_structure_import_remaps_transitions_and_preserves_empty_layer_design(request):
    client, _, original = request.getfixturevalue("layered_film")
    imported = client.post("/api/v1/projects/import", json=original)
    assert imported.status_code == 201, imported.text
    apr = imported.json()
    assert apr["assets"] == [] and apr["asset_versions"] == []
    assert len(apr["timeline"]["video_layers"]) == 2
    assert all(not track["items"] for track in apr["timeline"]["video_layers"])
    clips = apr["timeline"]["tracks"][0]["items"]
    assert not {clip["id"] for clip in clips} & {
        clip["id"] for clip in original["timeline"]["tracks"][0]["items"]
    }
    transition = apr["timeline"]["transitions"][0]
    assert (
        transition["from_clip_id"] == clips[0]["id"] and transition["to_clip_id"] == clips[1]["id"]
    )
    assert transition["id"] != original["timeline"]["transitions"][0]["id"]
    assert backbone_duration(apr["timeline"]) == 84


@pytest.mark.parametrize("problem", ["stale_main", "missing_main", "image_main", "stale_layer"])
def test_invalid_selected_media_identifies_the_affected_clip(request, problem):
    from app.composition import build_composition
    from app.errors import DomainError
    from app.skill_checks import check_production

    _, _, original = request.getfixturevalue("layered_film")
    apr = copy.deepcopy(original)
    clip = apr["timeline"]["tracks"][0]["items"][0]
    selected = apr["shots"][0]["active_asset_version_id"]
    version = next(value for value in apr["asset_versions"] if value["id"] == selected)
    if problem == "missing_main":
        apr["shots"][0]["active_asset_version_id"] = None
    elif problem == "image_main":
        version["asset_id"] = next(
            asset["id"] for asset in apr["assets"] if asset["media_type"] == "image"
        )
    elif problem == "stale_layer":
        clip = apr["timeline"]["video_layers"][0]["items"][1]
        version = next(
            value for value in apr["asset_versions"] if value["id"] == clip["asset_version_id"]
        )
        alternative = copy.deepcopy(version)
        alternative["id"] = new_id()
        alternative["status"] = "stale"
        apr["asset_versions"].append(alternative)
        clip["asset_version_id"] = alternative["id"]
    else:
        version["status"] = "stale"
    with pytest.raises(DomainError) as caught:
        build_composition(apr)
    assert caught.value.details["clip_id"] == clip["id"]
    checks = check_production(apr, "editing")["checks"]
    warning = next(item for item in checks if item["code"] == "EDIT_COMPOSITION")
    assert warning["status"] == "warning" and warning["evidence"]["clip_id"] == clip["id"]


@pytest.fixture
def layered_film(request):
    client, database, apr, red = request.getfixturevalue("media_film")
    proposal = body(apr)
    first, second = apr["timeline"]["tracks"][0]["items"]
    proposal["transitions"] = [
        {
            "id": new_id(),
            "from_clip_id": first["id"],
            "to_clip_id": second["id"],
            "kind": "dissolve",
            "duration_frames": 12,
        }
    ]
    layer = proposal["video_layers"][0]
    layer["items"][0]["asset_id"] = red
    version = next(
        version
        for version in apr["asset_versions"]
        if version["id"] == apr["shots"][1]["active_asset_version_id"]
    )
    layer["items"].append(
        {
            **layer["items"][0],
            "id": new_id(),
            "asset_id": version["asset_id"],
            "asset_version_id": version["id"],
            "start_frame": 15,
            "duration_frames": 24,
        }
    )
    hidden = copy.deepcopy(layer)
    hidden.update(id="hidden-overlay", name="Hidden DEMO", hidden=True)
    hidden["items"] = [{**hidden["items"][0], "id": new_id(), "start_frame": 50}]
    proposal["video_layers"].append(hidden)
    audio = next(asset for asset in apr["assets"] if asset["media_type"] == "audio")
    proposal["audio_tracks"] = [
        {
            "id": "music",
            "type": "audio",
            "items": [
                {
                    "asset_id": audio["id"],
                    "start_frame": start,
                    "source_start_frame": 0,
                    "duration_frames": 24,
                }
                for start in (0, 12)
            ],
        }
    ]
    proposal["captions"] = [
        {"id": new_id(), "start_frame": 0, "duration_frames": 12, "text": "DEMO/MOCK"}
    ]
    proposal["markers"] = [{"id": new_id(), "frame": 12, "kind": "beat", "label": "DEMO beat"}]
    root = f"/api/v1/projects/{apr['project_id']}"
    preview = client.post(f"{root}/timeline/composition/preview", json=proposal)
    assert preview.status_code == 200, preview.text
    saved = client.put(
        f"{root}/timeline/composition-v2",
        json={**proposal, "approved": True, "preview_token": preview.json()["preview_token"]},
    )
    assert saved.status_code == 200, saved.text
    return client, database, saved.json()


@pytest.mark.parametrize("overlap,expected", [(12, 84), (11, 85)])
def test_multitrack_otio_round_trip_and_odd_transition_handles(request, overlap, expected):
    client, database, apr = request.getfixturevalue("layered_film")
    if overlap != 12:
        proposal = {
            "base_revision": apr["revision"],
            "video_layers": apr["timeline"]["video_layers"],
            "transitions": [{**apr["timeline"]["transitions"][0], "duration_frames": overlap}],
            "audio_tracks": apr["timeline"]["tracks"][1:],
            "captions": apr["timeline"]["captions"],
            "markers": apr["timeline"]["markers"],
        }
        root = f"/api/v1/projects/{apr['project_id']}/timeline"
        preview = client.post(f"{root}/composition/preview", json=proposal).json()
        apr = client.put(
            f"{root}/composition-v2",
            json={**proposal, "approved": True, "preview_token": preview["preview_token"]},
        ).json()
    artifact = run_export(database, apr, "otio")
    timeline = otio.adapters.read_from_string(artifact["path"].read_text("utf-8"), "otio_json")
    assert timeline.duration().to_frames(24) == expected
    backbone = timeline.tracks[0]
    assert [type(item) for item in backbone] == [
        otio.schema.Clip,
        otio.schema.Transition,
        otio.schema.Clip,
    ]
    transition = backbone[1]
    assert transition.in_offset.to_frames(24) == overlap // 2
    assert transition.out_offset.to_frames(24) == overlap - overlap // 2
    assert backbone[2].source_range.start_time.to_frames(24) == overlap // 2
    assert backbone[0].media_reference.available_range.duration.to_frames(24) == 48
    stacks = [track for track in timeline.tracks if isinstance(track, otio.schema.Stack)]
    assert len(stacks) == 2
    assert stacks[0].enabled and not stacks[1].enabled
    assert len(stacks[0]) == 2
    for original, lane in zip(apr["timeline"]["video_layers"][0]["items"], stacks[0], strict=True):
        assert isinstance(lane[0], otio.schema.Gap)
        clip = lane.find_clips()[0]
        assert clip.metadata["AFDS"]["id"] == original["id"]
        assert clip.metadata["AFDS"]["opacity"] == original["opacity"]
        assert clip.effects[0].metadata["AFDS"]["schema_version"] == "0.5.0"
        assert lane.duration().to_frames(24) == expected
    assert (
        len(
            [
                track
                for track in timeline.tracks
                if isinstance(track, otio.schema.Track)
                and track.kind == otio.schema.TrackKind.Audio
            ]
        )
        == 2
    )


def test_restored_composition_renders_same_frames_and_remaps_all_clip_references(request):
    client, database, apr = request.getfixturevalue("layered_film")
    original_path, apr = render_film(client, database, apr)
    root = f"/api/v1/projects/{apr['project_id']}"
    exported = client.post(
        f"{root}/exports", json={"base_revision": apr["revision"], "format": "zip"}
    )
    assert exported.status_code == 202, exported.text
    assert Worker(database).run_once()
    downloaded = client.get(f"{root}/exports/{exported.json()['id']}/file")
    assert downloaded.status_code == 200, downloaded.text
    with ZipFile(io.BytesIO(downloaded.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["export_version"] == "0.3.0"
        assert "workspace.json" in archive.namelist()
    response = upload(client, downloaded.content)
    assert response.status_code == 202, response.text
    job = response.json()
    assert Worker(database).run_once()
    result = job_state(database, job)
    assert result["state"] == "completed", result.get("error")
    restored = client.get(f"/api/v1/projects/{job['project_id']}").json()
    original_clips = {item["id"] for item in apr["timeline"]["tracks"][0]["items"]}
    clips = restored["timeline"]["tracks"][0]["items"]
    assert not original_clips & {item["id"] for item in clips}
    transition = restored["timeline"]["transitions"][0]
    assert (
        transition["from_clip_id"] == clips[0]["id"] and transition["to_clip_id"] == clips[1]["id"]
    )
    assert transition["id"] != apr["timeline"]["transitions"][0]["id"]
    owned = {asset["id"] for asset in restored["assets"]}
    for track in restored["timeline"]["video_layers"]:
        for item in track["items"]:
            assert item["asset_id"] in owned
            assert item["id"] not in {
                old["id"]
                for original in apr["timeline"]["video_layers"]
                for old in original["items"]
            }
    assert backbone_duration(restored["timeline"]) == 84
    output, _ = render_film(client, database, restored)
    assert file_hash(output)
    for frame in (0, 10, 20, 42, 60, 83):
        assert pixel(database.settings, output, frame, 100, 65) == pixel(
            database.settings, original_path, frame, 100, 65
        )
