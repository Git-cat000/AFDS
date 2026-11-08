"""Durable reservations and reconciled provider charges."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cost_entries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("reference", sa.String(), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_cost_entries_project_id", "cost_entries", ["project_id"])


def downgrade():
    op.drop_table("cost_entries")
