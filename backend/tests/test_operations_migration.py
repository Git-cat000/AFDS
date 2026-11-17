from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.config import ROOT, Settings
from app import db
from app.db import Database
from app.project_engine import ProjectEngine
from app.schemas import APR


def test_upgrade_from_phase6_preserves_existing_project(tmp_path):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'migration.db'}")
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "0004")
    database = Database(settings)
    # Seed the frozen historical schema without invoking the current write pipeline.
    apr = APR(title="Existing Phase6 project").model_dump(mode="json")
    payload = {
        key: value
        for key, value in apr.items()
        if key not in {"scenes", "shots", "assets", "asset_versions"}
    }
    with database.transaction() as connection:
        connection.execute(
            db.projects.insert().values(
                id=apr["project_id"], revision=apr["revision"], payload=payload, archived=False
            )
        )
    assert "worker_heartbeats" not in inspect(database.engine).get_table_names()
    database.migrate()
    assert "worker_heartbeats" in inspect(database.engine).get_table_names()
    assert ProjectEngine(database).read(apr["project_id"])["title"] == apr["title"]
    database.engine.dispose()
