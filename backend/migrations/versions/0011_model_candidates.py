"""Immutable model candidates, legacy default connections and route previews."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    current = op.create_table(
        "provider_candidates",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String()),
    )
    history = op.create_table(
        "candidate_revisions",
        sa.Column("candidate_id", sa.String(100), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String()),
    )
    op.create_table(
        "route_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_route_decisions_project_id", "route_decisions", ["project_id"])
    legacy = sa.table(
        "provider_configs",
        sa.column("id", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("config", sa.JSON()),
        sa.column("secret", sa.String()),
    )
    versions = sa.table(
        "provider_revisions",
        sa.column("provider_id", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("config", sa.JSON()),
        sa.column("secret", sa.String()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(legacy)).mappings():
        config = {**row["config"], "label": f"默认 {row['id']}", "all_projects": True}
        connection.execute(
            current.insert().values(
                id=f"default-{row['id']}",
                kind=row["id"],
                revision=row["revision"],
                config=config,
                secret=row["secret"],
            )
        )
    for row in connection.execute(sa.select(versions)).mappings():
        config = {**row["config"], "label": f"默认 {row['provider_id']}", "all_projects": True}
        connection.execute(
            history.insert().values(
                candidate_id=f"default-{row['provider_id']}",
                kind=row["provider_id"],
                revision=row["revision"],
                config=config,
                secret=row["secret"],
            )
        )


def downgrade():
    op.drop_index("ix_route_decisions_project_id", table_name="route_decisions")
    op.drop_table("route_decisions")
    op.drop_table("candidate_revisions")
    op.drop_table("provider_candidates")
