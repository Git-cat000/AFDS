import time

import pytest
from conftest import storyboard
from sqlalchemy import update


def generate(client, apr, **extra):
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [s["id"] for s in apr["shots"]],
            **extra,
        },
    )


def test_queue_deduplicates_and_cancels(client):
    apr = storyboard(client)
    first = generate(client, apr)
    assert first.status_code == 202, first.text
    second = generate(client, apr)
    assert [j["id"] for j in first.json()] == [j["id"] for j in second.json()]
    pid, jid = apr["project_id"], first.json()[0]["id"]
    response = client.post(f"/api/v1/projects/{pid}/jobs/{jid}/cancel")
    assert response.json()["state"] == "cancelled"
    assert client.post(f"/api/v1/projects/{pid}/jobs/{jid}/cancel").json()["state"] == "cancelled"
    events = client.get(f"/api/v1/events?project_id={pid}&after=0").json()
    assert any(e["type"] == "job.cancelled" for e in events)
    cursor = events[-1]["event_id"]
    assert client.get(f"/api/v1/events?project_id={pid}&after={cursor}").json() == []


def test_unknown_and_paid_provider_rejected(client):
    apr = storyboard(client)
    assert (
        generate(client, apr, provider_id="cloud-paid").json()["error"]["code"]
        == "MODEL_UNAVAILABLE"
    )
    assert (
        generate(client, apr, capability="voice_clone").json()["error"]["code"]
        == "CAPABILITY_UNSUPPORTED"
    )


def test_lease_recovery_fences_old_owner(client):
    from app import db
    from app.execution.queue import Queue

    apr = storyboard(client)
    generate(client, apr)
    queue = Queue(client.app.state.database)
    claimed = queue.claim("old-worker")
    with queue.database.transaction() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == claimed["id"])
            .values(
                lease_expires_at=time.time() - 1,
            )
        )
    recovered = queue.claim("new-worker")
    assert recovered["id"] == claimed["id"]
    assert recovered["attempt"] == 2
    assert not queue.heartbeat(claimed["id"], "old-worker")
    assert queue.heartbeat(claimed["id"], "new-worker")


def test_job_state_machine_rejects_illegal_transition():
    from app.errors import DomainError
    from app.execution.queue import transition

    with pytest.raises(DomainError):
        transition("queued", "completed")
    assert transition("running", "verifying") == "verifying"


@pytest.mark.media
def test_real_four_clip_render_and_local_edit(client):
    from app.execution.worker import Worker
    from app.media import probe
    from app.storage import asset_path

    apr = storyboard(client)
    pid = apr["project_id"]
    assert generate(client, apr).status_code == 202
    worker = Worker(client.app.state.database)
    for _ in range(4):
        assert worker.run_once()
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert len(apr["assets"]) == 8  # Video and labeled poster per shot.
    assert all(s["status"] == "ready" for s in apr["shots"])
    response = client.post(
        f"/api/v1/projects/{pid}/render", json={"base_revision": apr["revision"]}
    )
    assert response.status_code == 202, response.text
    assert worker.run_once()
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    output = asset_path(client.app.state.database, pid, apr["render_asset_id"])
    metadata = probe(client.app.state.settings, output)
    assert metadata["codec"] == "h264"
    assert metadata["duration_frames"] == 480
    assert abs(metadata["duration_seconds"] - 20) <= 1 / 24
    old_versions = [s["active_asset_version_id"] for s in apr["shots"]]
    old_render = apr["render_asset_id"]
    repeated = client.post(
        f"/api/v1/projects/{pid}/render", json={"base_revision": apr["revision"]}
    )
    assert repeated.json()["id"] == response.json()["id"]
    assert repeated.json()["reused_from_job_id"] == response.json()["id"]
    sid = apr["shots"][2]["id"]
    edited = client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}",
        json={
            "base_revision": apr["revision"],
            "patch": {"action": "新的第三镜头"},
        },
    ).json()
    assert [v["status"] for v in edited["asset_versions"]].count("stale") == 1
    assert edited["render_status"] == "stale"
    queued = generate(client, edited).json()
    assert sum(j["state"] == "queued" for j in queued) == 1
    assert worker.run_once()
    edited = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert [s["active_asset_version_id"] for i, s in enumerate(edited["shots"]) if i != 2] == [
        old_versions[i] for i in (0, 1, 3)
    ]
    assert edited["shots"][2]["active_asset_version_id"] != old_versions[2]
    client.post(f"/api/v1/projects/{pid}/render", json={"base_revision": edited["revision"]})
    assert worker.run_once()
    final = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert final["render_asset_id"] != old_render
    media = client.get(
        f"/api/v1/projects/{pid}/assets/{final['render_asset_id']}/file",
        headers={"Range": "bytes=0-99"},
    )
    assert media.status_code == 206
    assert len(media.content) == 100
    assert "D:" not in str(final["assets"])
