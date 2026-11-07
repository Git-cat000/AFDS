import pytest
from conftest import storyboard
from test_queue import generate


@pytest.mark.media
def test_returning_to_cached_spec_and_timeline_reactivates_outputs(client):
    from app.execution.worker import Worker

    apr = storyboard(client)
    pid = apr["project_id"]
    original_action = apr["shots"][2]["action"]
    generate(client, apr)
    worker = Worker(client.app.state.database)
    while worker.run_once():
        pass
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    original_version = apr["shots"][2]["active_asset_version_id"]
    first_render = client.post(
        f"/api/v1/projects/{pid}/render",
        json={
            "base_revision": apr["revision"],
        },
    ).json()
    worker.run_once()
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    original_render = apr["render_asset_id"]
    sid = apr["shots"][2]["id"]
    for action in ["changed", original_action]:
        apr = client.patch(
            f"/api/v1/projects/{pid}/shots/{sid}",
            json={
                "base_revision": apr["revision"],
                "patch": {"action": action},
            },
        ).json()
    returned = generate(client, apr)
    assert returned.status_code == 202
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert apr["shots"][2]["status"] == "ready"
    assert apr["shots"][2]["active_asset_version_id"] == original_version
    assert not worker.run_once()
    for ids in [[s["id"] for s in reversed(apr["shots"])], [s["id"] for s in apr["shots"]]]:
        apr = client.post(
            f"/api/v1/projects/{pid}/timeline",
            json={
                "base_revision": apr["revision"],
                "shot_ids": ids,
            },
        ).json()
    cached = client.post(
        f"/api/v1/projects/{pid}/render",
        json={
            "base_revision": apr["revision"],
        },
    )
    assert cached.status_code == 202, cached.text
    assert cached.json()["reused_from_job_id"] == first_render["id"]
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert apr["render_status"] == "ready"
    assert apr["render_asset_id"] == original_render


@pytest.mark.media
def test_cache_checks_missing_or_corrupted_media(client):
    from app.execution.worker import Worker
    from app.storage import asset_path

    apr = storyboard(client)
    pid = apr["project_id"]
    generate(client, apr)
    worker = Worker(client.app.state.database)
    while worker.run_once():
        pass
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    asset_id = apr["asset_versions"][0]["asset_id"]
    path = asset_path(client.app.state.database, pid, asset_id)
    path.write_bytes(b"corrupted test asset")
    response = generate(client, apr)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CACHE_MEDIA_INVALID"
