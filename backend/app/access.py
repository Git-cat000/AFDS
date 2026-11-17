"""Explicit HTTP access policies with fresh membership checks."""

from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute
from sqlalchemy import select

from app import db
from app.errors import DomainError
from app.identity_context import LOCAL_USER_ID, Principal, current_access, current_principal
from app.identity_service import IdentityService

ROLE_ACTIONS = {
    "owner": {
        "read",
        "edit",
        "generate",
        "review",
        "run_script",
        "manage_members",
        "manage_budget",
    },
    "editor": {"read", "edit", "generate", "review", "run_script"},
    "reviewer": {"read", "review"},
    "viewer": {"read"},
}


def allowed(role, action):
    if action == "edit_or_review":
        return allowed(role, "edit") or allowed(role, "review")
    return action in ROLE_ACTIONS.get(role, set())


def require_membership(connection, project_id, principal, action):
    if principal.mode == "local":
        return "owner"
    row = connection.execute(
        select(db.project_members.c.role)
        .join(db.projects)
        .join(db.users, db.users.c.id == db.project_members.c.user_id)
        .where(
            db.project_members.c.project_id == str(project_id),
            db.project_members.c.user_id == principal.user_id,
            db.projects.c.archived.is_(False),
            db.users.c.active.is_(True),
        )
    ).scalar_one_or_none()
    if row is None:
        raise DomainError("NOT_FOUND", "Project not found", 404)
    if not allowed(row, action):
        raise DomainError("ACCESS_DENIED", "Your project role does not allow this action", 403)
    return row


def require_project_access(request, project_id, action, *, principal=None):
    database = request.app.state.database
    principal = principal or IdentityService(database).principal(request)
    with database.engine.connect() as connection:
        request.state.project_role = require_membership(connection, project_id, principal, action)
    return principal


def job_action(operation):
    if operation == "run_skill_script":
        return "run_script"
    if operation == "review_shot":
        return "review"
    if operation == "export_project":
        return "read"
    if operation in {
        "generate_video",
        "generate_image",
        "generate_speech",
        "plan_storyboard",
        "plan_semantic",
        "check_production",
        "transcribe_audio",
        "analyze_beats",
    }:
        return "generate"
    return "edit"


def check_job_access(connection, job, settings):
    if settings.mode == "local":
        return
    identifier = job.get("execution_actor_id") or job.get("actor_id")
    if not identifier or identifier == LOCAL_USER_ID:
        raise DomainError("ACCESS_REVOKED", "A current project member must approve this task", 403)
    actor = Principal(identifier, None, False, "team", "")
    try:
        require_membership(connection, job["project_id"], actor, job_action(job["operation"]))
    except DomainError:
        raise DomainError(
            "ACCESS_REVOKED", "The task's approving user no longer has execution access", 403
        ) from None


def policy_for(path, methods):
    path = path.removeprefix("/api/v1")
    writing = bool(set(methods) - {"GET", "HEAD", "OPTIONS"})
    if path in {"/health", "/version", "/auth/login", "/auth/session"}:
        return {"audience": "public", "action": "read"}
    if path == "/auth/logout":
        return {"audience": "authenticated", "action": "edit"}
    if path.startswith(("/settings/", "/system/", "/admin/")):
        return {"audience": "admin", "action": "edit" if writing else "read"}
    if path == "/models" or path in {"/projects", "/projects/import", "/projects/restore"}:
        return {"audience": "authenticated", "action": "edit" if writing else "read"}
    if path == "/events":
        return {"audience": "project-query", "action": "read"}
    if path.startswith("/skills"):
        return {"audience": "library", "action": "edit" if writing else "read"}
    if path.startswith("/projects/{project_id}"):
        action = "edit" if writing else "read"
        if writing and "/jobs/{job_id}/" in path:
            action = "job_control"
        elif writing and path.endswith("/exports"):
            action = "read"
        elif writing and ("/members/" in path or path.endswith("/members")):
            action = "manage_members"
        elif writing and "/budget" in path:
            action = "manage_budget"
        elif writing and "/skills/scripts" in path:
            action = "run_script"
        elif writing and (path.endswith("/reviews") or "/feedback" in path):
            action = "review"
        elif writing and "/changesets/" in path:
            action = "edit_or_review"
        elif writing and any(
            part in path
            for part in ("/generate", "/planning/", "/director", "/media", "/skills/checks")
        ):
            action = "generate"
        return {"audience": "project", "action": action}
    raise RuntimeError(f"Register an access policy for {path}")


def authorize(request, policy):
    database = request.app.state.database
    service = IdentityService(database)
    audience = policy["audience"]
    if audience == "public":
        return None
    if audience == "library" and database.settings.mode == "team":
        from app.skill_packages import builtins

        package_id = request.path_params.get("package_id")
        if request.method in {"GET", "HEAD"} and (package_id is None or package_id in builtins()):
            return None
        raise DomainError("PROJECT_REQUIRED", "Use the project's private skill library", 403)
    principal = service.principal(request)
    if audience == "admin" and not principal.is_admin:
        raise DomainError("ADMIN_REQUIRED", "Administrator access is required", 403)
    if principal.mode == "team" and request.method not in {"GET", "HEAD", "OPTIONS"}:
        service.csrf(request)
    if audience in {"project", "project-query"}:
        project_id = request.path_params.get("project_id") or request.query_params.get("project_id")
        if not project_id:
            if principal.mode == "team":
                raise DomainError("PROJECT_REQUIRED", "Choose a project", 422)
        else:
            action = "read" if policy["action"] == "job_control" else policy["action"]
            require_project_access(request, project_id, action, principal=principal)
            if policy["action"] == "job_control":
                with database.engine.connect() as connection:
                    job = connection.execute(
                        select(db.jobs.c.payload).where(
                            db.jobs.c.id == request.path_params["job_id"],
                            db.jobs.c.project_id == str(project_id),
                        )
                    ).scalar_one_or_none()
                if job is None:
                    raise DomainError("NOT_FOUND", "Task not found in this project", 404)
                action = job_action(job["operation"])
                role = request.state.project_role
                if not allowed(role, action) or (
                    role not in {"owner", "editor"}
                    and principal.user_id
                    not in {job.get("actor_id"), job.get("execution_actor_id")}
                ):
                    raise DomainError("ACCESS_DENIED", "Your role cannot control this task", 403)
                request.state.effective_project_action = action
    request.state.principal = principal
    return principal


class AccessRoute(APIRoute):
    def get_route_handler(self):
        policy = policy_for(self.path, self.methods or {"GET"})
        self.openapi_extra = {**(self.openapi_extra or {}), "x-afds-access": policy}
        handler = super().get_route_handler()

        async def secured(request):
            principal = await run_in_threadpool(authorize, request, policy)
            token = current_principal.set(principal)
            scope = None
            if principal and policy["audience"] in {"project", "project-query"}:
                project_id = request.path_params.get("project_id") or request.query_params.get(
                    "project_id"
                )
                action = policy["action"]
                if action == "job_control":
                    action = request.state.effective_project_action
                if action == "edit_or_review":
                    action = "review" if request.state.project_role == "reviewer" else "edit"
                if project_id:
                    scope = (str(project_id), action)
            access_token = current_access.set(scope)
            try:
                return await handler(request)
            finally:
                current_access.reset(access_token)
                current_principal.reset(token)

        return secured
