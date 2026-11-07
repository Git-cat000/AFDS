import time

import pytest
from conftest import project, storyboard
from sqlalchemy import select
from test_queue import generate

from app import db
from app.errors import DomainError
from app.execution.worker import Worker
from app.media import Cancelled
from app.storage import contained


def test_crud_soft_archive_and_scene_edits(client):
    apr = storyboard(client)
    pid = apr["project_id"]
    sid = apr["scenes"][0]["id"]
    patched = client.patch(
        f"/api/v1/projects/{pid}/scenes/{sid}",
        json={
            "base_revision": apr["revision"],
            "patch": {"title": "改名场景"},
        },
    )
    assert patched.status_code == 200
    apr = patched.json()
    deleted = client.request(
        "DELETE",
        f"/api/v1/projects/{pid}/shots/{apr['shots'][0]['id']}",
        json={
            "base_revision": apr["revision"],
            "approved": True,
        },
    )
    assert deleted.status_code == 200
    assert len(deleted.json()["timeline"]["tracks"][0]["items"]) == 3
    archived = client.request(
        "DELETE",
        f"/api/v1/projects/{pid}",
        json={
            "base_revision": deleted.json()["revision"],
            "approved": True,
        },
    )
    assert archived.status_code == 200
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404


def test_untrusted_import_malformed_assets_returns_validation_error(client):
    apr = storyboard(client)
    apr["assets"] = [{"id": "malformed"}]
    response = client.post("/api/v1/projects/import", json=apr)
    assert response.status_code == 422


def test_failure_retry_backoff_and_cancel_running(client, monkeypatch):
    apr = storyboard(client)
    queued = generate(client, apr).json()
    worker = Worker(client.app.state.database)

    def timeout(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr("app.media.MockVideo.generate", timeout)
    assert worker.run_once()
    with worker.database.engine.connect() as connection:
        row = (
            connection.execute(select(db.jobs).where(db.jobs.c.state == "retry_wait"))
            .mappings()
            .one()
        )
        assert row["attempt"] == 1
        assert row["available_at"] > time.time()
    for job in queued:
        worker.queue.cancel(apr["project_id"], job["id"])
    worker.queue.retry(apr["project_id"], queued[0]["id"])

    def cancel(self, request, output, checkpoint):
        worker.queue.cancel(apr["project_id"], queued[0]["id"])
        assert checkpoint() is False
        raise Cancelled()

    monkeypatch.setattr("app.media.MockVideo.generate", cancel)
    assert worker.run_once()
    with worker.database.engine.connect() as connection:
        assert (
            worker.queue.get(connection, apr["project_id"], queued[0]["id"])["state"] == "cancelled"
        )


def test_permanent_storage_error_does_not_retry(client, monkeypatch):
    apr = storyboard(client)
    generate(client, apr)
    worker = Worker(client.app.state.database)

    def disk_full(*args, **kwargs):
        raise OSError(28, "disk full")

    monkeypatch.setattr("app.media.MockVideo.generate", disk_full)
    worker.run_once()
    with worker.database.engine.connect() as connection:
        failed = (
            connection.execute(select(db.jobs).where(db.jobs.c.state == "failed")).mappings().one()
        )
        assert failed["payload"]["error"]["code"] == "STORAGE_ERROR"
        assert failed["attempt"] == 1


def test_path_containment(tmp_path):
    with pytest.raises(DomainError):
        contained(tmp_path, "../outside.mp4")


@pytest.mark.media
def test_approved_selection_survives_global_style_edit(client):
    apr = storyboard(client)
    pid = apr["project_id"]
    generate(client, apr)
    worker = Worker(client.app.state.database)
    while worker.run_once():
        pass
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    sid = apr["shots"][0]["id"]
    selected = apr["shots"][0]["active_asset_version_id"]
    apr = client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}",
        json={
            "base_revision": apr["revision"],
            "patch": {"status": "approved"},
        },
    ).json()
    apr = client.post(
        f"/api/v1/projects/{pid}/changesets/commit",
        json={
            "base_revision": apr["revision"],
            "approved": True,
            "operations": [
                {
                    "op": "set",
                    "entity_type": "project",
                    "entity_id": pid,
                    "field": "style_bible",
                    "value": {"visual_style": "warm"},
                },
            ],
        },
    ).json()
    response = client.post(
        f"/api/v1/projects/{pid}/generate",
        json={
            "base_revision": apr["revision"],
            "shot_ids": [sid],
        },
    )
    assert response.status_code == 202
    assert worker.run_once()
    apr = client.get(f"/api/v1/projects/{pid}/apr").json()
    assert apr["shots"][0]["active_asset_version_id"] == selected
    assert apr["shots"][0]["status"] == "needs_review"
    other = project(client)
    asset_id = apr["assets"][0]["id"]
    assert (
        client.get(f"/api/v1/projects/{other['project_id']}/assets/{asset_id}/file").status_code
        == 404
    )


def test_api_restart_preserves_project(client):
    from fastapi.testclient import TestClient

    from app.main import create_app

    apr = storyboard(client)
    with TestClient(create_app(client.app.state.settings)) as restarted:
        assert restarted.get(f"/api/v1/projects/{apr['project_id']}/apr").json() == apr
