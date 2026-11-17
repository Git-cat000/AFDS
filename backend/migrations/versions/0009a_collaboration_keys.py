"""Retain supported 200-character identities with their entity namespace."""

import sqlalchemy as sa
from alembic import op

revision = "0009a"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "workspace_entities",
            "entity_id",
            existing_type=sa.String(200),
            type_=sa.String(205),
            existing_nullable=False,
        )


def downgrade():
    # SQLite has no varchar length enforcement. PostgreSQL validates shrinkage.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "workspace_entities",
            "entity_id",
            existing_type=sa.String(205),
            type_=sa.String(200),
            existing_nullable=False,
        )
