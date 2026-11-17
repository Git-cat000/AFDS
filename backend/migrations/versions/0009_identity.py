"""Application identities, project scopes and collaboration records."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String()),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_table(
        "project_skill_packages",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column(
            "package_id", sa.String(64), sa.ForeignKey("skill_packages.id"), primary_key=True
        ),
    )
    op.create_table(
        "login_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_at", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_table(
        "workspace_operations",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "workspace_entities",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("entity_id", sa.String(200), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "presence",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT INTO users (id, username, display_name, password_hash, "
            "is_admin, active, created_at) VALUES "
            "(:id, :name, :display, NULL, :admin, :active, 0)"
        ).bindparams(
            id="00000000-0000-0000-0000-000000000001",
            name="_local",
            display="本机用户",
            admin=True,
            active=True,
        )
    )


def downgrade():
    for table in (
        "presence",
        "workspace_entities",
        "workspace_operations",
        "project_skill_packages",
        "project_members",
        "login_limits",
        "sessions",
        "users",
    ):
        op.drop_table(table)
