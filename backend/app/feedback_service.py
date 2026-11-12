"""Human creative judgments bound to immutable owned footage and optional evidence."""

import time
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, insert, select

from app import db
from app.errors import DomainError
from app.history_service import owned_header, page_rows
from app.project_engine import ProjectEngine, find
from app.schemas import RevisionRequest, new_id

Score = Annotated[int, Field(strict=True, ge=1, le=5)]


class FeedbackRequest(RevisionRequest):
    shot_id: UUID
    version_id: UUID
    report_id: UUID | None = None
    verdict: Literal["accepted", "rejected", "needs_work"]
    scores: dict[Literal["continuity", "action", "overall"], Score] = Field(
        default_factory=dict, max_length=3
    )
    note: str = Field(default="", max_length=2000)


class FeedbackService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)

    def create(self, project_id, body):
        if not body.approved:
            raise DomainError("APPROVAL_REQUIRED", "Confirm this human feedback", 409)
        record = {}

        def apply(apr, connection):
            shot = find(apr["shots"], body.shot_id)
            version = find(apr["asset_versions"], body.version_id)
            if version["shot_id"] != shot["id"]:
                raise DomainError("NOT_FOUND", "Version does not belong to this shot", 404)
            asset = find(apr["assets"], version["asset_id"])
            if body.report_id:
                report = connection.execute(
                    select(
                        db.review_reports.c.payload, db.jobs.c.payload["request"].label("request")
                    )
                    .join(db.jobs, db.review_reports.c.job_id == db.jobs.c.id)
                    .where(
                        db.review_reports.c.id == str(body.report_id),
                        db.review_reports.c.project_id == str(project_id),
                        db.jobs.c.project_id == str(project_id),
                    )
                ).first()
                if report is None:
                    raise DomainError("NOT_FOUND", "Evidence report not found in project", 404)
                evidence, request = report
                if (
                    evidence["shot_id"] != shot["id"]
                    or evidence["asset_version_id"] != version["id"]
                    or request["asset_id"] != asset["id"]
                    or request["asset_sha256"] != asset["sha256"]
                ):
                    raise DomainError(
                        "FEEDBACK_SOURCE_MISMATCH", "Evidence report refers to another source", 409
                    )
            record.update(body.model_dump(mode="json", exclude={"base_revision", "approved"}))
            record.update(
                id=new_id(),
                project_id=apr["project_id"],
                created_at=time.time(),
                source="human",
                source_revision=apr["revision"],
                asset_id=asset["id"],
                asset_sha256=asset["sha256"],
            )
            connection.execute(
                insert(db.creative_feedback).values(
                    id=record["id"],
                    project_id=apr["project_id"],
                    shot_id=shot["id"],
                    version_id=version["id"],
                    created_at=record["created_at"],
                    payload=record,
                )
            )
            self.engine.approve(
                connection,
                project_id,
                "human_feedback",
                {
                    "feedback_id": record["id"],
                    "version_id": version["id"],
                    "asset_sha256": asset["sha256"],
                },
            )

        project = self.engine.mutate(
            project_id, body.base_revision, "Record human creative feedback", apply
        )
        return {"feedback": record, "project": project}

    def page(self, project_id, limit, cursor=None, shot_id=None):
        with self.database.engine.connect() as connection:
            owned_header(connection, project_id)
            query = select(db.creative_feedback.c.payload).where(
                db.creative_feedback.c.project_id == str(project_id)
            )
            count = (
                select(func.count())
                .select_from(db.creative_feedback)
                .where(db.creative_feedback.c.project_id == str(project_id))
            )
            if shot_id:
                if (
                    connection.execute(
                        select(db.shots.c.id).where(
                            db.shots.c.id == str(shot_id), db.shots.c.project_id == str(project_id)
                        )
                    ).first()
                    is None
                ):
                    raise DomainError("NOT_FOUND", "Shot not found in project", 404)
                query = query.where(db.creative_feedback.c.shot_id == str(shot_id))
                count = count.where(db.creative_feedback.c.shot_id == str(shot_id))
            result = page_rows(
                connection,
                query,
                db.creative_feedback.c.created_at,
                db.creative_feedback.c.id,
                project_id,
                "feedback",
                limit,
                cursor,
                str(shot_id) if shot_id else "all",
            )
            rows = [row["payload"] for row in result["items"]]
            shot_ids = {row["shot_id"] for row in rows}
            version_ids = {row["version_id"] for row in rows}
            selections = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    select(
                        db.shots.c.id,
                        db.shots.c.payload["active_asset_version_id"].as_string(),
                        db.shots.c.payload["status"].as_string(),
                    ).where(db.shots.c.project_id == str(project_id), db.shots.c.id.in_(shot_ids))
                )
            }
            statuses = dict(
                connection.execute(
                    select(db.versions.c.id, db.versions.c.payload["status"].as_string()).where(
                        db.versions.c.project_id == str(project_id),
                        db.versions.c.id.in_(version_ids),
                    )
                ).all()
            )
            for row in rows:
                selected, status = selections.get(row["shot_id"], (None, None))
                row["matches_current_selection"] = (
                    selected == row["version_id"]
                    and status not in {"stale", "needs_review", "archived"}
                    and statuses.get(row["version_id"]) == "ready"
                )
            result.update(items=rows, total=connection.execute(count).scalar_one())
            return result
