"""Hashed sessions and project membership for the application identity boundary."""

import hashlib
import hmac
import re
import secrets
import threading
import time
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, insert, select, update

from app import db
from app.errors import DomainError
from app.identity_context import LOCAL_USER_ID, Principal
from app.project_engine import emit
from app.schemas import new_id

HASH_SLOTS = threading.BoundedSemaphore(2)


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache(maxsize=1)
def dummy_password_hash():
    return PasswordHasher().hash(secrets.token_urlsafe(32))


def public_user(user):
    return {key: user[key] for key in ("id", "username", "display_name", "is_admin", "active")}


class IdentityService:
    def __init__(self, database):
        self.database = database

    def create_user(self, username, password, *, is_admin=False, display_name=None):
        username = username.strip().lower()
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username)
            or not 12 <= len(password) <= 1024
        ):
            raise DomainError(
                "INVALID_ACCOUNT", "Use a valid username and a12–1024character password", 422
            )
        if display_name is not None and not 1 <= len(display_name.strip()) <= 100:
            raise DomainError("INVALID_ACCOUNT", "Use a display name within100characters", 422)
        with HASH_SLOTS:
            hashed = PasswordHasher().hash(password)
        user = {
            "id": new_id(),
            "username": username,
            "display_name": display_name or username,
            "password_hash": hashed,
            "is_admin": is_admin,
            "active": True,
            "created_at": time.time(),
        }
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, f"username:{username}")
            if connection.execute(
                select(db.users.c.id).where(db.users.c.username == username)
            ).first():
                raise DomainError("USERNAME_EXISTS", "This username already exists", 409)
            connection.execute(insert(db.users).values(**user))
        return public_user(user)

    def attempt(self, username, peer):
        key = token_hash(f"{peer}:{username}")
        now = time.time()
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, f"login-limit:{key}")
            row = (
                connection.execute(select(db.login_limits).where(db.login_limits.c.key == key))
                .mappings()
                .first()
            )
            attempts = row["attempts"] if row and now - row["window_at"] < 60 else 0
            if attempts >= 8:
                raise DomainError("LOGIN_RATE_LIMIT", "Wait before trying to sign in again", 429)
            values = {"window_at": row["window_at"] if attempts else now, "attempts": attempts + 1}
            if row:
                connection.execute(
                    update(db.login_limits).where(db.login_limits.c.key == key).values(**values)
                )
            else:
                connection.execute(insert(db.login_limits).values(key=key, **values))
            connection.execute(
                delete(db.login_limits).where(db.login_limits.c.window_at < now - 3600)
            )
        return key

    def login(self, username, password, peer, previous_token=None):
        username = username.lower()
        limit_key = self.attempt(username, peer)
        with self.database.engine.connect() as connection:
            user = (
                connection.execute(select(db.users).where(db.users.c.username == username))
                .mappings()
                .first()
            )
        with HASH_SLOTS:
            hasher = PasswordHasher()
            hashed = (
                user["password_hash"] if user and user["password_hash"] else dummy_password_hash()
            )
            try:
                hasher.verify(hashed, password)
            except (VerificationError, InvalidHashError):
                raise DomainError(
                    "INVALID_CREDENTIALS", "Username or password is incorrect", 401
                ) from None
            if not user or not user["active"] or user["id"] == LOCAL_USER_ID:
                raise DomainError("INVALID_CREDENTIALS", "Username or password is incorrect", 401)
            replacement = hasher.hash(password) if hasher.check_needs_rehash(hashed) else None
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, f"user:{user['id']}")
            latest = (
                connection.execute(select(db.users).where(db.users.c.id == user["id"]))
                .mappings()
                .one()
            )
            if not latest["active"] or latest["password_hash"] != hashed:
                raise DomainError("INVALID_CREDENTIALS", "Username or password is incorrect", 401)
            if replacement:
                connection.execute(
                    update(db.users)
                    .where(db.users.c.id == user["id"])
                    .values(password_hash=replacement)
                )
            if previous_token:
                connection.execute(
                    delete(db.sessions).where(
                        db.sessions.c.token_hash == token_hash(previous_token)
                    )
                )
            connection.execute(delete(db.sessions).where(db.sessions.c.expires_at < now))
            connection.execute(delete(db.login_limits).where(db.login_limits.c.key == limit_key))
            connection.execute(
                insert(db.sessions).values(
                    id=new_id(),
                    token_hash=token_hash(token),
                    user_id=user["id"],
                    csrf_hash=token_hash(csrf),
                    created_at=now,
                    expires_at=now + self.database.settings.session_hours * 3600,
                )
            )
        return public_user(user), token, csrf

    def session(self, token):
        if not token or len(token) > 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return None
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        db.sessions,
                        db.users.c.username,
                        db.users.c.display_name,
                        db.users.c.is_admin,
                    )
                    .join(db.users)
                    .where(
                        db.sessions.c.token_hash == token_hash(token),
                        db.sessions.c.expires_at > time.time(),
                        db.users.c.active.is_(True),
                        db.users.c.id != LOCAL_USER_ID,
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def principal(self, request):
        if self.database.settings.mode == "local":
            return Principal(LOCAL_USER_ID, None, True, "local", "_local")
        session = self.session(request.cookies.get("afds_session"))
        if session is None:
            raise DomainError("AUTH_REQUIRED", "Sign in to continue", 401)
        request.state.identity_session = session
        return Principal(
            session["user_id"], session["id"], session["is_admin"], "team", session["username"]
        )

    def csrf(self, request):
        session = request.state.identity_session
        value = request.headers.get("X-CSRF-Token", "")
        if len(value) > 128 or not hmac.compare_digest(token_hash(value), session["csrf_hash"]):
            raise DomainError("CSRF_INVALID", "Refresh the session before submitting changes", 403)

    def logout(self, token):
        if token:
            with self.database.transaction() as connection:
                connection.execute(
                    delete(db.sessions).where(db.sessions.c.token_hash == token_hash(token))
                )

    def members(self, project_id):
        with self.database.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(
                        db.users.c.id,
                        db.users.c.username,
                        db.users.c.display_name,
                        db.project_members.c.role,
                    )
                    .join(db.project_members)
                    .where(db.project_members.c.project_id == str(project_id))
                    .order_by(db.users.c.username)
                ).mappings()
            ]

    def change_member(self, project_id, user_id, role, principal):
        from app.access import require_membership

        with self.database.transaction() as connection:
            db.lock_project(connection, project_id)
            require_membership(connection, project_id, principal, "manage_members")
            user = (
                connection.execute(
                    select(db.users).where(
                        db.users.c.id == str(user_id), db.users.c.active.is_(True)
                    )
                )
                .mappings()
                .first()
            )
            if user is None or user["id"] == LOCAL_USER_ID:
                raise DomainError("USER_NOT_FOUND", "Choose an active team user", 404)
            prior = connection.execute(
                select(db.project_members.c.role).where(
                    db.project_members.c.project_id == str(project_id),
                    db.project_members.c.user_id == str(user_id),
                )
            ).scalar_one_or_none()
            if prior == "owner" and role != "owner":
                count = connection.execute(
                    select(func.count())
                    .select_from(db.project_members)
                    .where(
                        db.project_members.c.project_id == str(project_id),
                        db.project_members.c.role == "owner",
                    )
                ).scalar_one()
                if count <= 1:
                    raise DomainError("LAST_OWNER", "Keep at least one project owner", 409)
            condition = (db.project_members.c.project_id == str(project_id)) & (
                db.project_members.c.user_id == str(user_id)
            )
            if role is None:
                connection.execute(delete(db.project_members).where(condition))
            elif prior:
                connection.execute(update(db.project_members).where(condition).values(role=role))
            else:
                connection.execute(
                    insert(db.project_members).values(
                        project_id=str(project_id),
                        user_id=str(user_id),
                        role=role,
                        created_at=time.time(),
                    )
                )
            emit(
                connection,
                str(project_id),
                "member.changed",
                {"actor_id": principal.user_id, "user_id": str(user_id), "role": role},
            )
        return self.members(project_id)

    def claim_local_projects(self, username):
        with self.database.transaction() as connection:
            user_id = connection.execute(
                select(db.users.c.id).where(
                    db.users.c.username == username.lower(), db.users.c.active.is_(True)
                )
            ).scalar_one_or_none()
            if user_id is None or user_id == LOCAL_USER_ID:
                raise DomainError("USER_NOT_FOUND", "Choose an active team user", 404)
            identifiers = (
                connection.execute(select(db.projects.c.id).order_by(db.projects.c.id))
                .scalars()
                .all()
            )
            claimed = []
            for project_id in identifiers:
                db.lock_project(connection, project_id)
                owners = (
                    connection.execute(
                        select(db.project_members).where(
                            db.project_members.c.project_id == project_id
                        )
                    )
                    .mappings()
                    .all()
                )
                if owners and any(row["user_id"] != LOCAL_USER_ID for row in owners):
                    continue
                connection.execute(
                    delete(db.project_members).where(db.project_members.c.project_id == project_id)
                )
                connection.execute(
                    insert(db.project_members).values(
                        project_id=project_id, user_id=user_id, role="owner", created_at=time.time()
                    )
                )
                emit(connection, project_id, "project.owner_claimed", {"user_id": user_id})
                claimed.append(project_id)
        return claimed
