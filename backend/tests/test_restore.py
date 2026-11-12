import io
import json
import stat
import struct
import time
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_exchange import demo_media, export_project, run_export  # noqa: F401

from app import db
from app.config import Settings
from app.execution.queue import Queue
from app.execution.worker import Worker
from app.main import create_app
from app.project_engine import ProjectEngine
from app.schemas import new_id
from app.storage import asset_path, file_hash


@pytest.fixture
def source_backup(request):
    database, apr = request.getfixturevalue("export_project")
    character, location, state, marker = (new_id() for _ in range(4))

    def enrich(value, connection):
        value["characters"] = [{"id": character, "name": "DEMO traveller"}]
        value["locations"] = [{"id": location, "name": "DEMO station"}]
        value["world_states"] = [{"id": state, "scene_id": apr["scenes"][0]["id"]}]
        value["scenes"][0]["location_id"] = location
        value["shots"][0].update(character_ids=[character], input_state_ref=state)
        audio = next(a for a in value["assets"] if a["media_type"] == "audio")
        value["timeline"]["tracks"].append(
            {
                "id": "music",
                "type": "audio",
                "name": "DEMO music",
                "gain_db": -3,
                "items": [
                    {
                        "asset_id": audio["id"],
                        "start_frame": 0,
                        "source_start_frame": 0,
                        "duration_frames": 48,
                        "gain_db": -6,
                    }
                ],
            }
        )
        value["timeline"]["captions"] = [
            {"id": new_id(), "start_frame": 0, "duration_frames": 24, "text": "DEMO / MOCK"}
        ]
        value["timeline"]["markers"] = [
            {"id": marker, "frame": 24, "label": "beat", "kind": "beat"}
        ]
        value["render_asset_id"] = value["assets"][0]["id"]
        value["render_status"] = "ready"

    apr = ProjectEngine(database).mutate(
        apr["project_id"], apr["revision"], "DEMO continuity/audio", enrich
    )
    archive = run_export(database, apr)["path"].read_bytes()
    return database, apr, archive


@pytest.fixture
def destination(tmp_path):
    root = tmp_path / "destination"
    settings = Settings(data_dir=root, database_url=f"sqlite:///{root / 'test.db'}")
    database = db.Database(settings)
    database.migrate()
    with TestClient(create_app(settings)) as client:
        yield client, client.app.state.database
    database.engine.dispose()


def upload(client, content, request_id=None, title="Recovered DEMO"):
    return client.post(
        "/api/v1/projects/restore",
        params={
            "request_id": request_id or str(uuid4()),
            "title": title,
            "approved": True,
        },
        content=content,
        headers={"Content-Type": "application/zip"},
    )


def job_state(database, job):
    with database.engine.connect() as connection:
        return Queue(database).get(connection, job["project_id"], job["id"])


def test_roundtrip_preserves_media_references_and_can_render_without_source(
    source_backup, destination
):
    source, original, archive = source_backup
    client, database = destination
    response = upload(client, archive)
    assert response.status_code == 202, response.text
    job = response.json()
    assert "staging_path" not in json.dumps(job)
    assert job["project_id"] != original["project_id"]
    source.engine.dispose()
    (source.settings.data_dir / "projects" / original["project_id"]).rename(
        source.settings.data_dir / "offline-original-media"
    )
    assert Worker(database).run_once()
    complete = job_state(database, job)
    assert complete["state"] == "completed", complete.get("error")
    restored = client.get(f"/api/v1/projects/{job['project_id']}/apr").json()
    assert restored["title"] == "Recovered DEMO"
    assert restored["shots"][0]["locked"] and restored["shots"][0]["status"] == "approved"
    assert restored["shots"][0]["id"] != original["shots"][0]["id"]
    assert restored["asset_versions"][0]["generated_by_job_id"] == job["id"]
    assert restored["shots"][0]["character_ids"] == [restored["characters"][0]["id"]]
    assert restored["shots"][0]["input_state_ref"] == restored["world_states"][0]["id"]
    assert restored["scenes"][0]["location_id"] == restored["locations"][0]["id"]
    assert restored["timeline"]["tracks"][1]["items"][0]["gain_db"] == -6
    assert restored["timeline"]["captions"][0]["text"] == "DEMO / MOCK"
    assert not restored["budget"]["allow_remote"] and not restored["budget"]["allow_unknown_cost"]
    assert float(restored["budget"]["budget_limit"]) == 0
    assert len(complete["restore_receipt"]["source_project_id"]) == 36
    for asset in restored["assets"]:
        assert (
            file_hash(asset_path(database, restored["project_id"], asset["id"])) == asset["sha256"]
        )
        assert (
            client.get(
                f"/api/v1/projects/{restored['project_id']}/assets/{asset['id']}/file"
            ).status_code
            == 200
        )
    render = client.post(
        f"/api/v1/projects/{restored['project_id']}/render",
        json={"base_revision": restored["revision"]},
    ).json()
    assert Worker(database).run_once()
    assert job_state(database, render)["state"] == "completed"


def test_upload_request_uuid_is_idempotent_and_conflicting_input_rejected(
    source_backup, destination
):
    _, _, archive = source_backup
    client, database = destination
    request_id = str(uuid4())
    first = upload(client, archive, request_id).json()
    second = upload(client, archive, request_id)
    assert second.status_code == 202 and second.json()["id"] == first["id"]
    assert upload(client, archive, request_id, "Different").status_code == 409
    assert upload(client, archive + b"different bytes", request_id).status_code == 409
    assert len(client.get("/api/v1/projects").json()) == 1
    assert Worker(database).run_once()
    assert upload(client, archive, request_id).json()["id"] == first["id"]


def rewrite_archive(content, failure):
    with ZipFile(io.BytesIO(content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    media = next(n for n in files if n.startswith("media/"))
    if failure == "traversal":
        files["../outside.txt"] = b"no"
    elif failure == "drive":
        files["C:/outside.txt"] = b"no"
    elif failure == "backslash":
        files["media\\outside.txt"] = b"no"
    elif failure == "extra":
        files["unrelated.txt"] = b"no"
    elif failure == "missing":
        files.pop(media)
    elif failure == "hash":
        files[media] = b"corrupt"
    elif failure == "snapshot":
        apr = json.loads(files["project.apr.json"])
        apr["title"] = "tampered"
        files["project.apr.json"] = json.dumps(apr).encode()
    elif failure == "bad_otio":
        files["timeline.otio"] = b"not OTIO"
    elif failure == "json_duplicate":
        files["manifest.json"] = b'{"export_version":"0.2.0","export_version":"0.2.0"}'
    elif failure == "version":
        manifest = json.loads(files["manifest.json"])
        manifest["export_version"] = "99.0.0"
        files["manifest.json"] = json.dumps(manifest).encode()
    result = io.BytesIO()
    with ZipFile(result, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        if failure == "duplicate":
            archive.writestr("README.txt", b"duplicate")
        if failure == "symlink":
            info = ZipInfo("media/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"../../private")
    return result.getvalue()


@pytest.mark.parametrize(
    "failure",
    [
        "traversal",
        "drive",
        "backslash",
        "extra",
        "missing",
        "hash",
        "snapshot",
        "bad_otio",
        "json_duplicate",
        "version",
        "duplicate",
        "symlink",
    ],
)
def test_rejects_untrusted_archives_before_publication(source_backup, destination, failure):
    _, original, archive = source_backup
    client, database = destination
    response = upload(client, rewrite_archive(archive, failure))
    assert response.status_code == 202, response.text
    job = response.json()
    Worker(database).run_once()
    current = job_state(database, job)
    assert current["state"] == "failed", failure
    assert current["error"]["code"].startswith("RESTORE_"), current["error"]
    assert ProjectEngine(database).read(job["project_id"])["assets"] == []
    assert original["shots"][0]["locked"]


def test_destination_changes_and_cancelled_jobs_cannot_be_overwritten(source_backup, destination):
    _, _, archive = source_backup
    client, database = destination
    job = upload(client, archive).json()
    ProjectEngine(database).mutate(
        job["project_id"], 1, "User edit", lambda apr, _: apr.update(title="User title")
    )
    Worker(database).run_once()
    assert job_state(database, job)["error"]["code"] == "REVISION_CONFLICT"
    assert ProjectEngine(database).read(job["project_id"])["title"] == "User title"
    cancelled = upload(client, archive).json()
    Queue(database).cancel(cancelled["project_id"], cancelled["id"])
    assert not Worker(database).run_once()
    assert ProjectEngine(database).read(cancelled["project_id"])["assets"] == []


def test_expired_attempt_cannot_publish_restore(source_backup, destination):
    from app.restore_service import RestoreService

    _, _, archive = source_backup
    client, database = destination
    queued = upload(client, archive).json()
    worker = Worker(database)
    job = worker.queue.claim(worker.owner)
    work = database.settings.data_dir / "cache" / new_id()
    result = RestoreService(database).execute(job, work, lambda: True)
    with database.transaction() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(
                lease_owner="other", attempt=job["attempt"] + 1, lease_expires_at=time.time() + 30
            )
        )
    RestoreService(database).publish(job, worker.owner, result, 10)
    assert ProjectEngine(database).read(queued["project_id"])["assets"] == []
    with database.engine.connect() as connection:
        assert not connection.execute(
            select(db.assets).where(db.assets.c.project_id == queued["project_id"])
        ).first()


def test_cancel_retry_responses_hide_staging_paths(source_backup, destination):
    _, _, archive = source_backup
    client, database = destination
    job = upload(client, archive).json()
    for action in ("cancel", "retry"):
        response = client.post(
            f"/api/v1/projects/{job['project_id']}/jobs/{job['id']}/{action}", json={}
        )
        assert response.status_code == 200
        assert "staging_path" not in json.dumps(response.json())
    assert Worker(database).run_once()
    assert job_state(database, job)["state"] == "completed"


def test_central_directory_bounds_precede_zipfile_allocation(
    source_backup, destination, monkeypatch
):
    import app.restore_service as restore

    _, _, archive = source_backup
    client, database = destination
    data = bytearray(archive)
    offset = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, offset + 8, 10005, 10005)

    def must_not_parse(*args, **kwargs):
        raise AssertionError("Unbounded central directory was parsed")

    monkeypatch.setattr(restore, "ZipFile", must_not_parse)
    job = upload(client, bytes(data)).json()
    Worker(database).run_once()
    assert job_state(database, job)["error"]["code"] == "RESTORE_INVALID_ARCHIVE"


def test_upload_and_metadata_limits(source_backup, destination, monkeypatch):
    from app import restore_schema

    _, _, archive = source_backup
    client, database = destination
    monkeypatch.setattr(restore_schema, "MAX_ARCHIVE_BYTES", 10)
    assert upload(client, archive).status_code == 413
    assert client.get("/api/v1/projects").json() == []
    monkeypatch.setattr(restore_schema, "MAX_ARCHIVE_BYTES", 2 * 1024**3)
    monkeypatch.setattr(restore_schema, "MAX_METADATA_BYTES", 10)
    job = upload(client, archive).json()
    Worker(database).run_once()
    assert job_state(database, job)["error"]["code"] == "RESTORE_INVALID_ARCHIVE"


def test_cancelled_publication_can_retry_on_same_worker(source_backup, destination, monkeypatch):
    from app.restore_service import RestoreService

    _, _, archive = source_backup
    client, database = destination
    job = upload(client, archive).json()
    original = RestoreService.publish

    def cancel_once(service, active, owner, result, elapsed_ms):
        Queue(database).cancel(active["project_id"], active["id"])
        original(service, active, owner, result, elapsed_ms)

    monkeypatch.setattr(RestoreService, "publish", cancel_once)
    worker = Worker(database)
    worker.run_once()
    assert job_state(database, job)["state"] == "cancelled"
    assert ProjectEngine(database).read(job["project_id"])["assets"] == []
    monkeypatch.setattr(RestoreService, "publish", original)
    worker.queue.retry(job["project_id"], job["id"])
    worker.run_once()
    assert job_state(database, job)["state"] == "completed"


@pytest.mark.parametrize("comment_size", [0, 65535])
def test_zip64_directory_cannot_override_preallocation_bounds(
    source_backup, destination, monkeypatch, comment_size
):
    import app.restore_service as restore

    _, _, archive = source_backup
    client, database = destination
    offset = archive.rfind(b"PK\x05\x06")
    end = bytearray(archive[offset:])
    _, _, _, _, _, length, directory, _ = struct.unpack("<4s4H2LH", end)
    struct.pack_into("<H", end, 20, comment_size)
    zip64 = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 10005, 10005, length, directory
    ) + struct.pack("<4sLQL", b"PK\x06\x07", 0, offset, 1)
    content = archive[:offset] + zip64 + end + b"x" * comment_size

    def must_not_parse(*args, **kwargs):
        raise AssertionError("ZIP64 overrode the prechecked directory bounds")

    monkeypatch.setattr(restore, "ZipFile", must_not_parse)
    job = upload(client, content).json()
    Worker(database).run_once()
    assert job_state(database, job)["error"]["code"] == "RESTORE_INVALID_ARCHIVE"


@pytest.mark.parametrize("track_id", ["v1", "video", "audio"])
def test_restore_remaps_schema_references_without_rewriting_text(
    source_backup, destination, track_id
):
    from app.project_engine import validate
    from app.restore_service import RestoreService

    _, apr, _ = source_backup
    _, database = destination
    apr["timeline"]["tracks"][0]["id"] = track_id
    apr["brief"] = track_id
    apr["style_bible"] = {"id": track_id, "nested": {"asset_id": track_id}}
    apr["scenes"][0]["description"] = track_id
    apr["shots"][0]["action"] = track_id
    apr["characters"][0]["name"] = track_id
    apr["locations"][0]["spatial_notes"] = track_id
    apr["timeline"]["captions"][0]["text"] = track_id
    apr["timeline"]["markers"][0]["label"] = track_id
    character = apr["characters"][0]
    character["reference_asset_ids"] = [apr["assets"][0]["id"]]
    apr["world_states"][0].update(
        shot_id=apr["shots"][0]["id"],
        characters=[{"character_id": character["id"], "costume": track_id}],
        props=[{"name": track_id, "holder_character_id": character["id"]}],
        environment={"id": track_id},
    )
    apr = validate(apr)
    restored, mapping = RestoreService(database).remap(
        apr, {"project_id": new_id(), "title": "Copy", "base_revision": 1}, new_id()
    )
    assert restored["brief"] == track_id
    assert restored["style_bible"] == apr["style_bible"]
    assert restored["scenes"][0]["description"] == track_id
    assert restored["shots"][0]["action"] == track_id
    assert restored["characters"][0]["name"] == track_id
    assert restored["locations"][0]["spatial_notes"] == track_id
    assert restored["timeline"]["captions"][0]["text"] == track_id
    assert restored["timeline"]["markers"][0]["label"] == track_id
    assert restored["timeline"]["tracks"][0]["type"] == "video"
    assert restored["timeline"]["tracks"][0]["id"] == mapping[track_id]
    state = restored["world_states"][0]
    assert state["shot_id"] == restored["shots"][0]["id"]
    assert state["characters"][0]["character_id"] == restored["characters"][0]["id"]
    assert state["props"][0]["holder_character_id"] == restored["characters"][0]["id"]
    assert state["characters"][0]["costume"] == state["props"][0]["name"] == track_id
    assert state["environment"] == {"id": track_id}
    assert restored["characters"][0]["reference_asset_ids"] == [mapping[apr["assets"][0]["id"]]]
    validate(restored)
