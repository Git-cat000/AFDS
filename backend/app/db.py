from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
)

from app.config import ROOT, Settings

metadata = MetaData()
projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("archived", Boolean, default=False),
)
scenes = Table(
    "scenes",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)
shots = Table(
    "shots",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("scene_id", ForeignKey("scenes.id"), nullable=False),
    Column("payload", JSON, nullable=False),
)
assets = Table(
    "assets",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("path", String, nullable=False),
    Column("payload", JSON, nullable=False),
)
versions = Table(
    "asset_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("shot_id", ForeignKey("shots.id"), nullable=False),
    Column("asset_id", ForeignKey("assets.id"), nullable=False),
    Column("payload", JSON, nullable=False),
)
revisions = Table(
    "revisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("revision", Integer, nullable=False),
    Column("reason", String, nullable=False),
    Column("author", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("before", JSON),
    Column("after", JSON, nullable=False),
    UniqueConstraint("project_id", "revision"),
)
jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("idempotency_key", String, nullable=False, unique=True),
    Column("state", String, nullable=False, index=True),
    Column("lease_owner", String),
    Column("lease_expires_at", Float),
    Column("attempt", Integer, nullable=False, default=0),
    Column("available_at", Float, nullable=False, default=0),
    Column("payload", JSON, nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("job_id", String),
    Column("type", String, nullable=False),
    Column("timestamp", Float, nullable=False),
    Column("payload", JSON, nullable=False),
)
plans = Table(
    "plans",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)
approvals = Table(
    "approvals",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)
edges = Table(
    "dependency_edges",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("source", String, nullable=False),
    Column("target", String, nullable=False),
    Column("kind", String, nullable=False),
    UniqueConstraint("project_id", "source", "target", "kind"),
)


provider_configs = Table(
    "provider_configs",
    metadata,
    Column("id", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("config", JSON, nullable=False),
    Column("secret", String),
)
provider_revisions = Table(
    "provider_revisions",
    metadata,
    Column("provider_id", ForeignKey("provider_configs.id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("config", JSON, nullable=False),
    Column("secret", String),
)
preferences = Table(
    "preferences",
    metadata,
    Column("id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
)

cost_entries = Table(
    "cost_entries",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("reference", String, nullable=False, unique=True),
    Column("payload", JSON, nullable=False),
)

review_reports = Table(
    "review_reports",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("job_id", ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("payload", JSON, nullable=False),
)


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        path = settings.database_url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            settings.database_url,
            connect_args={
                "check_same_thread": False,
                "timeout": 30,
            },
        )

        @event.listens_for(self.engine, "connect")
        def pragmas(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")

    def migrate(self):
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
        config.set_main_option("sqlalchemy.url", self.settings.database_url.replace("%", "%%"))
        command.upgrade(config, "head")

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
