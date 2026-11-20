"""Trusted worker registrations, shared-store proofs and incarnation fencing."""

import json
import platform
import time
from pathlib import Path
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import insert, select, update

from app import APP_VERSION, db
from app.errors import DomainError
from app.operations_service import OPERATIONS
from app.schemas import Schema
from app.storage import contained, file_hash

STALE_SECONDS = 15
MEDIA_OPERATIONS = {
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
SCRIPT_RUNTIMES = {
    ".py": "python",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".ps1": "powershell",
    ".sh": "bash",
}


class NodeInfo(Schema):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=120)
    operations: list[str] = Field(min_length=1, max_length=len(OPERATIONS))
    slots: int = Field(default=1, ge=1, le=32)

    @model_validator(mode="after")
    def supported_operations(self):
        if set(self.operations) - OPERATIONS or len(set(self.operations)) != len(self.operations):
            raise ValueError("Choose unique supported worker operations")
        return self


def required_capability(job):
    if job["operation"] in MEDIA_OPERATIONS:
        return "media"
    if job["operation"] != "run_skill_script":
        return "python"
    request = job["request"]
    if request.get("execution_mode") == "container":
        policy = request.get("execution_policy", {})
        return f"sandbox:{policy.get('image_id', '')}:{policy.get('policy_version', '')}"
    return SCRIPT_RUNTIMES.get(Path(request.get("script_path", "")).suffix.lower(), "unsupported")


def eligible(node, job):
    return bool(
        node
        and not node["draining"]
        and node["last_seen_at"] >= time.time() - STALE_SECONDS
        and node["storage"].get("status") == "available"
        and node["app_version"] == APP_VERSION
        and job["operation"] in node["operations"]
        and required_capability(job) in node["capabilities"]
    )


class WorkerRegistry:
    def __init__(self, database):
        self.database = database

    def initialize_store(self):
        """Anchor the API's shared logical namespace before starting team nodes."""
        path = contained(self.database.settings.data_dir, "state/worker-store.json")
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, "worker-storage")
            expected = connection.execute(
                select(db.worker_storage.c.payload).where(db.worker_storage.c.id == "default")
            ).scalar_one_or_none()
            if expected:
                if not path.is_file() or file_hash(path) != expected["sha256"]:
                    raise DomainError(
                        "SHARED_STORAGE_MISSING", "Mount the registered AFDS shared store", 503
                    )
                return expected
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                with path.open("xb") as handle:
                    handle.write(
                        json.dumps({"namespace": str(uuid4()), "proof": str(uuid4())}).encode()
                    )
            if path.stat().st_size > 4096:
                raise DomainError("SHARED_STORAGE_INVALID", "Shared store marker is invalid", 503)
            payload = {"sha256": file_hash(path)}
            connection.execute(insert(db.worker_storage).values(id="default", payload=payload))
            return payload

    def probe_storage(self):
        try:
            with self.database.engine.connect() as connection:
                expected = connection.execute(
                    select(db.worker_storage.c.payload).where(db.worker_storage.c.id == "default")
                ).scalar_one_or_none()
            path = contained(self.database.settings.data_dir, "state/worker-store.json")
            if (
                not expected
                or not path.is_file()
                or path.stat().st_size > 4096
                or file_hash(path) != expected["sha256"]
            ):
                return {
                    "status": "unavailable",
                    "reason": "Shared store marker is missing or differs",
                }
            proof = contained(self.database.settings.data_dir, f"state/probes/{uuid4().hex}")
            proof.parent.mkdir(parents=True, exist_ok=True)
            content = uuid4().bytes
            try:
                with proof.open("xb") as handle:
                    handle.write(content)
                if proof.read_bytes() != content:
                    raise OSError("Shared store readback differs")
            finally:
                proof.unlink(missing_ok=True)
            return {"status": "available", "namespace": expected["sha256"]}
        except (OSError, DomainError):
            return {"status": "unavailable", "reason": "Shared store read/write probe failed"}

    def capabilities(self, operations):
        from app.skill_process import interpreter

        result = ["python"]
        try:
            self.database.settings.binary("ffmpeg")
            self.database.settings.binary("ffprobe")
            result.append("media")
        except FileNotFoundError:
            pass
        if "run_skill_script" in operations:
            for suffix, capability in {".js": "node", ".ps1": "powershell", ".sh": "bash"}.items():
                if interpreter("check" + suffix)["executable"]:
                    result.append(capability)
            from app.skill_sandbox import execution_policy

            try:
                policy = execution_policy(self.database.settings, 300)
                result.append(f"sandbox:{policy.image_id}:{policy.policy_version}")
            except DomainError:
                pass
        return result

    def register(self, node_info):
        info = NodeInfo.model_validate(node_info).model_dump()
        storage = self.probe_storage()
        capabilities = self.capabilities(info["operations"])
        now = time.time()
        payload = {
            **info,
            "generation": uuid4().hex,
            "capabilities": capabilities,
            "capabilities_checked_at": now,
            "storage": storage,
            "app_version": APP_VERSION,
            "runtime": {"python": platform.python_version(), "platform": platform.platform()},
            "last_seen_at": now,
            "draining": False,
        }
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, "worker-node:" + info["id"])
            previous = connection.execute(
                select(db.worker_nodes.c.payload).where(db.worker_nodes.c.id == info["id"])
            ).scalar_one_or_none()
            if previous:
                payload["draining"] = previous["draining"]
                connection.execute(
                    update(db.worker_nodes)
                    .where(db.worker_nodes.c.id == info["id"])
                    .values(payload=payload, last_seen_at=now)
                )
            else:
                connection.execute(
                    insert(db.worker_nodes).values(id=info["id"], payload=payload, last_seen_at=now)
                )
        return payload

    def refresh(self, node, *, runtime=False):
        capabilities = None
        if runtime and time.time() - node.get("capabilities_checked_at", 0) >= 5:
            capabilities = self.capabilities(node["operations"])
        storage = self.probe_storage()
        with self.database.transaction() as connection:
            current = self.lock(connection, node)
            if current is None:
                return None
            current.update(last_seen_at=time.time(), storage=storage)
            if capabilities is not None:
                current.update(capabilities=capabilities, capabilities_checked_at=time.time())
            connection.execute(
                update(db.worker_nodes)
                .where(db.worker_nodes.c.id == current["id"])
                .values(payload=current, last_seen_at=current["last_seen_at"])
            )
            return current

    def lock(self, connection, node):
        statement = select(db.worker_nodes.c.payload).where(db.worker_nodes.c.id == node["id"])
        if connection.dialect.name == "postgresql":
            statement = statement.with_for_update()
        current = connection.execute(statement).scalar_one_or_none()
        if current is None or current["generation"] != node["generation"]:
            return None
        return dict(current)

    def check_job(self, connection, job):
        if not job.get("worker_node_id"):
            return self.database.settings.mode == "local"
        current = self.lock(
            connection, {"id": job["worker_node_id"], "generation": job["worker_generation"]}
        )
        return bool(
            current
            and current["app_version"] == APP_VERSION
            and current["storage"].get("status") == "available"
            and current["last_seen_at"] >= time.time() - STALE_SECONDS
        )

    def drain(self, node_id, draining):
        with self.database.transaction() as connection:
            statement = select(db.worker_nodes.c.payload).where(db.worker_nodes.c.id == node_id)
            if connection.dialect.name == "postgresql":
                statement = statement.with_for_update()
            payload = connection.execute(statement).scalar_one_or_none()
            if payload is None:
                raise DomainError("NOT_FOUND", "Worker node not found", 404)
            payload = {**payload, "draining": bool(draining)}
            connection.execute(
                update(db.worker_nodes)
                .where(db.worker_nodes.c.id == node_id)
                .values(payload=payload)
            )
        return payload

    def list(self):
        with self.database.engine.connect() as connection:
            nodes = (
                connection.execute(
                    select(db.worker_nodes.c.payload).order_by(db.worker_nodes.c.id).limit(200)
                )
                .scalars()
                .all()
            )
            result = []
            for node in nodes:
                status = (
                    "offline"
                    if node["last_seen_at"] < time.time() - STALE_SECONDS
                    else "unavailable"
                    if node["storage"]["status"] != "available"
                    else "draining"
                    if node["draining"]
                    else "online"
                )
                tasks = [
                    dict(row)
                    for row in connection.execute(
                        select(db.jobs.c.id, db.jobs.c.project_id, db.jobs.c.state).where(
                            db.jobs.c.worker_node_id == node["id"],
                            db.jobs.c.state.in_(
                                ["claimed", "running", "verifying", "cancel_requested"]
                            ),
                            db.jobs.c.lease_expires_at >= time.time(),
                        )
                    ).mappings()
                ]
                result.append(
                    {
                        **node,
                        "status": status,
                        "current_tasks": tasks,
                        "last_seen_age_seconds": max(0, time.time() - node["last_seen_at"]),
                    }
                )
            return result


def main():
    import argparse

    from app.config import Settings

    parser = argparse.ArgumentParser(description="Initialize the API shared AFDS asset store")
    parser.add_argument("command", choices=["init-storage"])
    parser.parse_args()
    database = db.Database(Settings())
    try:
        database.migrate()
        namespace = WorkerRegistry(database).initialize_store()
        print("Initialized AFDS shared store " + namespace["sha256"])
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
