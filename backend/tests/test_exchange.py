import copy
import hashlib
import importlib.util
import json
import wave
from zipfile import ZipFile

import pytest
from sqlalchemy import select, update

from app import db
from app.config import Settings
from app.errors import DomainError
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate, new_id
from app.storage import import_asset


@pytest.fixture(scope="module")
def demo_media(tmp_path_factory):
    from PIL import Image, ImageDraw

    from app.media import run_process, verify

    source = tmp_path_factory.mktemp("DEMO-exchange-source")
    settings = Settings(data_dir=source, database_url=f"sqlite:///{source / 'unused.db'}")
    poster = source / "DEMO.png"
    image = Image.new("RGB", (160, 90), "#233044")
    ImageDraw.Draw(image).text((20, 38), "DEMO / MOCK", fill="white")
    image.save(poster)
    video = source / "DEMO.mp4"
    run_process(
        [
            settings.binary("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(poster),
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
    fmt = {"fps_num": 24, "fps_den": 1, "width": 160, "height": 90}
    metadata = verify(settings, video, fmt, 48)
    return video, poster, metadata


@pytest.fixture
def export_project(tmp_path, demo_media):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    database = db.Database(settings)
    database.migrate()
    engine = ProjectEngine(database)
    apr = engine.create(
        ProjectCreate(
            title="DEMO exchange", brief="Portable snapshot", format={"width": 160, "height": 90}
        )
    )
    scene_id, shot_id = new_id(), new_id()
    video, poster, metadata = demo_media
    audio = tmp_path / "DEMO.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\0\0" * 48000 * 2)
    with database.transaction() as connection:
        assets = [
            import_asset(
                connection,
                settings,
                apr["project_id"],
                source,
                metadata,
                {"provider_id": "mock-exchange", "is_mock": True},
                kind,
            )
            for source, metadata, kind in [
                (video, metadata, "video"),
                (poster, {}, "image"),
                (audio, {"duration_seconds": 2.0}, "audio"),
            ]
        ]
        version_id = new_id()
        apr.update(
            assets=assets,
            scenes=[{"id": scene_id, "order": 0, "title": "DEMO", "shot_ids": [shot_id]}],
            shots=[
                {
                    "id": shot_id,
                    "scene_id": scene_id,
                    "order": 0,
                    "title": "DEMO selected shot",
                    "duration_frames": 48,
                    "active_asset_version_id": version_id,
                    "status": "approved",
                    "locked": True,
                }
            ],
            asset_versions=[
                {
                    "id": version_id,
                    "shot_id": shot_id,
                    "asset_id": assets[0]["id"],
                    "poster_asset_id": assets[1]["id"],
                    "generated_by_job_id": new_id(),
                    "status": "ready",
                    "created_at": 1.0,
                }
            ],
            timeline={
                "tracks": [
                    {
                        "id": "v1",
                        "type": "video",
                        "items": [{"shot_id": shot_id, "start_frame": 0, "duration_frames": 48}],
                    }
                ],
                "markers": [],
            },
        )
        before = copy.deepcopy(apr)
        apr["revision"] += 1
        engine.save(connection, apr, before, "DEMO fixture")
    apr = engine.read(apr["project_id"])
    yield database, apr
    database.engine.dispose()


def run_export(database, apr, kind="zip", checkpoint=lambda: True):
    from app.exchange import execute_export, export_request

    work = database.settings.data_dir / "cache" / new_id()
    job = {
        "id": new_id(),
        "project_id": apr["project_id"],
        "operation": "export_project",
        "request": export_request(apr, kind),
    }
    return execute_export(database, job, work, checkpoint)


native_otio = pytest.mark.skipif(
    importlib.util.find_spec("opentimelineio") is None,
    reason="Native OpenTimelineIO is not installed; no hand-written OTIO fallback",
)


def test_request_is_detached_revision_and_hash_bound(export_project):
    from app.exchange import export_request

    _, apr = export_project
    apr["style_bible"] = {
        "palette": "rain blue",
        "api_key": "PRIVATE_KEY_MUST_NOT_EXPORT",
        "provider_config": {"url": "PRIVATE_PROVIDER_CONFIG"},
    }
    request = export_request(apr, "zip")
    assert request["snapshot_revision"] == apr["revision"]
    assert request["asset_manifest"][0]["sha256"] in {a["sha256"] for a in apr["assets"]}
    assert len(request["asset_manifest"]) == len(apr["assets"])
    assert "PRIVATE" not in json.dumps(request)
    apr["shots"][0]["title"] = "later edit"
    apr["assets"][0]["sha256"] = "0" * 64
    assert request["snapshot"]["shots"][0]["title"] == "DEMO selected shot"
    assert request["snapshot"]["assets"][0]["sha256"] != "0" * 64


def test_request_rejects_unknown_format(export_project):
    from app.exchange import export_request

    _, apr = export_project
    with pytest.raises(DomainError) as caught:
        export_request(apr, "../../private")
    assert caught.value.code == "VALIDATION_ERROR"


@native_otio
def test_zip_contains_portable_apr_manifest_and_native_otio(export_project):
    import opentimelineio as otio

    database, apr = export_project
    result = run_export(database, apr)
    assert result["media_type"] == "application/zip"
    assert result["path"].is_relative_to(database.settings.data_dir)
    with ZipFile(result["path"]) as archive:
        assert {"project.apr.json", "manifest.json", "timeline.otio", "README.txt"} <= set(
            archive.namelist()
        )
        exported_apr = json.loads(archive.read("project.apr.json"))
        manifest = json.loads(archive.read("manifest.json"))
        assert exported_apr["project_id"] == apr["project_id"]
        assert manifest["snapshot_revision"] == apr["revision"]
        assert len(manifest["assets"]) == 3
        for asset in manifest["assets"]:
            assert asset["uri"].startswith("media/")
            assert hashlib.sha256(archive.read(asset["uri"])).hexdigest() == asset["sha256"]
            assert asset["license"] == "generated-test-media"
            assert asset["provenance"]["is_mock"] is True
        timeline = otio.adapters.read_from_string(archive.read("timeline.otio").decode())
        clip = timeline.find_clips()[0]
        assert clip.media_reference.target_url in {a["uri"] for a in manifest["assets"]}
        assert clip.source_range.duration.value == 48
        assert timeline.duration().value == 48
        assert exported_apr["shots"][0]["locked"] is True
        assert "DEMO" in archive.read("README.txt").decode()


def test_missing_native_dependency_is_explicit(export_project, monkeypatch):
    from app import exchange

    database, apr = export_project
    original = exchange.importlib.import_module

    def unavailable(name):
        if name == "opentimelineio":
            raise ModuleNotFoundError(name)
        return original(name)

    monkeypatch.setattr(exchange.importlib, "import_module", unavailable)
    with pytest.raises(DomainError) as caught:
        run_export(database, apr)
    assert caught.value.code == "EXPORT_DEPENDENCY_MISSING"
    assert not list(database.settings.data_dir.glob("cache/**/*.zip"))


@native_otio
@pytest.mark.parametrize("failure", ["missing", "corrupt", "size", "foreign", "stored_hash"])
def test_rejects_missing_corrupt_foreign_and_rebound_media(export_project, failure):
    database, apr = export_project
    asset = apr["assets"][0]
    with database.engine.connect() as connection:
        relative = connection.execute(
            select(db.assets.c.path).where(db.assets.c.id == asset["id"])
        ).scalar_one()
    path = database.settings.data_dir / relative
    expected = "HASH_MISMATCH"
    if failure == "missing":
        path.unlink()
        expected = "MISSING_ASSET"
    elif failure == "corrupt":
        path.write_bytes(b"x" * asset["size_bytes"])
    elif failure == "size":
        path.write_bytes(b"different length")
    elif failure == "foreign":
        foreign = ProjectEngine(database).create(ProjectCreate(title="Other owner"))
        with database.transaction() as connection:
            connection.execute(
                update(db.assets)
                .where(db.assets.c.id == asset["id"])
                .values(project_id=foreign["project_id"])
            )
        expected = "NOT_FOUND"
    elif failure == "stored_hash":
        with database.transaction() as connection:
            connection.execute(
                update(db.assets)
                .where(db.assets.c.id == asset["id"])
                .values(payload={**asset, "sha256": "0" * 64})
            )
        expected = "EXPORT_SNAPSHOT_MISMATCH"
    with pytest.raises(DomainError) as caught:
        run_export(database, apr)
    assert caught.value.code == expected
    assert not list(database.settings.data_dir.glob("cache/**/*.zip"))


@native_otio
@pytest.mark.parametrize(
    "relative", ["../private.mp4", "C:/private.mp4", "/private.mp4", "projects/other/assets/x.mp4"]
)
def test_rejects_paths_outside_owned_asset_store(export_project, relative):
    database, apr = export_project
    with database.transaction() as connection:
        connection.execute(
            update(db.assets).where(db.assets.c.id == apr["assets"][0]["id"]).values(path=relative)
        )
    with pytest.raises(DomainError) as caught:
        run_export(database, apr)
    assert caught.value.code == "INVALID_PATH"


@native_otio
def test_export_does_not_follow_a_symlink_outside_asset_store(export_project, tmp_path):
    database, apr = export_project
    external = tmp_path / "private.mp4"
    external.write_bytes(b"PRIVATE")
    link = tmp_path / "projects" / apr["project_id"] / "assets" / "escape.mp4"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("Creating Windows symlinks requires Developer Mode or symlink privilege")
    with database.transaction() as connection:
        connection.execute(
            update(db.assets)
            .where(db.assets.c.id == apr["assets"][0]["id"])
            .values(path=link.relative_to(tmp_path).as_posix())
        )
    with pytest.raises(DomainError) as caught:
        run_export(database, apr)
    assert caught.value.code == "INVALID_PATH"


@native_otio
def test_snapshot_tampering_is_rejected(export_project):
    from app.exchange import execute_export, export_request

    database, apr = export_project
    request = export_request(apr, "zip")
    request["snapshot"]["title"] = "unbound replacement"
    with pytest.raises(DomainError) as caught:
        execute_export(
            database,
            {"operation": "export_project", "project_id": apr["project_id"], "request": request},
            database.settings.data_dir / "cache" / "tampered",
            lambda: True,
        )
    assert caught.value.code == "EXPORT_SNAPSHOT_MISMATCH"


@native_otio
def test_project_edits_after_enqueue_preserve_exported_snapshot(export_project):
    from app.exchange import execute_export, export_request

    database, apr = export_project
    request = export_request(apr, "zip")
    engine = ProjectEngine(database)
    with database.transaction() as connection:
        current = engine.load(connection, apr["project_id"])
        before = copy.deepcopy(current)
        current["title"] = "New title after enqueue"
        current["revision"] += 1
        engine.save(connection, current, before, "Later title edit")
    result = execute_export(
        database,
        {"operation": "export_project", "project_id": apr["project_id"], "request": request},
        database.settings.data_dir / "cache" / "snapshot",
        lambda: True,
    )
    with ZipFile(result["path"]) as archive:
        exported = json.loads(archive.read("project.apr.json"))
    assert exported["revision"] == apr["revision"]
    assert exported["title"] == "DEMO exchange"


@native_otio
def test_zip_excludes_private_files_and_provider_credentials(export_project):
    database, apr = export_project
    (database.settings.data_dir / "PRIVATE_DO_NOT_BUNDLE.key").write_text("PRIVATE_FILE_CONTENT")
    apr["assets"][0]["provenance"].update(
        api_key="PRIVATE_PROVIDER_KEY", provider_config={"password": "PRIVATE_PASSWORD"}
    )
    result = run_export(database, apr)
    with ZipFile(result["path"]) as archive:
        assert all("PRIVATE" not in name for name in archive.namelist())
        assert all(b"PRIVATE" not in archive.read(name) for name in archive.namelist())


@native_otio
def test_cancelled_export_removes_partial_artifact(export_project):
    from app.media import Cancelled

    database, apr = export_project

    def checkpoint():
        return not list(database.settings.data_dir.glob("cache/**/project.zip"))

    with pytest.raises(Cancelled):
        run_export(database, apr, checkpoint=checkpoint)
    assert not list(database.settings.data_dir.glob("cache/**/*.zip"))


@native_otio
def test_media_mutation_during_zip_copy_is_detected(export_project):
    database, apr = export_project
    asset = apr["assets"][0]
    with database.engine.connect() as connection:
        relative = connection.execute(
            select(db.assets.c.path).where(db.assets.c.id == asset["id"])
        ).scalar_one()

    def checkpoint():
        if list(database.settings.data_dir.glob("cache/**/project.zip")):
            (database.settings.data_dir / relative).write_bytes(b"x" * asset["size_bytes"])
        return True

    with pytest.raises(DomainError) as caught:
        run_export(database, apr, checkpoint=checkpoint)
    assert caught.value.code == "HASH_MISMATCH"
    assert not list(database.settings.data_dir.glob("cache/**/*.zip"))


@native_otio
def test_export_size_limit_is_enforced(export_project, monkeypatch):
    from app import exchange

    database, apr = export_project
    monkeypatch.setattr(exchange, "MAX_EXPORT_BYTES", 10)
    with pytest.raises(DomainError) as caught:
        run_export(database, apr)
    assert caught.value.code == "EXPORT_TOO_LARGE"


@native_otio
def test_native_otio_roundtrip_audio_overlap_trim_gain_captions_and_markers(export_project):
    import opentimelineio as otio

    database, apr = export_project
    apr["format"].update(fps_num=30000, fps_den=1001)
    audio_id = next(asset["id"] for asset in apr["assets"] if asset["media_type"] == "audio")
    apr["timeline"]["tracks"].append(
        {
            "id": "dialogue",
            "name": "DEMO dialogue",
            "type": "audio",
            "gain_db": -3,
            "muted": True,
            "items": [
                {
                    "asset_id": audio_id,
                    "start_frame": 6,
                    "source_start_frame": 3,
                    "duration_frames": 18,
                    "gain_db": -6,
                },
                {
                    "asset_id": audio_id,
                    "start_frame": 12,
                    "source_start_frame": 0,
                    "duration_frames": 12,
                    "gain_db": 2,
                },
            ],
        }
    )
    caption = {"id": new_id(), "start_frame": 8, "duration_frames": 16, "text": "DEMO 对白"}
    marker = {"id": new_id(), "frame": 12, "label": "DEMO beat", "kind": "beat"}
    apr["timeline"].update(captions=[caption], markers=[marker])
    result = run_export(database, apr, "otio")
    timeline = otio.adapters.read_from_file(str(result["path"]))
    assert timeline.global_start_time.rate == pytest.approx(30000 / 1001)
    assert timeline.metadata["AFDS"]["format"]["fps_den"] == 1001
    audio_tracks = list(timeline.audio_tracks())
    assert len(audio_tracks) == 2
    assert all(track.metadata["AFDS"]["track_id"] == "dialogue" for track in audio_tracks)
    assert all(track.metadata["AFDS"]["muted"] is True for track in audio_tracks)
    assert all(track.metadata["AFDS"]["gain_db"] == -3 for track in audio_tracks)
    audio_clips = [clip for track in audio_tracks for clip in track.find_clips()]
    assert sorted(clip.range_in_parent().start_time.value for clip in audio_clips) == [6, 12]
    trimmed = next(clip for clip in audio_clips if clip.source_range.start_time.value == 3)
    assert trimmed.source_range.duration.value == 18
    assert trimmed.metadata["AFDS"]["gain_db"] == -6
    assert trimmed.media_reference.target_url.startswith("media/")
    markers = list(timeline.tracks.markers)
    caption_marker = next(item for item in markers if item.metadata["AFDS"]["kind"] == "caption")
    assert caption_marker.marked_range.start_time.value == 8
    assert caption_marker.marked_range.duration.value == 16
    assert caption_marker.name == caption["text"]
    beat_marker = next(item for item in markers if item.metadata["AFDS"]["kind"] == "beat")
    assert beat_marker.marked_range.start_time.value == 12
    assert beat_marker.marked_range.duration.value == 0
    assert "AFDS metadata" in " ".join(result["metadata"]["limitations"])


@pytest.fixture
def exchange_client(export_project):
    from fastapi.testclient import TestClient

    from app.exchange_api import router
    from app.main import create_app

    database, apr = export_project
    app = create_app(database.settings)
    if "/api/v1/projects/{project_id}/exports" not in app.openapi()["paths"]:
        app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client, database, apr
    app.state.database.engine.dispose()


def test_export_api_enqueues_snapshot_without_producing_files(exchange_client):
    client, database, apr = exchange_client
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/exports",
        json={"base_revision": apr["revision"], "format": "zip"},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["operation"] == "export_project"
    assert job["state"] == "queued"
    assert job["request"]["snapshot_revision"] == apr["revision"]
    assert not list(database.settings.data_dir.glob("cache/**/*.zip"))
    stale = client.post(
        f"/api/v1/projects/{apr['project_id']}/exports",
        json={"base_revision": apr["revision"] - 1, "format": "zip"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"


def publish_download_fixture(database, apr, content=b"DEMO export"):
    from app.exchange import export_request
    from app.execution.queue import Queue

    pid = apr["project_id"]
    sha = hashlib.sha256(content).hexdigest()
    relative = f"projects/{pid}/exports/{new_id()}-{sha}.zip"
    path = database.settings.data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with database.transaction() as connection:
        queue = Queue(database)
        job = queue.enqueue(connection, apr, "export_project", export_request(apr, "zip"))
        payload = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
        ).scalar_one()
        payload["artifact"] = {
            "relative_path": relative,
            "filename": "DEMO.zip",
            "media_type": "application/zip",
            "sha256": sha,
            "size_bytes": len(content),
        }
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(payload=payload, state="completed")
        )
    return job["id"], path


def test_download_checks_project_ownership_integrity_and_hides_path(exchange_client):
    client, database, apr = exchange_client
    job_id, path = publish_download_fixture(database, apr)
    route = f"/api/v1/projects/{apr['project_id']}/exports/{job_id}/file"
    response = client.get(route)
    assert response.status_code == 200, response.text
    assert response.content == b"DEMO export"
    other = ProjectEngine(database).create(ProjectCreate(title="Other project"))
    denied = client.get(f"/api/v1/projects/{other['project_id']}/exports/{job_id}/file")
    assert denied.status_code == 404
    reused = client.post(
        f"/api/v1/projects/{apr['project_id']}/exports",
        json={"base_revision": apr["revision"], "format": "zip"},
    )
    assert reused.status_code == 202, reused.text
    assert "relative_path" not in json.dumps(reused.json())
    path.write_bytes(b"CORRUPT")
    corrupt = client.get(route)
    assert corrupt.status_code == 422
    assert corrupt.json()["error"]["code"] == "HASH_MISMATCH"


def test_download_rejects_artifact_traversal(exchange_client):
    client, database, apr = exchange_client
    job_id, _ = publish_download_fixture(database, apr)
    with database.transaction() as connection:
        payload = connection.execute(
            select(db.jobs.c.payload).where(db.jobs.c.id == job_id)
        ).scalar_one()
        payload["artifact"]["relative_path"] = "../private.zip"
        connection.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(payload=payload))
    response = client.get(f"/api/v1/projects/{apr['project_id']}/exports/{job_id}/file")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_PATH"


@native_otio
@pytest.mark.parametrize("kind", ["zip", "otio"])
def test_worker_publishes_snapshot_export_with_downloadable_artifact(exchange_client, kind):
    import opentimelineio as otio

    from app.execution.queue import Queue
    from app.execution.worker import Worker

    client, database, apr = exchange_client
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/exports",
        json={"base_revision": apr["revision"], "format": kind},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert Worker(database).run_once()
    with database.engine.connect() as connection:
        completed = Queue(database).get(connection, apr["project_id"], job_id)
    assert completed["state"] == "completed", completed.get("error")
    assert completed["artifact"]["size_bytes"] > 0
    download = client.get(f"/api/v1/projects/{apr['project_id']}/exports/{job_id}/file")
    assert download.status_code == 200, download.text
    if kind == "zip":
        from io import BytesIO

        with ZipFile(BytesIO(download.content)) as archive:
            native = archive.read("timeline.otio").decode()
    else:
        native = download.content.decode()
    timeline = otio.adapters.read_from_string(native)
    assert timeline.metadata["AFDS"]["revision"] == apr["revision"]
    assert len(timeline.find_clips()) == 1
    public = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{job_id}")
    assert "relative_path" not in json.dumps(public.json())
    assert ProjectEngine(database).read(apr["project_id"])["revision"] == apr["revision"]
