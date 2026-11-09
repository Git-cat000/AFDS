"""Local Worker liveness independent of whether it currently owns a media job."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_heartbeats",
        sa.Column("owner", sa.String(), primary_key=True),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=False),
    )


def downgrade():
    op.drop_table("worker_heartbeats")
