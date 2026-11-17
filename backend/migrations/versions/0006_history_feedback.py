"""Bounded read indexes and immutable-source human creative feedback."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    created = (
        "COALESCE((payload ->> 'created_at')::double precision, 0)"
        if op.get_bind().dialect.name == "postgresql"
        else "COALESCE(json_extract(payload, '$.created_at'), 0)"
    )
    for table in ("jobs", "cost_entries"):
        op.execute(
            f"CREATE INDEX ix_{table}_project_created ON {table} "
            f"(project_id, {created} DESC, id DESC)"
        )
    op.create_table(
        "creative_feedback",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("shot_id", sa.String(), sa.ForeignKey("shots.id"), nullable=False),
        sa.Column("version_id", sa.String(), sa.ForeignKey("asset_versions.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_creative_feedback_project_id", "creative_feedback", ["project_id"])


def downgrade():
    op.drop_table("creative_feedback")
    for table in ("jobs", "cost_entries"):
        op.drop_index(f"ix_{table}_project_created", table_name=table)
