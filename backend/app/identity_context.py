from contextvars import ContextVar
from dataclasses import dataclass

LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


@dataclass(frozen=True)
class Principal:
    user_id: str
    session_id: str | None
    is_admin: bool
    mode: str
    username: str


current_principal: ContextVar[Principal | None] = ContextVar("afds_principal", default=None)
current_access: ContextVar[tuple[str, str] | None] = ContextVar("afds_project_access", default=None)


def actor_id(settings):
    actor = current_principal.get()
    return actor.user_id if actor else LOCAL_USER_ID if settings.mode == "local" else None
