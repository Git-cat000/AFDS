"""Versioned provider connections and encrypted credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_configs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String()),
    )
    op.create_table(
        "provider_revisions",
        sa.Column(
            "provider_id", sa.String(), sa.ForeignKey("provider_configs.id"), primary_key=True
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String()),
    )
    op.create_table(
        "preferences",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("preferences")
    op.drop_table("provider_revisions")
    op.drop_table("provider_configs")
