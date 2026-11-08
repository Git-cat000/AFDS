import copy
import time
from collections import deque

from pydantic import ValidationError
from sqlalchemy import delete, insert, select, update

from app import db
from app.errors import DomainError
from app.schemas import APR, ChangeSet, ProjectCreate, Shot, new_id

SHOT_FIELDS = {
    "title",
    "action",
    "emotion",
    "duration_frames",
    "camera.shot_size",
    "camera.movement",
    "locked",
    "status",
    "active_asset_version_id",
}
SPEC_FIELDS = {"action", "emotion", "duration_frames", "camera.shot_size", "camera.movement"}


def validate(apr):
    try:
        return APR.model_validate(apr).model_dump(mode="json")
    except (ValidationError, ValueError, KeyError) as error:
        raise DomainError("VALIDATION_ERROR", "Invalid APR or references", 422) from error


def emit(connection, project_id, kind, payload, job_id=None):
    connection.execute(
        insert(db.events).values(
            project_id=project_id,
            job_id=job_id,
            type=kind,
            timestamp=time.time(),
            payload=payload,
        )
    )


def find(items, entity_id):
    item = next((item for item in items if item["id"] == str(entity_id)), None)
    if item is None:
        raise DomainError("NOT_FOUND", "Resource does not belong to this project", 404)
    return item


def rebuild_timeline(apr, shot_ids=None):
    if shot_ids is None:
        shot_ids = [i["shot_id"] for i in apr["timeline"]["tracks"][0]["items"]]
    items = []
    position = 0
    for sid in shot_ids:
        shot = find(apr["shots"], sid)
        if shot["status"] == "archived":
            continue
        items.append(
            {
                "shot_id": str(sid),
                "start_frame": position,
                "duration_frames": shot["duration_frames"],
            }
        )
        position += shot["duration_frames"]
    apr["timeline"]["tracks"][0]["items"] = items
    apr["timeline"]["version"] += 1


class ProjectEngine:
    def __init__(self, database: db.Database):
        self.database = database

    def load(self, connection, project_id):
        row = (
            connection.execute(
                select(db.projects).where(
                    db.projects.c.id == str(project_id),
                    db.projects.c.archived.is_(False),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DomainError("NOT_FOUND", "Project not found", 404)
        apr = copy.deepcopy(row["payload"])
        for key, table in [
            ("scenes", db.scenes),
            ("shots", db.shots),
            ("assets", db.assets),
            ("asset_versions", db.versions),
        ]:
            apr[key] = [
                r[0]
                for r in connection.execute(
                    select(table.c.payload).where(table.c.project_id == str(project_id))
                )
            ]
        apr["scenes"].sort(key=lambda s: s["order"])
        apr["shots"].sort(key=lambda s: s["order"])
        from app.budget import BudgetService

        finance = BudgetService(self.database).summary(connection, apr)
        apr["budget"].update(spent=finance["spent"], reserved=finance["reserved"])
        return apr

    def read(self, project_id):
        with self.database.engine.connect() as connection:
            return self.load(connection, project_id)

    def check_revision(self, apr, base_revision):
        if apr["revision"] != base_revision:
            raise DomainError(
                "REVISION_CONFLICT",
                "Project changed. Refresh and preview again.",
                409,
                {"expected": apr["revision"], "received": base_revision},
            )

    def save(self, connection, apr, before, reason, author="user"):
        apr = validate(apr)
        from app.budget import BudgetService

        finance = BudgetService(self.database).summary(connection, apr)
        apr["budget"].update(spent=finance["spent"], reserved=finance["reserved"])
        pid = apr["project_id"]
        payload = {
            k: v for k, v in apr.items() if k not in {"scenes", "shots", "assets", "asset_versions"}
        }
        values = dict(
            revision=apr["revision"], payload=payload, archived=apr["status"] == "archived"
        )
        if before is None:
            connection.execute(insert(db.projects).values(id=pid, **values))
        else:
            connection.execute(update(db.projects).where(db.projects.c.id == pid).values(**values))
        for key, table in [
            ("scenes", db.scenes),
            ("shots", db.shots),
            ("asset_versions", db.versions),
        ]:
            existing = set(
                connection.execute(select(table.c.id).where(table.c.project_id == pid)).scalars()
            )
            for item in apr[key]:
                values = {"payload": item}
                if key == "shots":
                    values["scene_id"] = item["scene_id"]
                if key == "asset_versions":
                    values.update(shot_id=item["shot_id"], asset_id=item["asset_id"])
                if item["id"] in existing:
                    connection.execute(
                        update(table).where(table.c.id == item["id"]).values(**values)
                    )
                else:
                    connection.execute(
                        insert(table).values(id=item["id"], project_id=pid, **values)
                    )
        connection.execute(
            insert(db.revisions).values(
                project_id=pid,
                revision=apr["revision"],
                reason=reason,
                author=author,
                before=before,
                after=apr,
                created_at=time.time(),
            )
        )
        self.refresh_edges(connection, apr)
        emit(connection, pid, "project.revised", {"revision": apr["revision"], "reason": reason})
        return apr

    def refresh_edges(self, connection, apr):
        pid = apr["project_id"]
        connection.execute(delete(db.edges).where(db.edges.c.project_id == pid))
        timeline_shots = {i["shot_id"] for i in apr["timeline"]["tracks"][0]["items"]}
        for version in apr["asset_versions"]:
            connection.execute(
                insert(db.edges).values(
                    project_id=pid,
                    source=version["shot_id"],
                    target=version["id"],
                    kind="generation",
                )
            )
            if version["shot_id"] in timeline_shots:
                connection.execute(
                    insert(db.edges).values(
                        project_id=pid,
                        source=version["id"],
                        target="render",
                        kind="composition",
                    )
                )

    def create(self, body: ProjectCreate):
        apr = APR(**body.model_dump()).model_dump(mode="json")
        with self.database.transaction() as connection:
            return self.save(connection, apr, None, "Create project")

    def mutate(self, project_id, base_revision, reason, operation, author="user"):
        with self.database.transaction() as connection:
            before = self.load(connection, project_id)
            self.check_revision(before, base_revision)
            after = copy.deepcopy(before)
            operation(after, connection)
            after["revision"] += 1
            return self.save(connection, after, before, reason, author)

    def invalidate(self, connection, apr, shot_ids):
        graph = {}
        for edge in connection.execute(
            select(db.edges).where(db.edges.c.project_id == apr["project_id"])
        ).mappings():
            graph.setdefault(edge["source"], []).append(edge["target"])
        affected = set(shot_ids)
        queue = deque(shot_ids)
        while queue:
            for target in graph.get(queue.popleft(), []):
                if target not in affected:
                    affected.add(target)
                    queue.append(target)
        for version in apr["asset_versions"]:
            if version["id"] in affected:
                version["status"] = "stale"
        for shot in apr["shots"]:
            if shot["id"] in shot_ids:
                if shot["status"] == "archived":
                    continue
                protected = shot["locked"] or shot["status"] in {"approved", "needs_review"}
                shot["status"] = "needs_review" if protected else "stale"
        if shot_ids:
            apr["render_status"] = "stale"

    def apply_changes(self, connection, apr, body: ChangeSet):
        affected = set()
        changes = []
        for operation in body.operations:
            field, value = operation.field, operation.value
            if operation.entity_type == "shot":
                target = find(apr["shots"], operation.entity_id)
                allowed = SHOT_FIELDS
                if field not in allowed:
                    raise DomainError("FIELD_FORBIDDEN", "Field cannot be changed", 422)
                protected = target["locked"] or target["status"] in {"approved", "needs_review"}
                if protected and field != "locked" and not body.approved:
                    raise DomainError(
                        "APPROVAL_REQUIRED", "Unlock or authorize this shot edit", 409
                    )
            elif operation.entity_type == "scene":
                target = find(apr["scenes"], operation.entity_id)
                allowed = {"title", "description"}
            else:
                if str(operation.entity_id) != apr["project_id"]:
                    raise DomainError("NOT_FOUND", "Project mismatch", 404)
                target, allowed = apr, {"title", "brief", "style_bible"}
            if field not in allowed:
                raise DomainError("FIELD_FORBIDDEN", "Field cannot be changed", 422)
            container = target
            parts = field.split(".")
            if len(parts) == 2:
                container = target[parts[0]]
            old = container.get(parts[-1])
            if old == value:
                continue
            if field == "status" and (
                not isinstance(value, str) or value not in {"draft", "approved", "archived"}
            ):
                raise DomainError("INVALID_STATUS", "Status is controlled by the engine", 422)
            if field == "status" and value == "approved" and not target["active_asset_version_id"]:
                raise DomainError("MISSING_ASSET", "Generate and select a video first", 409)
            if field == "active_asset_version_id":
                version = find(apr["asset_versions"], value)
                if version["shot_id"] != target["id"] or version["status"] == "stale":
                    raise DomainError("INVALID_ASSET", "Candidate is foreign or stale", 422)
                if old is not None and not body.approved:
                    raise DomainError(
                        "APPROVAL_REQUIRED", "Approve replacing the selected asset", 409
                    )
                target["status"] = "ready"
                apr["render_status"] = "stale"
            container[parts[-1]] = value
            if operation.entity_type == "shot":
                try:
                    canonical = Shot.model_validate(target).model_dump(mode="json")
                except ValidationError as error:
                    raise DomainError(
                        "VALIDATION_ERROR", "Invalid shot field value", 422
                    ) from error
                target.clear()
                target.update(canonical)
            changes.append(
                {
                    "entity_id": str(operation.entity_id),
                    "field": field,
                    "before": old,
                    "after": value,
                }
            )
            if operation.entity_type == "shot" and field in SPEC_FIELDS:
                affected.add(target["id"])
            if field == "style_bible":
                if apr["shots"] and not body.approved:
                    raise DomainError("APPROVAL_REQUIRED", "Approve the global style change", 409)
                affected.update(s["id"] for s in apr["shots"])
            if field == "status" and value == "archived":
                apr["render_status"] = "stale"
        self.invalidate(connection, apr, affected)
        rebuild_timeline(apr)
        validate(apr)
        return {
            "changes": changes,
            "affected_shot_ids": sorted(affected),
            "render_invalidated": bool(affected) or apr["render_status"] == "stale",
            "estimated_cost": {"status": "known", "amount": 0, "currency": "USD"},
        }

    def changeset(self, project_id, body, commit=False):
        with self.database.transaction() as connection:
            before = self.load(connection, project_id)
            self.check_revision(before, body.base_revision)
            after = copy.deepcopy(before)
            preview = self.apply_changes(connection, after, body)
            if not commit:
                return preview
            if body.approved:
                self.approve(connection, project_id, "changeset", body.model_dump(mode="json"))
            after["revision"] += 1
            return self.save(connection, after, before, body.reason)

    def approve(self, connection, project_id, scope, details):
        connection.execute(
            insert(db.approvals).values(
                id=new_id(),
                project_id=str(project_id),
                payload={
                    "scope": scope,
                    "actor": "local-user",
                    "decision": "approved",
                    "timestamp": time.time(),
                    "details": details,
                },
            )
        )

    def revert(self, project_id, revision, body):
        if not body.approved:
            raise DomainError("APPROVAL_REQUIRED", "Approve reverting this revision", 409)

        def restore(apr, connection):
            row = (
                connection.execute(
                    select(db.revisions).where(
                        db.revisions.c.project_id == str(project_id),
                        db.revisions.c.revision == revision,
                    )
                )
                .mappings()
                .first()
            )
            if not row or row["before"] is None:
                raise DomainError("NOT_FOUND", "Reversible revision not found", 404)
            if revision != apr["revision"]:
                raise DomainError("REVERT_CONFLICT", "v0.1 reverts the latest revision only", 409)
            previous = copy.deepcopy(row["before"])
            for key in ("assets", "asset_versions"):
                previous[key] = apr[key]
            current_ids = {s["id"] for s in previous["shots"]}
            for shot in apr["shots"]:
                if shot["id"] not in current_ids:
                    archived = copy.deepcopy(shot)
                    archived["status"] = "archived"
                    previous["shots"].append(archived)
            scene_ids = {s["id"] for s in previous["scenes"]}
            previous["scenes"].extend(s for s in apr["scenes"] if s["id"] not in scene_ids)
            for scene in previous["scenes"]:
                scene["shot_ids"] = [
                    s["id"] for s in previous["shots"] if s["scene_id"] == scene["id"]
                ]
            from app.execution.queue import shot_request

            current_shots = {s["id"]: s for s in apr["shots"]}
            changed = [
                s["id"]
                for s in previous["shots"]
                if s["id"] not in current_shots
                or shot_request(previous, s) != shot_request(apr, current_shots[s["id"]])
            ]
            previous["revision"] = apr["revision"]
            apr.clear()
            apr.update(previous)
            self.invalidate(connection, apr, changed)
            self.approve(connection, project_id, "revert", {"revision": revision})

        return self.mutate(project_id, body.base_revision, f"Revert revision {revision}", restore)

    def import_apr(self, source: APR):
        apr = source.model_dump(mode="json")
        remap = {
            old: new_id()
            for old in [apr["project_id"]]
            + [x["id"] for key in ("scenes", "shots", "characters", "locations") for x in apr[key]]
        }
        apr["project_id"] = remap[apr["project_id"]]
        apr["revision"] = 1
        apr["status"] = "draft"
        apr["assets"], apr["asset_versions"] = [], []
        apr["render_asset_id"], apr["render_status"] = None, "missing"
        for key in ("characters", "locations", "scenes", "shots"):
            for item in apr[key]:
                item["id"] = remap[item["id"]]
                if "reference_asset_ids" in item:
                    item["reference_asset_ids"] = []
        for scene in apr["scenes"]:
            scene["shot_ids"] = [remap[s] for s in scene["shot_ids"]]
            if scene["location_id"]:
                scene["location_id"] = remap[scene["location_id"]]
        for shot in apr["shots"]:
            shot["scene_id"] = remap[shot["scene_id"]]
            shot["character_ids"] = [remap[c] for c in shot["character_ids"]]
            shot["active_asset_version_id"] = None
            shot["status"], shot["locked"] = "draft", False
        for item in apr["timeline"]["tracks"][0]["items"]:
            item["shot_id"] = remap[item["shot_id"]]
        with self.database.transaction() as connection:
            return self.save(
                connection, apr, None, "Import APR structure; media requires regeneration"
            )
