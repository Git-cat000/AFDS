import os
import shutil

from fastapi.testclient import TestClient

from app.config import Settings
from app.desktop import configure, desktop_app


def test_desktop_configuration_keeps_installed_data_separate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        os,
        "environ",
        {**os.environ, "AFDS_MODE": "team", "AFDS_DATABASE_URL": "sqlite:///./unrelated.db"},
    )
    data = tmp_path / "AFDS workspace"
    assert configure(data, 18765) == "http://127.0.0.1:18765"
    secret = tmp_path / "unrelated-database-url"
    secret.write_text("sqlite:///unrelated.db", encoding="ascii")
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"AFDS_DATABASE_URL_FILE='{secret.as_posix()}'", encoding="utf-8")
    settings = Settings(_env_file=dotenv)
    assert settings.data_dir == data.resolve()
    assert settings.database_url == "sqlite:///" + (data / "afds.db").as_posix()
    assert settings.mode == "local"


def test_desktop_serves_ui_and_api_on_loopback_host_only(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>AFDS Studio</h1>", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        database_url="sqlite:///" + (tmp_path / "desktop.db").as_posix(),
        cors_origins="http://127.0.0.1:8765",
    )
    app = desktop_app(settings, dist)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        home = client.get("/")
        assert "AFDS Studio" in home.text
        assert home.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert client.get("/api/v1/version").json()["version"] == "1.0.0"
        assert client.get("/", headers={"Host": "other.invalid:8765"}).status_code == 400
        rejected = client.post(
            "/api/v1/projects",
            json={"title": "External"},
            headers={"Origin": "http://other.invalid"},
        )
        assert rejected.status_code == 403
    app.state.database.engine.dispose()


def test_migrations_and_readiness_accept_percent_in_install_path(tmp_path, monkeypatch):
    from app import db, deployment
    from app.config import ROOT

    installed = tmp_path / "AFDS 100%"
    backend = installed / "backend"
    backend.mkdir(parents=True)
    shutil.copyfile(ROOT / "backend/alembic.ini", backend / "alembic.ini")
    shutil.copytree(ROOT / "backend/migrations", backend / "migrations")
    monkeypatch.setattr(db, "ROOT", installed)
    database = db.Database(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "workspace",
            database_url="sqlite:///" + (tmp_path / "workspace.db").as_posix(),
        )
    )
    try:
        database.migrate()
        deployment.WorkerRegistry(database).initialize_store()
        assert deployment.readiness(database)["ready"]
    finally:
        database.engine.dispose()
