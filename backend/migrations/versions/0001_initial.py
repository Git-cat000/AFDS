# Frozen initial schema, independent of future app metadata.
from alembic import op
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
)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

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


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    metadata.drop_all(op.get_bind())
