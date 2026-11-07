from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import project, storyboard


def test_apr_creation_export_import_and_references(client):
    apr = storyboard(client)
    assert len(apr["shots"]) == 4
    assert sum(s["duration_frames"] for s in apr["shots"]) == 480
    pid = apr["project_id"]
    exported = client.get(f"/api/v1/projects/{pid}/export").json()
    assert exported["apr_schema_version"] == "0.1.0"
    imported = client.post("/api/v1/projects/import", json=exported)
    assert imported.status_code == 201, imported.text
    assert imported.json()["project_id"] != pid
    exported["shots"][0]["scene_id"] = str(uuid4())
    assert client.post("/api/v1/projects/import", json=exported).status_code == 422


def test_preview_conflict_and_inverse_revision(client):
    apr = storyboard(client)
    pid, sid = apr["project_id"], apr["shots"][2]["id"]
    body = {
        "base_revision": apr["revision"],
        "reason": "close up",
        "operations": [
            {
                "op": "set",
                "entity_type": "shot",
                "entity_id": sid,
                "field": "camera.shot_size",
                "value": "detail",
            },
        ],
    }
    preview = client.post(f"/api/v1/projects/{pid}/changesets/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["affected_shot_ids"] == [sid]
    assert client.get(f"/api/v1/projects/{pid}/apr").json()["revision"] == apr["revision"]
    changed = client.post(f"/api/v1/projects/{pid}/changesets/commit", json=body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["shots"][2]["camera"]["shot_size"] == "detail"
    assert client.post(f"/api/v1/projects/{pid}/changesets/commit", json=body).status_code == 409
    revision = changed.json()["revision"]
    reverted = client.post(
        f"/api/v1/projects/{pid}/revisions/{revision}/revert",
        json={
            "base_revision": revision,
            "approved": True,
        },
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["shots"][2]["camera"] == apr["shots"][2]["camera"]
    assert reverted.json()["revision"] > revision


def test_ownership_lock_and_whitelist(client):
    apr = storyboard(client)
    other = project(client)
    pid, sid = apr["project_id"], apr["shots"][0]["id"]
    patch = {"base_revision": apr["revision"], "patch": {"locked": True}}
    locked = client.patch(f"/api/v1/projects/{pid}/shots/{sid}", json=patch).json()
    response = client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}",
        json={
            "base_revision": locked["revision"],
            "patch": {"action": "overwrite"},
        },
    )
    assert response.status_code == 409
    assert (
        client.patch(
            f"/api/v1/projects/{other['project_id']}/shots/{sid}",
            json={
                "base_revision": other["revision"],
                "patch": {"action": "foreign"},
            },
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/projects/{pid}/shots/{sid}",
            json={
                "base_revision": locked["revision"],
                "patch": {"path": "../secret"},
            },
        ).status_code
        == 422
    )


def test_concurrent_changes_have_one_winner(client):
    apr = storyboard(client)
    pid, sid = apr["project_id"], apr["shots"][0]["id"]

    def update(action):
        return client.patch(
            f"/api/v1/projects/{pid}/shots/{sid}",
            json={
                "base_revision": apr["revision"],
                "patch": {"action": action},
            },
        ).status_code

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(update, ["first", "second"])) == [200, 409]


def test_timeline_order_keeps_shot_specs(client):
    apr = storyboard(client)
    pid = apr["project_id"]
    ids = [s["id"] for s in reversed(apr["shots"])]
    response = client.post(
        f"/api/v1/projects/{pid}/timeline",
        json={
            "base_revision": apr["revision"],
            "shot_ids": ids,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["timeline"]["tracks"][0]["items"][0]["shot_id"] == ids[0]
    assert response.json()["shots"] == apr["shots"]


@pytest.mark.parametrize(
    "frames, numerator, denominator, seconds",
    [
        (120, 24, 1, 5),
        (30000, 30000, 1001, 1001),
    ],
)
def test_rational_frame_times(frames, numerator, denominator, seconds):
    from app.schemas import Format

    fmt = Format(fps_num=numerator, fps_den=denominator)
    assert fmt.seconds(frames) == seconds
