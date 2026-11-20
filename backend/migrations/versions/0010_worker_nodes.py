"""Worker capabilities, shared-store identity and durable assignments."""

from pathlib import PurePosixPath

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_storage",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "worker_nodes",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("last_seen_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.add_column("jobs", sa.Column("worker_node_id", sa.String(100)))
    op.add_column("jobs", sa.Column("worker_generation", sa.String(32)))
    op.add_column(
        "jobs",
        sa.Column("required_capability", sa.String(200), nullable=False, server_default="python"),
    )
    op.create_index(
        "ix_jobs_worker_assignment", "jobs", ["worker_node_id", "state", "lease_expires_at"]
    )
    jobs = sa.table(
        "jobs",
        sa.column("id", sa.String()),
        sa.column("payload", sa.JSON()),
        sa.column("required_capability", sa.String()),
    )
    media = {
        "generate_video",
        "generate_image",
        "generate_speech",
        "render_timeline",
        "render_preview",
        "import_audio",
        "import_image",
        "import_video",
        "analyze_beats",
        "transcribe_audio",
    }
    for row in op.get_bind().execute(sa.select(jobs.c.id, jobs.c.payload)).mappings():
        payload = row["payload"]
        requirement = "media" if payload["operation"] in media else "python"
        if payload["operation"] == "run_skill_script":
            request = payload["request"]
            if request.get("execution_mode") == "container":
                policy = request.get("execution_policy", {})
                requirement = (
                    f"sandbox:{policy.get('image_id', '')}:{policy.get('policy_version', '')}"
                )
            else:
                requirement = {
                    ".py": "python",
                    ".js": "node",
                    ".mjs": "node",
                    ".cjs": "node",
                    ".ps1": "powershell",
                    ".sh": "bash",
                }.get(PurePosixPath(request.get("script_path", "")).suffix.lower(), "unsupported")
        op.get_bind().execute(
            jobs.update().where(jobs.c.id == row["id"]).values(required_capability=requirement)
        )


def downgrade():
    op.drop_index("ix_jobs_worker_assignment", table_name="jobs")
    for column in ("required_capability", "worker_generation", "worker_node_id"):
        op.drop_column("jobs", column)
    op.drop_table("worker_nodes")
    op.drop_table("worker_storage")
