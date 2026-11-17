"""Project-locked canvas operations, durable replay and conditional own undo."""

import copy
import time

from sqlalchemy import delete, insert, select, update

from app import db
from app.collaboration_schema import OperationBatch, UndoBatch
from app.errors import DomainError
from app.identity_context import LOCAL_USER_ID
from app.project_engine import ProjectEngine
from app.workspace_schema import workspace_document
from app.workspace_service import read_workspace

EVENT_WINDOW = 10000


def entities(document):
    result = {}
    nodes = {node["id"]: node for node in document["nodes"]}
    for identity in sorted(set(nodes) | set(document["positions"]) | set(document["hiddenShots"])):
        value = {}
        if identity in nodes:
            value["node"] = nodes[identity]
        if identity in document["positions"]:
            value["position"] = document["positions"][identity]
        if identity in document["hiddenShots"]:
            value["hidden"] = True
        result["node/" + identity] = value
    result.update({"edge/" + edge["id"]: edge for edge in document["edges"]})
    return result


def entity_rows(connection, project_id):
    return {
        row["entity_id"]: dict(row)
        for row in connection.execute(
            select(db.workspace_entities).where(
                db.workspace_entities.c.project_id == str(project_id)
            )
        ).mappings()
    }


def put_entity(connection, project_id, key, version, value, *, exists):
    values = {"version": version, "payload": {"value": value}}
    if exists:
        connection.execute(
            update(db.workspace_entities)
            .where(
                db.workspace_entities.c.project_id == str(project_id),
                db.workspace_entities.c.entity_id == key,
            )
            .values(**values)
        )
    else:
        connection.execute(
            insert(db.workspace_entities).values(
                project_id=str(project_id), entity_id=key, **values
            )
        )


def synchronize_entities(connection, project_id, before, after):
    """Legacy document writes and restores also fence previously observed entities."""
    previous, current = entities(before), entities(after)
    rows = entity_rows(connection, project_id)
    for key in previous.keys() | current.keys():
        if previous.get(key) != current.get(key):
            put_entity(
                connection,
                project_id,
                key,
                rows.get(key, {}).get("version", 0) + 1,
                current.get(key),
                exists=key in rows,
            )


def replace_entity(document, key, value):
    identity = key[5:]
    if key.startswith("edge/"):
        document["edges"] = [edge for edge in document["edges"] if edge["id"] != identity]
        if value is not None:
            document["edges"].append(value)
        return
    document["nodes"] = [node for node in document["nodes"] if node["id"] != identity]
    document["positions"].pop(identity, None)
    document["hiddenShots"] = [item for item in document["hiddenShots"] if item != identity]
    if value is None:
        return
    if value.get("node"):
        document["nodes"].append(value["node"])
    if value.get("position") is not None:
        document["positions"][identity] = value["position"]
    if value.get("hidden"):
        document["hiddenShots"].append(identity)


class CollaborationService:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)

    def _snapshot(self, connection, project_id):
        result = read_workspace(connection, project_id)
        result["entity_versions"] = {
            key: row["version"] for key, row in entity_rows(connection, project_id).items()
        }
        return result

    def snapshot(self, project_id):
        with self.database.transaction(snapshot=True) as connection:
            self.engine.load(connection, project_id)
            return self._snapshot(connection, project_id)

    def _operation_result(self, connection, project_id, payload):
        if "result" in payload:
            return copy.deepcopy(payload["result"])
        stored = connection.execute(
            select(db.workspace_operations.c.payload).where(
                db.workspace_operations.c.project_id == str(project_id),
                db.workspace_operations.c.id == payload["result_id"],
            )
        ).scalar_one()
        return copy.deepcopy(stored["result"])

    def apply_operations(self, project_id, operations, actor):
        operations = OperationBatch(operations=operations).operations
        pid = str(project_id)
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, pid)
            current = self._snapshot(connection, pid)
            rows = {
                row["id"]: row
                for row in connection.execute(
                    select(db.workspace_operations).where(
                        db.workspace_operations.c.project_id == pid,
                        db.workspace_operations.c.id.in_(
                            [str(item.operation_id) for item in operations]
                        ),
                    )
                ).mappings()
            }
            for item in operations:
                old = rows.get(str(item.operation_id))
                if old and (
                    old["actor_id"] != actor
                    or old["payload"]["request"] != item.model_dump(mode="json")
                ):
                    raise DomainError(
                        "OPERATION_REBOUND", "Operation identity has different content", 409
                    )
            if len(rows) == len(operations):
                return self._operation_result(
                    connection, pid, rows[str(operations[-1].operation_id)]["payload"]
                )
            document = copy.deepcopy(current["document"])
            versions = dict(current["entity_versions"])
            records = []
            for item in operations:
                if str(item.operation_id) in rows:
                    continue
                key = ("edge/" if "edge" in item.kind else "node/") + item.entity_id
                version = versions.get(key, 0)
                if item.expected_entity_version != version:
                    raise DomainError(
                        "ENTITY_CONFLICT",
                        "Canvas item changed. Review this item.",
                        409,
                        {
                            "entity_id": item.entity_id,
                            "entity_version": version,
                            "value": entities(document).get(key),
                        },
                    )
                before = copy.deepcopy(entities(document).get(key))
                value = copy.deepcopy(before) or {}
                if item.kind in {"set_node", "set_edge", "upsert_edge"}:
                    value = item.value
                elif item.kind.startswith("remove_"):
                    value = None
                elif item.kind == "add_node":
                    if before is not None:
                        raise DomainError("ENTITY_EXISTS", "Canvas item already exists", 409)
                    value["node"] = item.value
                elif item.kind == "move_node":
                    value["position"] = item.value
                elif item.kind == "set_hidden":
                    value["hidden"] = item.value
                elif item.kind == "edit_note":
                    if value.get("node", {}).get("kind") != "note":
                        raise DomainError("INVALID_WORKSPACE", "Select an existing note", 422)
                    value["node"]["text"] = item.value
                replace_entity(document, key, value)
                versions[key] = version + 1
                records.append((item, key, before, version + 1))
            try:
                document = workspace_document(document, apr)
            except (ValueError, KeyError) as error:
                raise DomainError("INVALID_WORKSPACE", str(error), 422) from error
            self.engine.save_workspace(
                connection, apr, current["workspace_revision"], lambda _: document, actor
            )
            existing = entity_rows(connection, pid)
            final = entities(document)
            for key in {record[1] for record in records}:
                put_entity(
                    connection, pid, key, versions[key], final.get(key), exists=key in existing
                )
            result = self._snapshot(connection, pid)
            result_id = str(records[-1][0].operation_id)
            for item, key, before, version in records:
                payload = {
                    "request": item.model_dump(mode="json"),
                    "key": key,
                    "before": before,
                    "version": version,
                    "result_id": result_id,
                }
                if str(item.operation_id) == result_id:
                    payload["result"] = result
                connection.execute(
                    insert(db.workspace_operations).values(
                        project_id=pid,
                        id=str(item.operation_id),
                        actor_id=actor,
                        created_at=time.time(),
                        payload=payload,
                    )
                )
            return result

    def undo_operation(self, project_id, operation_id, actor):
        return self.undo_operations(project_id, [operation_id], actor)

    def undo_operations(self, project_id, operation_ids, actor):
        operation_ids = UndoBatch(operation_ids=operation_ids).operation_ids
        pid = str(project_id)
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, pid)
            result = None
            for operation_id in reversed(operation_ids):
                result = self._undo(connection, apr, operation_id, actor)
            return result

    def _undo(self, connection, apr, operation_id, actor):
        pid = apr["project_id"]
        row = (
            connection.execute(
                select(db.workspace_operations).where(
                    db.workspace_operations.c.project_id == pid,
                    db.workspace_operations.c.id == str(operation_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["actor_id"] != actor:
            raise DomainError("NOT_FOUND", "Own operation not found", 404)
        payload = copy.deepcopy(row["payload"])
        if payload.get("undo_result"):
            return payload["undo_result"]
        current = self._snapshot(connection, pid)
        key = payload["key"]
        version = current["entity_versions"].get(key, 0)
        if version != payload["version"]:
            raise DomainError(
                "ENTITY_CONFLICT",
                "A later change prevents undo",
                409,
                {"entity_id": key[5:], "entity_version": version},
            )
        document = copy.deepcopy(current["document"])
        replace_entity(document, key, payload["before"])
        self.engine.save_workspace(
            connection, apr, current["workspace_revision"], lambda _: document, actor
        )
        payload["undo_result"] = self._snapshot(connection, pid)
        connection.execute(
            update(db.workspace_operations)
            .where(
                db.workspace_operations.c.project_id == pid,
                db.workspace_operations.c.id == str(operation_id),
            )
            .values(payload=payload)
        )
        return payload["undo_result"]

    def events(self, project_id, after):
        pid = str(project_id)
        with self.database.engine.connect() as connection:
            if not connection.execute(
                select(db.projects.c.id).where(
                    db.projects.c.id == pid, db.projects.c.archived.is_(False)
                )
            ).first():
                raise DomainError("NOT_FOUND", "Project not found", 404)
            oldest = connection.execute(
                select(db.events.c.event_id)
                .where(db.events.c.project_id == pid)
                .order_by(db.events.c.event_id.desc())
                .offset(EVENT_WINDOW - 1)
                .limit(1)
            ).scalar_one_or_none()
            if after and oldest is not None and after < oldest:
                latest = connection.execute(
                    select(db.events.c.event_id)
                    .where(db.events.c.project_id == pid)
                    .order_by(db.events.c.event_id.desc())
                    .limit(1)
                ).scalar_one()
                return {"items": [], "cursor": latest, "reset": True}
            items = [
                dict(row)
                for row in connection.execute(
                    select(db.events)
                    .where(db.events.c.project_id == pid, db.events.c.event_id > after)
                    .order_by(db.events.c.event_id)
                    .limit(100)
                ).mappings()
            ]
        return {"items": items, "cursor": items[-1]["event_id"] if items else after}

    def presence(self, project_id, actor, session_id, entity_id):
        pid = str(project_id)
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, pid)
            document = read_workspace(connection, pid)["document"]
            ids = {shot["id"] for shot in apr["shots"]} | {node["id"] for node in document["nodes"]}
            if entity_id is not None and entity_id not in ids:
                raise DomainError(
                    "INVALID_WORKSPACE", "Editing item must belong to this canvas", 422
                )
            condition = (
                db.presence.c.project_id == pid,
                db.presence.c.user_id == actor,
                db.presence.c.session_id == session_id,
            )
            connection.execute(delete(db.presence).where(*condition))
            connection.execute(delete(db.presence).where(db.presence.c.expires_at <= time.time()))
            connection.execute(
                insert(db.presence).values(
                    project_id=pid,
                    user_id=actor,
                    session_id=session_id,
                    expires_at=time.time() + 15,
                    payload={"entity_id": entity_id},
                )
            )
        return self.online(pid)

    def online(self, project_id):
        with self.database.engine.connect() as connection:
            rows = connection.execute(
                select(db.presence, db.users.c.display_name)
                .join(db.users)
                .where(
                    db.presence.c.project_id == str(project_id),
                    db.presence.c.expires_at > time.time(),
                    db.users.c.active.is_(True),
                )
            ).mappings()
            result = []
            for row in rows:
                if self.database.settings.mode == "team":
                    member = connection.execute(
                        select(db.project_members.c.user_id).where(
                            db.project_members.c.project_id == str(project_id),
                            db.project_members.c.user_id == row["user_id"],
                        )
                    ).first()
                    session = connection.execute(
                        select(db.sessions.c.id).where(
                            db.sessions.c.id == row["session_id"],
                            db.sessions.c.user_id == row["user_id"],
                            db.sessions.c.expires_at > time.time(),
                        )
                    ).first()
                    if not member or not session or row["user_id"] == LOCAL_USER_ID:
                        continue
                result.append(
                    {
                        "user_id": row["user_id"],
                        "display_name": row["display_name"],
                        **row["payload"],
                    }
                )
            return result
