import hashlib
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
    func,
    select,
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
    Column("worker_node_id", String(100)),
    Column("worker_generation", String(32)),
    Column("required_capability", String(200), nullable=False, server_default="python"),
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


worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("owner", String, primary_key=True),
    Column("started_at", Float, nullable=False),
    Column("last_seen_at", Float, nullable=False),
)

creative_feedback = Table(
    "creative_feedback",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", ForeignKey("projects.id"), nullable=False, index=True),
    Column("shot_id", ForeignKey("shots.id"), nullable=False),
    Column("version_id", ForeignKey("asset_versions.id"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("payload", JSON, nullable=False),
)


skill_packages = Table(
    "skill_packages",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("created_at", Float, nullable=False),
    Column("summary", JSON, nullable=False),
    Column("payload", JSON, nullable=False),
)


workspaces = Table(
    "project_workspaces",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("author", String, nullable=False),
    Column("updated_at", Float, nullable=False),
)


users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("display_name", String(100), nullable=False),
    Column("password_hash", String),
    Column("is_admin", Boolean, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", Float, nullable=False),
)
sessions = Table(
    "sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("user_id", ForeignKey("users.id"), nullable=False, index=True),
    Column("csrf_hash", String(64), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False, index=True),
)
project_members = Table(
    "project_members",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("role", String(16), nullable=False),
    Column("created_at", Float, nullable=False),
)
project_skills = Table(
    "project_skill_packages",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("package_id", ForeignKey("skill_packages.id"), primary_key=True),
)
login_limits = Table(
    "login_limits",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("window_at", Float, nullable=False),
    Column("attempts", Integer, nullable=False),
)
workspace_operations = Table(
    "workspace_operations",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("id", String, primary_key=True),
    Column("actor_id", ForeignKey("users.id"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("payload", JSON, nullable=False),
)
workspace_entities = Table(
    "workspace_entities",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("entity_id", String(205), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)
worker_storage = Table(
    "worker_storage",
    metadata,
    Column("id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
)
worker_nodes = Table(
    "worker_nodes",
    metadata,
    Column("id", String(100), primary_key=True),
    Column("last_seen_at", Float, nullable=False),
    Column("payload", JSON, nullable=False),
)
presence = Table(
    "presence",
    metadata,
    Column("project_id", ForeignKey("projects.id"), primary_key=True),
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("session_id", String, primary_key=True),
    Column("expires_at", Float, nullable=False),
    Column("payload", JSON, nullable=False),
)


def lock_project(connection, project_id, *, force=False):
    if connection.dialect.name == "postgresql" and (
        force or connection.info.get("afds_write_transaction")
    ):
        connection.execute(
            select(projects.c.id).where(projects.c.id == str(project_id)).with_for_update()
        ).first()
    if connection.info.get("afds_write_transaction"):
        from app.identity_context import current_access, current_principal

        actor, access = current_principal.get(), current_access.get()
        if actor and actor.mode == "team" and access and access[0] == str(project_id):
            from app.access import require_membership

            require_membership(connection, project_id, actor, access[1])


def lock_named_resource(connection, name):
    if connection.dialect.name == "postgresql":
        identity = int.from_bytes(hashlib.sha256(f"afds:{name}".encode()).digest()[:8], signed=True)
        connection.execute(select(func.pg_advisory_xact_lock(identity)))


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        sqlite = settings.database_url.startswith("sqlite:///")
        if sqlite:
            path = settings.database_url.removeprefix("sqlite:///")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
            pool_pre_ping=True,
        )

        if sqlite:

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
    def transaction(self, *, snapshot=False):
        with self.engine.connect() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN" if snapshot else "BEGIN IMMEDIATE")
            else:
                if snapshot:
                    connection = connection.execution_options(isolation_level="REPEATABLE READ")
                connection.begin()
            connection.info["afds_write_transaction"] = not snapshot
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.info.pop("afds_write_transaction", None)
