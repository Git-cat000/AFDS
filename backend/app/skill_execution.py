"""Source-pinned skill approvals, registered checks and durable local script runs."""

import copy
import hashlib
import json
import os
import stat

from sqlalchemy import func, insert, select, update

from app import db
from app.errors import DomainError
from app.execution.queue import Queue, digest, public_job
from app.history_service import owned_header
from app.project_engine import ProjectEngine, emit, find
from app.schemas import new_id
from app.skill_checks import check_production
from app.skill_packages import SECRET, SkillLibrary, safe_name
from app.skill_process import interpreter, run_script
from app.skill_schema import add_guidance
from app.storage import contained, file_hash

OPERATIONS = {"check_production", "run_skill_script"}


def selected_shots(apr, identifiers):
    for identifier in identifiers:
        if find(apr["shots"], identifier)["status"] == "archived":
            raise DomainError("SHOT_ARCHIVED", "Choose active shots for skill work", 409)
    selected = set(map(str, identifiers))
    return [
        value
        for value in apr["shots"]
        if value["status"] != "archived" and (not selected or value["id"] in selected)
    ]


def script_context(apr, stage, identifiers):
    shots = selected_shots(apr, identifiers)
    chosen = shots[:100]
    scene_ids = {value["scene_id"] for value in chosen}
    project = {
        key: copy.deepcopy(apr[key])
        for key in ("project_id", "revision", "title", "brief", "format", "style_bible")
    }
    project.update(
        shots=chosen,
        scenes=[value for value in apr["scenes"] if value["id"] in scene_ids],
        characters=apr.get("characters", [])[:40],
        locations=apr.get("locations", [])[:40],
        world_states=[
            value for value in apr.get("world_states", []) if value.get("scene_id") in scene_ids
        ][:100],
    )
    timeline = copy.deepcopy(apr["timeline"])
    timeline_truncated = len(timeline["tracks"]) > 20
    timeline["tracks"] = timeline["tracks"][:20]
    for track in timeline["tracks"]:
        timeline_truncated |= len(track["items"]) > 100
        track["items"] = track["items"][:100]
    for field in ("captions", "markers"):
        timeline_truncated |= len(timeline.get(field, [])) > 100
        timeline[field] = timeline.get(field, [])[:100]
    versions = {value["id"]: value for value in apr["asset_versions"]}
    active_versions = [
        versions[shot["active_asset_version_id"]]
        for shot in chosen
        if shot["active_asset_version_id"] in versions
    ]
    project.update(
        timeline=timeline,
        asset_versions=active_versions,
        assets=[
            {
                key: value[key]
                for key in (
                    "id",
                    "sha256",
                    "media_type",
                    "duration_frames",
                    "duration_seconds",
                    "sample_rate",
                    "channels",
                )
                if key in value
            }
            for value in apr["assets"][:200]
        ],
    )
    context = add_guidance(
        {
            "stage": stage,
            "project": project,
            "scope": {
                "requested_shot_ids": list(map(str, identifiers)),
                "total_active_shots": len(shots),
                "truncated": len(chosen) < len(shots),
                "timeline_truncated": timeline_truncated,
                "assets_truncated": len(apr["assets"]) > 200,
            },
        },
        apr,
        [stage],
    )
    if len(json.dumps(context, ensure_ascii=False).encode()) > 512 * 1024:
        raise DomainError(
            "SKILL_CONTEXT_LIMIT", "Select a smaller shot scope or shorter guidance", 422
        )
    return context


class SkillExecution:
    def __init__(self, database):
        self.database = database
        self.engine = ProjectEngine(database)
        self.queue = Queue(database)
        self.library = SkillLibrary(database)

    def preview(self, project_id, body):
        if any(SECRET.search(value.encode()) for value in body.arguments):
            raise DomainError(
                "SCRIPT_ARGUMENT_CREDENTIAL",
                "Use credential references instead of secret values in saved arguments",
                422,
            )
        package = self.library.get(body.package_id)
        script = next(
            (value for value in package["scripts"] if value["path"] == body.script_path), None
        )
        if script is None:
            raise DomainError(
                "SCRIPT_NOT_FOUND", "Choose a script listed in this skill package", 404
            )
        if script["size"] > 256 * 1024:
            raise DomainError("SCRIPT_SOURCE_LIMIT", "Review scripts up to256KiB", 422)
        if any(
            name.split("/", 1)[0].casefold() in {"input.json", "outputs"}
            for name in package["files"]
        ):
            raise DomainError(
                "SCRIPT_WORKSPACE_CONFLICT",
                "Reserve input.json and outputs/ for the script run",
                422,
            )
        try:
            source = self.library.resource(package["id"], body.script_path).decode("utf-8-sig")
        except UnicodeError as error:
            raise DomainError(
                "SCRIPT_ENCODING", "Use UTF-8 script source for review", 422
            ) from error
        runtime = interpreter(body.script_path)
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            request = {
                "package_id": package["id"],
                "skill_name": package["name"],
                "script_path": body.script_path,
                "script_sha256": script["sha256"],
                "arguments": body.arguments,
                "timeout_seconds": body.timeout_seconds,
                "stage": body.stage,
                "context": script_context(apr, body.stage, body.shot_ids),
                "provider_id": "local-skill-script",
                "model_id": runtime["name"],
                "adapter_version": "0.7.0",
            }
            plan = {
                "id": new_id(),
                "type": "skill_script",
                "state": "proposed",
                "base_revision": apr["revision"],
                "fingerprint": digest(request),
                "request": request,
                "source_code": source,
                "runtime": {"name": runtime["name"], "available": bool(runtime["executable"])},
                "resources": package["resources"],
            }
            connection.execute(
                insert(db.plans).values(id=plan["id"], project_id=str(project_id), payload=plan)
            )
            return plan

    def approve(self, project_id, plan_id, body):
        if not body.approved:
            raise DomainError(
                "APPROVAL_REQUIRED", "Review and approve this exact local script run", 409
            )
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            plan = connection.execute(
                select(db.plans.c.payload).where(
                    db.plans.c.id == str(plan_id), db.plans.c.project_id == str(project_id)
                )
            ).scalar_one_or_none()
            if plan is None or plan.get("type") != "skill_script":
                raise DomainError("NOT_FOUND", "Script preview not found in this project", 404)
            if (
                body.fingerprint != plan["fingerprint"]
                or digest(plan["request"]) != plan["fingerprint"]
            ):
                raise DomainError(
                    "SCRIPT_PREVIEW_CHANGED", "Review the current source and parameters again", 409
                )
            if body.base_revision != plan["base_revision"]:
                raise DomainError(
                    "REVISION_CONFLICT", "Script preview is tied to its source revision", 409
                )
            if plan.get("job_id"):
                return public_job(self.queue.get(connection, project_id, plan["job_id"]))
            self.engine.check_revision(apr, body.base_revision)
            self.library.get(plan["request"]["package_id"])
            if not interpreter(plan["request"]["script_path"])["executable"]:
                raise DomainError(
                    "SCRIPT_INTERPRETER_MISSING",
                    "Install the declared interpreter before approving this run",
                    422,
                )
            job = self.queue.enqueue(
                connection,
                apr,
                "run_skill_script",
                plan["request"],
                idempotency_key=f"{project_id}:skill-script:{plan_id}",
            )
            plan.update(state="executed", job_id=job["id"])
            connection.execute(
                update(db.plans).where(db.plans.c.id == str(plan_id)).values(payload=plan)
            )
            self.engine.approve(
                connection,
                project_id,
                "skill_script",
                {
                    "plan_id": str(plan_id),
                    "job_id": job["id"],
                    "fingerprint": plan["fingerprint"],
                    "package_id": plan["request"]["package_id"],
                    "script_sha256": plan["request"]["script_sha256"],
                },
            )
            return public_job(job)

    def enqueue_check(self, project_id, body):
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            self.engine.check_revision(apr, body.base_revision)
            selected_shots(apr, body.shot_ids)
            request = {
                "stage": body.stage,
                "shot_ids": list(map(str, body.shot_ids)),
                "base_revision": body.base_revision,
                "provider_id": "local-production-check",
                "model_id": "afds-production-checks",
                "adapter_version": "0.7.0",
            }
            return public_job(self.queue.enqueue(connection, apr, "check_production", request))

    def execute(self, job, owner, work, checkpoint):
        request = job["request"]
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            live = self.engine.load(connection, job["project_id"])
            self.engine.check_revision(live, job["source_revision"])
            if job["operation"] == "check_production":
                apr = live
                self.engine.check_revision(apr, request["base_revision"])
            elif current.get("script_started"):
                raise DomainError(
                    "SCRIPT_RUN_UNCERTAIN",
                    "A previous launch may have run. Review and approve a new run",
                    409,
                )
        if job["operation"] == "check_production":
            return check_production(apr, request["stage"], request["shot_ids"])
        package = self.library.get(request["package_id"])
        for resource in package["resources"]:
            data = self.library.resource(package["id"], resource["path"])
            if hashlib.sha256(data).hexdigest() != resource["sha256"]:
                raise DomainError("HASH_MISMATCH", "Skill package content changed", 409)
            destination = contained(work, resource["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        source = contained(work, request["script_path"])
        if file_hash(source) != request["script_sha256"]:
            raise DomainError("HASH_MISMATCH", "Approved script source changed", 409)
        context = json.dumps(request["context"], ensure_ascii=False).encode()
        (work / "input.json").write_bytes(context)
        (work / "outputs").mkdir(exist_ok=True)
        runtime = interpreter(request["script_path"])
        if not runtime["executable"]:
            raise DomainError(
                "SCRIPT_INTERPRETER_MISSING", "The approved interpreter is unavailable", 422
            )
        if not checkpoint():
            from app.media import Cancelled

            raise Cancelled()
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            payload = connection.execute(
                select(db.jobs.c.payload).where(db.jobs.c.id == job["id"])
            ).scalar_one()
            if current.get("script_started"):
                raise DomainError("SCRIPT_RUN_UNCERTAIN", "Review and approve a new run", 409)
            payload["script_started"] = True
            self.engine.check_revision(
                self.engine.load(connection, job["project_id"]), job["source_revision"]
            )
            connection.execute(
                update(db.jobs).where(db.jobs.c.id == job["id"]).values(payload=payload)
            )
        source_path = source.as_posix() if runtime["name"] == "Bash" else str(source)
        arguments = [runtime["executable"], *runtime["options"], source_path, *request["arguments"]]
        result = run_script(arguments, work, context, request["timeout_seconds"], checkpoint)
        result.update(
            kind="script",
            stage=request["stage"],
            source_revision=job["source_revision"],
            artifacts=[],
        )
        try:
            result["artifacts"] = self.collect(job, work)
        except DomainError as error:
            result["error"] = {"code": error.code, "message": error.message}
        return result

    def collect(self, job, work):
        raw_root = work / "outputs"
        if not raw_root.exists() and not raw_root.is_symlink():
            return []
        if raw_root.is_symlink() or getattr(raw_root.lstat(), "st_file_attributes", 0) & 0x400:
            raise DomainError("SCRIPT_OUTPUT_INVALID", "Use a regular outputs directory", 422)
        root = contained(work, "outputs")
        result = []
        total = entries = 0
        for directory, directories, files in os.walk(root, followlinks=False):
            for name in [*directories, *files]:
                entries += 1
                path = contained(root, os.path.relpath(os.path.join(directory, name), root))
                info = (root / os.path.relpath(os.path.join(directory, name), root)).lstat()
                if (
                    entries > 256
                    or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    raise DomainError(
                        "SCRIPT_OUTPUT_INVALID",
                        "Output paths must be regular owned files within256entries",
                        422,
                    )
            for name in files:
                path = contained(root, os.path.relpath(os.path.join(directory, name), root))
                relative = safe_name(path.relative_to(root).as_posix())
                if len(result) >= 32 or not path.is_file() or path.stat().st_size > 8 * 1024**2:
                    raise DomainError(
                        "SCRIPT_OUTPUT_LIMIT", "Keep up to32output files of8MiB each", 422
                    )
                with path.open("rb") as handle:
                    content = handle.read(8 * 1024**2 + 1)
                if SECRET.search(content):
                    raise DomainError(
                        "SCRIPT_OUTPUT_CREDENTIAL",
                        "Remove private credentials from script output files",
                        422,
                    )
                total += len(content)
                if len(content) > 8 * 1024**2 or total > 32 * 1024**2:
                    raise DomainError("SCRIPT_OUTPUT_LIMIT", "Output artifacts exceed32MiB", 422)
                sha = hashlib.sha256(content).hexdigest()
                stored = f"skill-results/{job['project_id']}/{job['id']}/{sha}"
                destination = contained(self.database.settings.data_dir, stored)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with destination.open("xb") as handle:
                        handle.write(content)
                except FileExistsError:
                    if file_hash(destination) != sha:
                        raise DomainError(
                            "HASH_MISMATCH", "Stored script output integrity check failed", 409
                        )
                result.append(
                    {
                        "id": digest({"name": relative, "sha256": sha})[:32],
                        "name": relative,
                        "sha256": sha,
                        "size": len(content),
                        "relative_path": stored,
                    }
                )
        return result

    def publish(self, job, owner, result, elapsed_ms):
        with self.database.transaction() as connection:
            current = self.queue.check_owned(connection, job, owner)
            revision = owned_header(connection, job["project_id"])["revision"]
            result["stale"] = revision != job["source_revision"]
            self.queue.set_state(connection, current, "verifying", progress=90)
            problem = result.get("error")
            self.queue.set_state(
                connection,
                current,
                "failed" if problem else "completed",
                progress=100,
                skill_result=result,
                elapsed_ms=elapsed_ms,
                error=problem,
            )
            emit(
                connection,
                job["project_id"],
                "skill.completed",
                {"stage": job["request"]["stage"], "success": problem is None},
                job["id"],
            )

    def list(self, project_id, stage=None, offset=0, limit=20):
        with self.database.engine.connect() as connection:
            header = owned_header(connection, project_id)
            conditions = [
                db.jobs.c.project_id == str(project_id),
                db.jobs.c.payload["operation"].as_string().in_(OPERATIONS),
            ]
            if stage:
                conditions.append(db.jobs.c.payload["request"]["stage"].as_string() == stage)
            total = connection.execute(
                select(func.count()).select_from(db.jobs).where(*conditions)
            ).scalar_one()
            rows = connection.execute(
                select(db.jobs)
                .where(*conditions)
                .order_by(db.jobs.c.payload["created_at"].as_float().desc(), db.jobs.c.id)
                .offset(offset)
                .limit(limit)
            ).mappings()
            from app.execution.queue import serialize

            items = []
            for row in rows:
                job = public_job(serialize(row))
                job["request"] = {
                    key: value
                    for key, value in job["request"].items()
                    if key in {"stage", "skill_name", "script_path", "package_id"}
                }
                if job.get("skill_result"):
                    job["skill_result"]["stale"] = header["revision"] != job["source_revision"]
                items.append(job)
            return {"items": items, "total": total, "offset": offset}

    def artifact(self, project_id, job_id, artifact_id):
        with self.database.engine.connect() as connection:
            owned_header(connection, project_id)
            job = self.queue.get(connection, project_id, job_id)
            artifact = next(
                (
                    value
                    for value in job.get("skill_result", {}).get("artifacts", [])
                    if value["id"] == artifact_id
                ),
                None,
            )
            if artifact is None:
                raise DomainError("NOT_FOUND", "Script output not found in this project", 404)
            path = contained(self.database.settings.data_dir, artifact["relative_path"])
            if not path.is_file() or file_hash(path) != artifact["sha256"]:
                raise DomainError("HASH_MISMATCH", "Script output is missing or changed", 409)
            return path, artifact
