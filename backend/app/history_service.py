"""Bounded project-owned history projections and incremental event batches."""

import base64
import time
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import and_, func, literal_column, or_, select

from app import db
from app.budget import BudgetService, money
from app.errors import DomainError
from app.schemas import BudgetPolicy, Schema

ACTIVE_STATES = {"queued", "claimed", "running", "verifying", "retry_wait", "cancel_requested"}
StateFilter = Literal["all", "active", "completed", "failed", "cancelled", "needs_review"]


class Cursor(Schema):
    kind: Literal["jobs", "cost", "revisions", "feedback"]
    project_id: UUID
    state: str = Field(max_length=40)
    stamp: int | float = Field(ge=0, allow_inf_nan=False)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


def created_at(table):
    return func.coalesce(func.json_extract(table.c.payload, literal_column("'$.created_at'")), 0)


def owned_header(connection, project_id):
    row = (
        connection.execute(
            select(
                db.projects.c.id.label("project_id"),
                db.projects.c.revision,
                db.projects.c.payload["budget"].label("budget"),
            ).where(db.projects.c.id == str(project_id), db.projects.c.archived.is_(False))
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DomainError("NOT_FOUND", "Project not found", 404)
    result = dict(row)
    result["budget"] = BudgetPolicy.model_validate(result["budget"] or {}).model_dump(mode="json")
    return result


def page_rows(connection, query, stamp, key, project_id, kind, limit, cursor, state="all"):
    if cursor:
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            value = Cursor.model_validate_json(raw)
            if (
                value.kind != kind
                or str(value.project_id) != str(project_id)
                or value.state != state
            ):
                raise ValueError("Cursor context changed")
        except (ValueError, ValidationError, UnicodeError) as error:
            raise DomainError(
                "INVALID_CURSOR", "Cursor does not match this project or filter", 422
            ) from error
        query = query.where(or_(stamp < value.stamp, and_(stamp == value.stamp, key < value.key)))
    rows = [
        dict(row)
        for row in connection.execute(
            query.add_columns(stamp.label("_stamp"), key.label("_key"))
            .order_by(stamp.desc(), key.desc())
            .limit(limit + 1)
        ).mappings()
    ]
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if rows and has_more:
        payload = Cursor(
            kind=kind,
            project_id=project_id,
            state=state,
            stamp=rows[-1]["_stamp"],
            key=str(rows[-1]["_key"]),
        )
        next_cursor = (
            base64.urlsafe_b64encode(payload.model_dump_json().encode()).decode().rstrip("=")
        )
    for row in rows:
        row.pop("_stamp")
        row.pop("_key")
    return {"items": rows, "next_cursor": next_cursor, "has_more": has_more}


class HistoryService:
    def __init__(self, database):
        self.database = database

    def jobs(self, project_id, limit, cursor=None, state="all"):
        with self.database.engine.connect() as connection:
            owned_header(connection, project_id)
            counts = dict(
                connection.execute(
                    select(db.jobs.c.state, func.count())
                    .where(db.jobs.c.project_id == str(project_id))
                    .group_by(db.jobs.c.state)
                ).all()
            )
            active_operations = dict(
                connection.execute(
                    select(db.jobs.c.payload["operation"].as_string(), func.count())
                    .where(
                        db.jobs.c.project_id == str(project_id), db.jobs.c.state.in_(ACTIVE_STATES)
                    )
                    .group_by(db.jobs.c.payload["operation"].as_string())
                ).all()
            )
            payload = db.jobs.c.payload
            names = (
                "operation",
                "target_entity_id",
                "provider_id",
                "model_id",
                "external_task_id",
                "remote_notice",
                "result_plan_id",
            )
            query = select(
                db.jobs.c.id,
                db.jobs.c.project_id,
                db.jobs.c.state,
                db.jobs.c.attempt,
                *[payload[name].as_string().label(name) for name in names],
                payload["progress"].as_integer().label("progress"),
                payload["elapsed_ms"].as_integer().label("elapsed_ms"),
                payload["error"].label("error"),
                payload["artifact"].label("artifact"),
                payload["input_summary"].label("input_summary"),
                payload["request"]["provider_config"]["protocol"].as_string().label("protocol"),
            ).where(db.jobs.c.project_id == str(project_id))
            total = sum(counts.values())
            if state == "active":
                query = query.where(db.jobs.c.state.in_(ACTIVE_STATES))
                total = sum(counts.get(item, 0) for item in ACTIVE_STATES)
            elif state != "all":
                query = query.where(db.jobs.c.state == state)
                total = counts.get(state, 0)
            result = page_rows(
                connection,
                query,
                created_at(db.jobs),
                db.jobs.c.id,
                project_id,
                "jobs",
                limit,
                cursor,
                state,
            )
            for item in result["items"]:
                item["progress"] = item["progress"] or 0
                item["input_summary"] = item["input_summary"] or {}
                if item["artifact"]:
                    item["artifact"].pop("relative_path", None)
            return {
                **result,
                "total": total,
                "state_counts": counts,
                "active_count": sum(counts.get(item, 0) for item in ACTIVE_STATES),
                "active_operations": active_operations,
            }

    def budget(self, project_id):
        with self.database.engine.connect() as connection:
            header = owned_header(connection, project_id)
            result = BudgetService(self.database).summary(connection, header, include_entries=False)
            result["entry_count"] = connection.execute(
                select(func.count())
                .select_from(db.cost_entries)
                .where(db.cost_entries.c.project_id == str(project_id))
            ).scalar_one()
            return result

    def entries(self, project_id, limit, cursor=None):
        with self.database.engine.connect() as connection:
            owned_header(connection, project_id)
            payload = db.cost_entries.c.payload
            query = select(
                db.cost_entries.c.id,
                db.cost_entries.c.reference,
                *[
                    payload[name].as_string().label(name)
                    for name in ("state", "currency", "provider_id")
                ],
                payload["estimate_micros"].as_integer().label("estimate_micros"),
                payload["actual_micros"].as_integer().label("actual_micros"),
                payload["reconcile_after"].as_float().label("reconcile_after"),
                created_at(db.cost_entries).label("created_at"),
            ).where(db.cost_entries.c.project_id == str(project_id))
            result = page_rows(
                connection,
                query,
                created_at(db.cost_entries),
                db.cost_entries.c.id,
                project_id,
                "cost",
                limit,
                cursor,
            )
            result["total"] = connection.execute(
                select(func.count())
                .select_from(db.cost_entries)
                .where(db.cost_entries.c.project_id == str(project_id))
            ).scalar_one()
            for item in result["items"]:
                item["can_reconcile"] = (
                    item["state"] == "pending" and (item["reconcile_after"] or 0) <= time.time()
                )
                item["estimate"] = (
                    money(item["estimate_micros"]) if item["estimate_micros"] is not None else None
                )
                item["actual"] = (
                    money(item["actual_micros"]) if item["actual_micros"] is not None else None
                )
            return result

    def revisions(self, project_id, limit, cursor=None):
        with self.database.engine.connect() as connection:
            owned_header(connection, project_id)
            query = select(
                db.revisions.c.revision,
                db.revisions.c.reason,
                db.revisions.c.author,
                db.revisions.c.created_at,
            ).where(db.revisions.c.project_id == str(project_id))
            result = page_rows(
                connection,
                query,
                db.revisions.c.revision,
                db.revisions.c.revision,
                project_id,
                "revisions",
                limit,
                cursor,
            )
            result["total"] = connection.execute(
                select(func.count())
                .select_from(db.revisions)
                .where(db.revisions.c.project_id == str(project_id))
            ).scalar_one()
            return result

    def activity(self, project_id, after=None, limit=200):
        with self.database.engine.connect() as connection:
            owned_header(connection, project_id)
            query = select(
                db.events.c.event_id, db.events.c.type, db.events.c.timestamp, db.events.c.job_id
            )
            query = query.where(db.events.c.project_id == str(project_id))
            if after is None:
                rows = list(
                    connection.execute(
                        query.order_by(db.events.c.event_id.desc()).limit(limit)
                    ).mappings()
                )
                rows.reverse()
                has_more = False
            else:
                rows = list(
                    connection.execute(
                        query.where(db.events.c.event_id > after)
                        .order_by(db.events.c.event_id)
                        .limit(limit + 1)
                    ).mappings()
                )
                has_more = len(rows) > limit
                rows = rows[:limit]
            return {
                "items": [dict(row) for row in rows],
                "has_more": has_more,
                "cursor": rows[-1]["event_id"] if rows else (after or 0),
            }
