import hashlib
import io
import time

import pytest
from PIL import Image
from sqlalchemy import insert

from app import db
from app.execution.queue import Queue
from app.execution.worker import Worker
from app.project_engine import ProjectEngine
from app.schemas import Asset, ProjectCreate, new_id


def orphan(database, apr, color="blue"):
    output = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(output, "PNG")
    data = output.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    relative = f"projects/{apr['project_id']}/assets/{sha[:2]}/{sha}.png"
    path = database.settings.data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, relative, sha


def preview(client, apr):
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/maintenance/cleanup/preview",
        json={"base_revision": apr["revision"]},
    )


def enqueue(client, apr, plan, approved=True):
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/maintenance/cleanup",
        json={
            "base_revision": apr["revision"],
            "approved": approved,
            "plan_id": plan["id"],
            "preview_token": plan["preview_token"],
        },
    )


def age_clock(monkeypatch):
    import app.maintenance_service as maintenance

    now = time.time() + 48 * 3600
    monkeypatch.setattr(maintenance, "clock", lambda: now)


def test_diagnostics_download_has_only_sanitized_runtime_fields(client):
    apr = ProjectEngine(client.app.state.database).create(ProjectCreate(title="PRIVATE title"))
    response = client.get("/api/v1/system/report", params={"project_id": apr["project_id"]})
    assert response.status_code == 200
    assert response.json()["version"] == "0.8.0"
    assert response.json()["project"]["project_id"] == apr["project_id"]
    assert (
        "PRIVATE" not in response.text
        and str(client.app.state.settings.data_dir) not in response.text
    )
    assert "attachment" in response.headers["content-disposition"]


def test_cleanup_is_previewed_approved_and_worker_executed(client, monkeypatch):
    age_clock(monkeypatch)
    database = client.app.state.database
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO cleanup"))
    path, _, _ = orphan(database, apr)
    response = preview(client, apr)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert len(plan["files"]) == 1 and path.exists()
    assert enqueue(client, apr, plan, approved=False).status_code == 409
    queued = enqueue(client, apr, plan)
    assert queued.status_code == 202, queued.text
    assert path.exists()
    assert Worker(database).run_once()
    result = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{queued.json()['id']}").json()
    assert result["state"] == "completed", result.get("error")
    assert result["cleanup_result"]["deleted_count"] == 1 and not path.exists()
    assert ProjectEngine(database).read(apr["project_id"])["revision"] == apr["revision"]


@pytest.mark.parametrize("registered", [True, False])
def test_cleanup_rechecks_registration_and_exact_file_identity(client, monkeypatch, registered):
    age_clock(monkeypatch)
    database = client.app.state.database
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO protected cleanup"))
    path, relative, sha = orphan(database, apr)
    response = preview(client, apr)
    assert response.status_code == 200
    if registered:
        asset = Asset(
            id=new_id(),
            sha256=sha,
            media_type="image",
            size_bytes=path.stat().st_size,
            width=20,
            height=20,
            provenance={},
            license="DEMO",
            source="DEMO",
            consent="DEMO",
        ).model_dump(mode="json")
        with database.transaction() as connection:
            connection.execute(
                insert(db.assets).values(
                    id=asset["id"], project_id=apr["project_id"], path=relative, payload=asset
                )
            )
    else:
        path.write_bytes(b"Changed after preview")
    queued = enqueue(client, apr, response.json())
    assert queued.status_code == 202
    Worker(database).run_once()
    result = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{queued.json()['id']}").json()
    assert result["state"] == "completed", result.get("error")
    assert result["cleanup_result"]["skipped_count"] == 1 and path.exists()


def test_cleanup_blocks_active_jobs_and_keeps_cancelled_partial_receipt(client, monkeypatch):
    age_clock(monkeypatch)
    database = client.app.state.database
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO partial cleanup"))
    files = [orphan(database, apr, color)[0] for color in ("red", "green")]
    plan = preview(client, apr).json()
    queued = enqueue(client, apr, plan)
    assert queued.status_code == 202
    assert preview(client, apr).status_code == 409
    original = Queue.heartbeat

    def cancel_after_first(queue, job_id, owner):
        if job_id == queued.json()["id"] and sum(path.exists() for path in files) < 2:
            queue.cancel(apr["project_id"], job_id)
        return original(queue, job_id, owner)

    monkeypatch.setattr(Queue, "heartbeat", cancel_after_first)
    Worker(database).run_once()
    result = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{queued.json()['id']}").json()
    assert result["state"] == "cancelled"
    assert result["cleanup_result"]["deleted_count"] == 1
    assert sum(path.exists() for path in files) == 1
    monkeypatch.setattr(Queue, "heartbeat", original)
    assert (
        client.post(
            f"/api/v1/projects/{apr['project_id']}/jobs/{queued.json()['id']}/retry"
        ).status_code
        == 200
    )
    Worker(database).run_once()
    complete = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{queued.json()['id']}").json()
    assert complete["state"] == "completed" and complete["cleanup_result"]["deleted_count"] == 2


def test_upload_reports_known_disk_exhaustion_before_writing(client, monkeypatch):
    import shutil
    from collections import namedtuple

    from conftest import storyboard
    from test_images_references import upload_image

    apr = storyboard(client)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(100, 100, 0))
    response = upload_image(client, apr)
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "STORAGE_FULL"


def test_worker_disk_full_keeps_project_unchanged(client, monkeypatch):
    import errno

    from conftest import storyboard
    from test_images_references import upload_image

    import app.image_service as images

    apr = storyboard(client)
    response = upload_image(client, apr)
    assert response.status_code == 202

    def full(*args):
        raise OSError(errno.ENOSPC, "PRIVATE filesystem path")

    monkeypatch.setattr(images, "canonical_image", full)
    Worker(client.app.state.database).run_once()
    result = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{response.json()['id']}").json()
    assert result["error"]["code"] == "STORAGE_FULL"
    assert "PRIVATE" not in str(result["error"])
