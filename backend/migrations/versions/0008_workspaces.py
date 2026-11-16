"""Project canvas aggregates with independent optimistic revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_workspaces",
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def downgrade():
    op.drop_table("project_workspaces")
