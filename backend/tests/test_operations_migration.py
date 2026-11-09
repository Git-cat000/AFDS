from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.config import ROOT, Settings
from app.db import Database
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate


def test_upgrade_from_phase6_preserves_existing_project(tmp_path):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'migration.db'}")
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "0004")
    database = Database(settings)
    apr = ProjectEngine(database).create(ProjectCreate(title="Existing Phase6 project"))
    assert "worker_heartbeats" not in inspect(database.engine).get_table_names()
    database.migrate()
    assert "worker_heartbeats" in inspect(database.engine).get_table_names()
    assert ProjectEngine(database).read(apr["project_id"])["title"] == apr["title"]
    database.engine.dispose()
