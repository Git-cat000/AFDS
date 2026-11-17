import hmac
import secrets
import time
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from app import db
from app.access import ROLE_ACTIONS, AccessRoute
from app.errors import DomainError
from app.identity_schema import LoginRequest, MemberInvite, MemberUpdate
from app.identity_service import IdentityService, public_user, token_hash

router = APIRouter(route_class=AccessRoute)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    service = IdentityService(request.app.state.database)
    settings = service.database.settings
    if settings.mode != "team":
        raise DomainError("LOCAL_SESSION", "The local edition uses the local-user session", 409)
    user, token, csrf = service.login(
        body.username,
        body.password.get_secret_value(),
        request.client.host if request.client else "unknown",
        request.cookies.get("afds_session"),
    )
    response.set_cookie(
        "afds_session",
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )
    response.set_cookie(
        "afds_csrf",
        csrf,
        max_age=settings.session_hours * 3600,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
    )
    return {"mode": "team", "authenticated": True, "user": user, "csrf_token": csrf}


@router.get("/auth/session")
def session(request: Request, response: Response):
    service = IdentityService(request.app.state.database)
    try:
        principal = service.principal(request)
    except DomainError:
        return {"mode": "team", "authenticated": False, "user": None, "csrf_token": None}
    with service.database.engine.connect() as connection:
        user = (
            connection.execute(select(db.users).where(db.users.c.id == principal.user_id))
            .mappings()
            .one()
        )
    csrf = None
    if principal.mode == "team":
        stored = request.state.identity_session
        csrf = request.cookies.get("afds_csrf", "")
        if len(csrf) > 128 or not hmac.compare_digest(token_hash(csrf), stored["csrf_hash"]):
            csrf = secrets.token_urlsafe(32)
            with service.database.transaction() as connection:
                changed = connection.execute(
                    update(db.sessions)
                    .where(
                        db.sessions.c.id == principal.session_id,
                        db.sessions.c.expires_at > time.time(),
                    )
                    .values(csrf_hash=token_hash(csrf))
                )
                if changed.rowcount != 1:
                    raise DomainError("AUTH_REQUIRED", "Sign in to continue", 401)
            response.set_cookie(
                "afds_csrf",
                csrf,
                max_age=max(0, int(stored["expires_at"] - time.time())),
                httponly=False,
                secure=service.database.settings.secure_cookies,
                samesite="lax",
            )
    return {
        "mode": principal.mode,
        "authenticated": True,
        "user": public_user(user),
        "csrf_token": csrf,
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    IdentityService(request.app.state.database).logout(request.cookies.get("afds_session"))
    response.delete_cookie("afds_session")
    response.delete_cookie("afds_csrf")
    return {"authenticated": False}


@router.get("/projects/{project_id}/members")
def members(project_id: UUID, request: Request):
    return IdentityService(request.app.state.database).members(project_id)


@router.get("/projects/{project_id}/access")
def access(project_id: UUID, request: Request):
    role = request.state.project_role
    return {"role": role, "actions": sorted(ROLE_ACTIONS[role])}


@router.post("/projects/{project_id}/members")
def invite_member(project_id: UUID, body: MemberInvite, request: Request):
    database = request.app.state.database
    with database.engine.connect() as connection:
        user_id = connection.execute(
            select(db.users.c.id).where(
                db.users.c.username == body.username.lower(), db.users.c.active.is_(True)
            )
        ).scalar_one_or_none()
    if user_id is None:
        raise DomainError("USER_NOT_FOUND", "Choose a registered team username", 404)
    return IdentityService(database).change_member(
        project_id, user_id, body.role, request.state.principal
    )


@router.put("/projects/{project_id}/members/{user_id}")
def update_member(project_id: UUID, user_id: UUID, body: MemberUpdate, request: Request):
    return IdentityService(request.app.state.database).change_member(
        project_id, user_id, body.role, request.state.principal
    )


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_member(project_id: UUID, user_id: UUID, request: Request):
    return IdentityService(request.app.state.database).change_member(
        project_id, user_id, None, request.state.principal
    )
