from typing import Annotated, Literal

from pydantic import ConfigDict, Field, SecretStr, StringConstraints

from app.schemas import Schema

Role = Literal["owner", "editor", "reviewer", "viewer"]


class LoginRequest(Schema):
    model_config = ConfigDict(str_strip_whitespace=False)
    username: Annotated[str, StringConstraints(strip_whitespace=True)] = Field(
        min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$"
    )
    password: SecretStr = Field(min_length=1, max_length=1024)


class MemberUpdate(Schema):
    role: Role


class MemberInvite(MemberUpdate):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
