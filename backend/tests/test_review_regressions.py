import pytest
from conftest import storyboard


@pytest.mark.parametrize(
    "field,value",
    [
        ("characters", [{}]),
        ("characters", [{"id": []}]),
        ("locations", [{}]),
        ("locations", [{"id": "not-uuid"}]),
    ],
)
def test_import_validates_all_entity_ids(client, field, value):
    apr = storyboard(client)
    apr[field] = value
    assert client.post("/api/v1/projects/import", json=apr).status_code == 422


def test_style_changes_require_approval_and_preserve_archived_shots(client):
    apr = storyboard(client)
    pid = apr["project_id"]
    archived_id = apr["shots"][0]["id"]
    apr = client.request(
        "DELETE",
        f"/api/v1/projects/{pid}/shots/{archived_id}",
        json={
            "base_revision": apr["revision"],
            "approved": True,
        },
    ).json()
    body = {
        "base_revision": apr["revision"],
        "operations": [
            {
                "op": "set",
                "entity_type": "project",
                "entity_id": pid,
                "field": "style_bible",
                "value": {"visual_style": "warm"},
            },
        ],
    }
    rejected = client.post(f"/api/v1/projects/{pid}/changesets/commit", json=body)
    assert rejected.status_code == 409
    accepted = client.post(
        f"/api/v1/projects/{pid}/changesets/commit", json={**body, "approved": True}
    )
    assert accepted.status_code == 200
    assert accepted.json()["shots"][0]["status"] == "archived"


@pytest.mark.parametrize(
    "patch",
    [
        {"status": []},
        {"duration_frames": "invalid"},
        {"duration_frames": []},
        {"camera.shot_size": {}},
        {"locked": "invalid"},
        {},
    ],
)
def test_patch_bad_values_are_validation_errors(client, patch):
    apr = storyboard(client)
    response = client.patch(
        f"/api/v1/projects/{apr['project_id']}/shots/{apr['shots'][0]['id']}",
        json={"base_revision": apr["revision"], "patch": patch},
    )
    assert response.status_code == 422


def test_stale_worker_cannot_change_reclaimed_job(client, monkeypatch):
    import time

    from sqlalchemy import select, update
    from test_queue import generate

    from app import db
    from app.execution.worker import Worker

    apr = storyboard(client)
    generate(client, apr)
    worker = Worker(client.app.state.database)
    original_claim = worker.queue.claim
    reclaimed = {}

    def claim_then_lose_lease(owner):
        job = original_claim(owner)
        reclaimed["id"] = job["id"]
        with worker.database.transaction() as connection:
            connection.execute(
                update(db.jobs)
                .where(db.jobs.c.id == job["id"])
                .values(
                    lease_owner="replacement-worker",
                    attempt=job["attempt"] + 1,
                    lease_expires_at=time.time() + 60,
                )
            )
        return job

    monkeypatch.setattr(worker.queue, "claim", claim_then_lose_lease)
    worker.run_once()
    with worker.database.engine.connect() as connection:
        row = (
            connection.execute(select(db.jobs).where(db.jobs.c.id == reclaimed["id"]))
            .mappings()
            .one()
        )
        assert row["state"] == "claimed"
        assert row["lease_owner"] == "replacement-worker"


@pytest.mark.media
def test_metadata_only_revert_preserves_ready_media(client):
    from test_queue import generate

    from app.execution.worker import Worker

    apr = storyboard(client)
    pid = apr["project_id"]
    generate(client, apr)
    worker = Worker(client.app.state.database)
    while worker.run_once():
        pass
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    versions = apr["asset_versions"]
    sid = apr["shots"][0]["id"]
    apr = client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}",
        json={
            "base_revision": apr["revision"],
            "patch": {"locked": True},
        },
    ).json()
    reverted = client.post(
        f"/api/v1/projects/{pid}/revisions/{apr['revision']}/revert",
        json={
            "base_revision": apr["revision"],
            "approved": True,
        },
    ).json()
    assert reverted["shots"][0]["status"] == "ready"
    assert reverted["asset_versions"] == versions
