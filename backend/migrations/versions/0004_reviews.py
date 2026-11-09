"""Version-bound evidence reports for optional vision and offline reviews."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_review_reports_project_id", "review_reports", ["project_id"])


def downgrade():
    op.drop_table("review_reports")
