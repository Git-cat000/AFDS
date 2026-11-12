"""Read-only local diagnostics and bounded, observed task/media relationships."""

import importlib
import math
import re
import shutil
import time

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.budget import BudgetService
from app.errors import DomainError
from app.schemas import BudgetPolicy

WORKER_STALE_SECONDS = 15
JOB_LIMIT = 50
MEDIA_LIMIT = 100
EDGE_LIMIT = 300
OPERATIONS = {
    "generate_video",
    "generate_image",
    "generate_speech",
    "render_timeline",
    "import_audio",
    "review_shot",
    "export_project",
    "restore_project",
}


def pulse_worker(connection, owner, now=None):
    now = time.time() if now is None else now
    statement = insert(db.worker_heartbeats).values(owner=owner, started_at=now, last_seen_at=now)
    connection.execute(
        statement.on_conflict_do_update(index_elements=["owner"], set_={"last_seen_at": now})
    )


def native_otio_available():
    try:
        importlib.import_module("opentimelineio")
        return True
    except (ImportError, OSError):
        return False


class OperationsService:
    def __init__(self, database):
        self.database = database

    def status(self):
        now = time.time()
        database = {"status": "unavailable", "schema_version": None}
        worker = {
            "status": "unknown",
            "online_count": None,
            "last_seen_age_seconds": None,
            "stale_after_seconds": WORKER_STALE_SECONDS,
        }
        try:
            with self.database.engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                last_seen = db.worker_heartbeats.c.last_seen_at
                online_count, latest = connection.execute(
                    select(
                        func.count().filter(last_seen.between(now - WORKER_STALE_SECONDS, now + 1)),
                        func.max(last_seen),
                    )
                ).one()
                database = {"status": "available", "schema_version": version}
                worker.update(
                    status="online" if online_count else "offline",
                    online_count=online_count,
                    last_seen_age_seconds=max(0, now - latest) if latest is not None else None,
                )
        except SQLAlchemyError:
            pass
        tools = {}
        for name in ("ffmpeg", "ffprobe"):
            try:
                self.database.settings.binary(name)
                tools[name] = "available"
            except (FileNotFoundError, OSError):
                tools[name] = "unavailable"
        tools["otio"] = "available" if native_otio_available() else "unavailable"
        storage = {"status": "unavailable", "free_bytes": None, "total_bytes": None}
        try:
            usage = shutil.disk_usage(self.database.settings.data_dir)
            storage.update(status="available", free_bytes=usage.free, total_bytes=usage.total)
        except OSError:
            pass
        ok = (
            database["status"] == "available"
            and storage["status"] == "available"
            and all(value == "available" for value in tools.values())
            and worker["status"] == "online"
        )
        return {
            "status": "ok" if ok else "degraded",
            "deployment": "local_single_user",
            "database": database,
            "worker": worker,
            "tools": tools,
            "storage": storage,
            "checked_at": now,
        }

    def project(self, project_id):
        with self.database.engine.connect() as connection:
            header = (
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
            if header is None:
                raise DomainError("NOT_FOUND", "Project not found", 404)
            apr = dict(header)
            apr["budget"] = BudgetPolicy.model_validate(apr["budget"] or {}).model_dump(mode="json")
            counts = dict(
                connection.execute(
                    select(db.jobs.c.state, func.count())
                    .where(db.jobs.c.project_id == str(project_id))
                    .group_by(db.jobs.c.state)
                ).all()
            )
            payload = db.jobs.c.payload
            query = (
                select(
                    db.jobs.c.id,
                    db.jobs.c.state,
                    payload["operation"].as_string().label("operation"),
                    payload["elapsed_ms"].as_float().label("elapsed_ms"),
                    payload["created_at"].as_float().label("created_at"),
                    payload["error"]["code"].as_string().label("error_code"),
                    payload["output_asset_ids"].label("outputs"),
                    payload["dependencies"].label("dependencies"),
                    payload["request"]["items"].label("items"),
                    payload["request"]["audio_items"].label("audio_items"),
                    payload["request"]["asset_id"].as_string().label("asset_id"),
                    payload["request"]["neighbors"].label("neighbors"),
                    payload["request"]["asset_manifest"].label("asset_manifest"),
                )
                .where(db.jobs.c.project_id == str(project_id))
                .order_by(payload["created_at"].as_float().desc(), db.jobs.c.id)
                .limit(200)
            )
            recent = list(connection.execute(query).mappings())
            elapsed = sorted(
                row["elapsed_ms"]
                for row in recent
                if row["state"] in {"completed", "failed", "needs_review"}
                and isinstance(row["elapsed_ms"], (int, float))
                and math.isfinite(row["elapsed_ms"])
                and row["elapsed_ms"] >= 0
            )
            asset_count = connection.execute(
                select(func.count())
                .select_from(db.assets)
                .where(db.assets.c.project_id == str(project_id))
            ).scalar_one()
            files = (
                select(
                    db.assets.c.path,
                    func.max(db.assets.c.payload["size_bytes"].as_integer()).label("size_bytes"),
                )
                .where(db.assets.c.project_id == str(project_id))
                .group_by(db.assets.c.path)
                .subquery()
            )
            unique_files, media_bytes = connection.execute(
                select(func.count(), func.coalesce(func.sum(files.c.size_bytes), 0)).select_from(
                    files
                )
            ).one()
            budget = BudgetService(self.database).summary(connection, apr, include_entries=False)
            graph = self.graph(
                connection, project_id, recent[:JOB_LIMIT], sum(counts.values()) > JOB_LIMIT
            )
        total = sum(counts.values())
        return {
            "project_id": str(project_id),
            "revision": apr["revision"],
            "jobs": {
                "total": total,
                "state_counts": counts,
                "active_count": sum(
                    counts.get(s, 0)
                    for s in (
                        "queued",
                        "claimed",
                        "running",
                        "verifying",
                        "retry_wait",
                        "cancel_requested",
                    )
                ),
                "elapsed_ms": {
                    "sample_count": len(elapsed),
                    "p50": elapsed[math.ceil(len(elapsed) * 0.5) - 1] if elapsed else None,
                    "p95": elapsed[math.ceil(len(elapsed) * 0.95) - 1] if elapsed else None,
                },
                "sample_limit": 200,
            },
            "media": {
                "asset_count": asset_count,
                "unique_files": unique_files,
                "bytes": media_bytes,
            },
            "budget": budget,
            "graph": graph,
        }

    def graph(self, connection, project_id, rows, truncated):
        jobs = {row["id"] for row in rows}
        producers = {aid: row["id"] for row in rows for aid in row["outputs"] or []}
        nodes = []
        for row in rows:
            operation = row["operation"] if row["operation"] in OPERATIONS else "unknown"
            code = row["error_code"]
            nodes.append(
                {
                    "id": row["id"],
                    "type": "job",
                    "label": operation,
                    "operation": operation,
                    "state": row["state"],
                    "error_code": code
                    if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code)
                    else None,
                }
            )
        edges, media = set(), set()
        for row in rows:
            for dependency in row["dependencies"] or []:
                if dependency in jobs and dependency != row["id"]:
                    edges.add((dependency, row["id"], "dependency"))
                else:
                    truncated = True
            inputs = {row["asset_id"]} if row["asset_id"] else set()
            for key in ("items", "audio_items", "neighbors", "asset_manifest"):
                inputs.update(
                    item["asset_id"]
                    for item in row[key] or []
                    if isinstance(item, dict) and item.get("asset_id")
                )
            for aid in sorted(inputs):
                producer = producers.get(aid)
                if producer and producer != row["id"]:
                    edges.add((producer, row["id"], "media"))
                else:
                    if aid not in media:
                        if len(media) >= MEDIA_LIMIT:
                            truncated = True
                            continue
                        media.add(aid)
                        nodes.append(
                            {
                                "id": f"asset:{aid}",
                                "type": "asset",
                                "label": "unavailable_media",
                                "media_type": "unknown",
                            }
                        )
                    edges.add((f"asset:{aid}", row["id"], "media"))
        if media:
            known_media = dict(
                connection.execute(
                    select(db.assets.c.id, db.assets.c.payload["media_type"].as_string()).where(
                        db.assets.c.project_id == str(project_id), db.assets.c.id.in_(media)
                    )
                ).all()
            )
            for node in nodes:
                if node["type"] == "asset" and node["id"][6:] in known_media:
                    node["label"] = "existing_media"
                    node["media_type"] = known_media[node["id"][6:]]
        if len(edges) > EDGE_LIMIT:
            truncated = True
        return {
            "nodes": nodes,
            "edges": [
                {"source": a, "target": b, "kind": k} for a, b, k in sorted(edges)[:EDGE_LIMIT]
            ],
            "job_limit": JOB_LIMIT,
            "media_limit": MEDIA_LIMIT,
            "truncated": truncated,
            "relationship": "observed_media_lineage",
        }
