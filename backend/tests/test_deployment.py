"""Deployment readiness, explicit production configuration and offline recovery."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import db
from app.config import Settings
from app.deployment import readiness
from app.errors import DomainError
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate
from app.system_backup import backup_system, restore_system
from app.worker_registry import WorkerRegistry


def test_readiness_requires_current_schema_and_shared_store(dialect_database):
    database = dialect_database
    assert not readiness(database)["ready"]
    WorkerRegistry(database).initialize_store()
    assert readiness(database)["ready"]
    marker = database.settings.data_dir / "state/worker-store.json"
    marker.write_text("DEMO incorrect mount", encoding="utf-8")
    assert not readiness(database)["ready"]


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "local"},
        {"secure_cookies": False},
        {"public_origin": "http://studio.example"},
        {"database_url": "sqlite:///./data/test.db"},
        {"trusted_proxy": "*"},
        {"public_origin": "https://studio.example/path"},
    ],
)
def test_production_rejects_unsafe_configuration(tmp_path, change):
    values = dict(
        production=True,
        mode="team",
        secure_cookies=True,
        public_origin="https://studio.example",
        trusted_proxy="172.31.250.10",
        database_url="postgresql+psycopg://afds@database/afds",
        data_dir=tmp_path,
    )
    with pytest.raises(ValidationError):
        Settings(**(values | change))


def test_production_reads_database_secret_file(tmp_path):
    secret = tmp_path / "database-url"
    secret.write_text("postgresql+psycopg://afds@database/afds\n", encoding="utf-8")
    config = Settings(
        production=True,
        mode="team",
        public_origin="https://studio.example",
        trusted_proxy="172.31.250.10",
        database_url_file=secret,
        data_dir=tmp_path,
    )
    assert config.database_url == "postgresql+psycopg://afds@database/afds"
    assert config.cors_origins == "https://studio.example"


def test_offline_backup_restores_project_revisions_and_media_without_secrets(tmp_path):
    from app.db import Database
    from app.identity_service import IdentityService

    source = tmp_path / "source"
    database = Database(Settings(data_dir=source, database_url=f"sqlite:///{source / 'afds.db'}"))
    database.migrate()
    WorkerRegistry(database).initialize_store()
    identity = IdentityService(database)
    owner = identity.create_user("demo-owner", "DEMO-owner-password-2026", is_admin=True)
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO recovery", brief="offline"))
    identity.claim_local_projects("demo-owner")
    identity.login("demo-owner", "DEMO-owner-password-2026", "DEMO localhost")
    with database.engine.connect() as connection:
        assert connection.execute(select(db.sessions)).first() is not None
    media = source / "projects" / apr["project_id"] / "assets" / "DEMO.txt"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"DEMO immutable asset evidence")
    secrets = source / "secrets"
    secrets.mkdir()
    (secrets / "provider.key").write_text("DEMO excluded", encoding="utf-8")
    archive = tmp_path / "backup"
    with pytest.raises(DomainError, match="Stop API"):
        backup_system(database, archive, stopped=False)
    manifest = backup_system(database, archive, stopped=True)
    assert manifest["migration"] == "0011"
    assert all("secret" not in item["path"] for item in manifest["files"])
    target = tmp_path / "restored"
    config = Settings(data_dir=target, database_url=f"sqlite:///{target / 'afds.db'}")
    restore_system(config, archive, stopped=True)
    restored = Database(config)
    assert ProjectEngine(restored).read(apr["project_id"])["revision"] == apr["revision"]
    assert (target / media.relative_to(source)).read_bytes() == media.read_bytes()
    assert not (target / "secrets/provider.key").exists()
    assert readiness(restored)["ready"]
    with restored.engine.connect() as connection:
        assert set(connection.execute(select(db.users.c.username)).scalars()) == {
            "_local",
            "demo-owner",
        }
        assert connection.execute(select(db.sessions)).first() is None
        assert (
            connection.execute(
                select(db.project_members.c.role).where(
                    db.project_members.c.project_id == apr["project_id"],
                    db.project_members.c.user_id == owner["id"],
                )
            ).scalar_one()
            == "owner"
        )
    with pytest.raises(DomainError):
        restore_system(config, archive, stopped=True)
    restored.engine.dispose()
    database.engine.dispose()


def test_restore_rejects_corruption_and_path_traversal_before_writing(tmp_path):
    archive = tmp_path / "bad"
    archive.mkdir()
    (archive / "database.sqlite").write_bytes(b"DEMO not a database")
    (archive / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "dialect": "sqlite",
                "migration": "0011",
                "database": {"path": "database.sqlite", "sha256": "0" * 64, "size": 19},
                "files": [{"path": "../escape", "sha256": "0" * 64, "size": 0}],
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "target"
    with pytest.raises(DomainError):
        restore_system(
            Settings(data_dir=target, database_url=f"sqlite:///{target / 'afds.db'}"),
            archive,
            stopped=True,
        )
    assert not target.exists()


def test_restore_rejects_valid_paths_with_corrupt_contents_before_writing(tmp_path):
    from app.db import Database

    source = tmp_path / "source"
    database = Database(Settings(data_dir=source, database_url=f"sqlite:///{source / 'afds.db'}"))
    database.migrate()
    WorkerRegistry(database).initialize_store()
    archive = tmp_path / "backup"
    manifest = backup_system(database, archive, stopped=True)
    dump = archive / manifest["database"]["path"]
    contents = bytearray(dump.read_bytes())
    contents[-1] ^= 1
    dump.write_bytes(contents)
    target = tmp_path / "restored"
    with pytest.raises(DomainError, match="hash is invalid"):
        restore_system(
            Settings(data_dir=target, database_url=f"sqlite:///{target / 'afds.db'}"),
            archive,
            stopped=True,
        )
    assert not target.exists()
    database.engine.dispose()


def test_public_readiness_does_not_expose_internal_details(client):
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    WorkerRegistry(client.app.state.database).initialize_store()
    assert client.get("/api/v1/ready").json() == {"status": "ready"}
