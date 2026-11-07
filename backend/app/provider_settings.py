"""Local provider profiles. Secret values never enter project state or API responses."""

import ipaddress
import os
from typing import Literal
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, SecretStr, model_validator
from sqlalchemy import insert, select, update

from app import db
from app.errors import DomainError
from app.schemas import Schema

ProviderKind = Literal["llm", "video", "image", "speech"]
KINDS = ("llm", "video", "image", "speech")
DEFAULTS = {
    "llm": "mock-director",
    "video": "mock-video-local",
    "image": "openai-image",
    "speech": "openai-speech",
}
CAPABILITIES = {
    "llm": "storyboard",
    "video": "text_to_video",
    "image": "text_to_image",
    "speech": "text_to_speech",
}
NAMES = {"llm": "LLM", "video": "视频", "image": "图像", "speech": "语音"}


def validate_url(value, allow_private=False):
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid port") from error
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(p in {".", ".."} for p in unquote(parsed.path).split("/"))
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Use a base URL without credentials, query or parent paths")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    if parsed.scheme != "https" and not allow_private:
        raise ValueError("HTTP/local addresses require private-network opt-in")
    host = parsed.hostname.lower()
    private = host == "localhost" or host.endswith(".localhost")
    try:
        address = ipaddress.ip_address(host)
        private = not address.is_global
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("Link-local, multicast and unspecified targets are blocked")
    except ValueError as error:
        if host.replace(".", "").isdigit() or ":" in host:
            raise ValueError("Invalid or prohibited IP address") from error
    if private and not allow_private:
        raise ValueError("Local addresses require private-network opt-in")
    return value.rstrip("/")


class ProviderConfig(Schema):
    revision: int = Field(default=0, ge=0)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    enabled: bool = False
    allow_private_network: bool = False
    timeout_seconds: int = Field(default=60, ge=5, le=180)
    json_mode: bool = True
    video_seconds: int = Field(default=8, ge=1, le=30)
    video_size: str = Field(default="1280x720", pattern=r"^\d{2,4}x\d{2,4}$")
    video_poll_seconds: int = Field(default=3, ge=1, le=30)
    video_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    image_size: str = Field(default="1024x1024", pattern=r"^(auto|\d{2,4}x\d{2,4})$")
    speech_voice: str = Field(default="alloy", min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_config(self):
        if self.base_url:
            self.base_url = validate_url(self.base_url, self.allow_private_network)
        if self.enabled and (not self.base_url or not self.model):
            raise ValueError("Enabled connections need a base URL and model")
        return self


class ProviderUpdate(ProviderConfig):
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class DefaultUpdate(Schema):
    llm: str | None = None
    video: str | None = None
    image: str | None = None
    speech: str | None = None


class ProviderStore:
    def __init__(self, database):
        self.database = database

    def cipher(self):
        directory = self.database.settings.data_dir / "secrets"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "provider.key"
        # Called while holding the SQLite write lock when creating the first key.
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(Fernet.generate_key())
        try:
            return Fernet(path.read_bytes())
        except (ValueError, OSError) as error:
            raise DomainError(
                "SECRET_STORE_UNAVAILABLE", "Cannot read local provider key", 500
            ) from error

    def default_config(self, kind):
        url = "" if kind == "video" else "https://api.openai.com/v1"
        return ProviderConfig(base_url=url).model_dump()

    def public(self, kind, row=None):
        config = row["config"] if row else self.default_config(kind)
        return {
            **config,
            "kind": kind,
            "provider_id": f"openai-{kind}",
            "has_api_key": bool(row and row["secret"]),
            "protocol": "openai-compatible",
            "display_name": f"OpenAI 兼容 {NAMES[kind]}",
        }

    def list(self):
        with self.database.engine.connect() as connection:
            rows = {r["id"]: r for r in connection.execute(select(db.provider_configs)).mappings()}
            preference = connection.execute(
                select(db.preferences.c.payload).where(db.preferences.c.id == "providers")
            ).scalar_one_or_none()
        return {
            "providers": [self.public(kind, rows.get(kind)) for kind in KINDS],
            "defaults": {**DEFAULTS, **(preference or {})},
        }

    def save(self, kind, body: ProviderUpdate):
        if kind == "video" and urlsplit(body.base_url).hostname == "api.openai.com":
            raise DomainError(
                "PROVIDER_RETIRED",
                "Official OpenAI Videos API is retired. Configure a compatible service.",
                422,
            )
        with self.database.transaction() as connection:
            old = (
                connection.execute(
                    select(db.provider_configs).where(db.provider_configs.c.id == kind)
                )
                .mappings()
                .first()
            )
            revision = old["revision"] if old else 0
            if body.revision != revision:
                raise DomainError("CONFIG_CONFLICT", "Settings changed. Reload before saving.", 409)
            secret = old["secret"] if old else None
            key = body.api_key.get_secret_value().strip() if body.api_key else ""
            if any(ord(char) < 32 or ord(char) > 126 for char in key):
                raise DomainError(
                    "VALIDATION_ERROR", "API Key must contain printable ASCII characters", 422
                )
            if body.clear_api_key:
                secret = None
            elif key:
                cipher = self.cipher()
                try:
                    existing_key = cipher.decrypt(secret.encode()).decode() if secret else ""
                except InvalidToken:
                    existing_key = ""
                if existing_key != key:
                    secret = cipher.encrypt(key.encode()).decode()
            config = body.model_dump(exclude={"api_key", "clear_api_key"})
            if old and config == old["config"] and secret == old["secret"]:
                return self.public(kind, old)
            config["revision"] = revision + 1
            values = {"revision": revision + 1, "config": config, "secret": secret}
            if old:
                connection.execute(
                    update(db.provider_configs)
                    .where(db.provider_configs.c.id == kind)
                    .values(**values)
                )
            else:
                connection.execute(insert(db.provider_configs).values(id=kind, **values))
            connection.execute(insert(db.provider_revisions).values(provider_id=kind, **values))
            return self.public(kind, values)

    def runtime(self, kind, revision=None):
        if kind not in KINDS:
            raise DomainError("MODEL_UNAVAILABLE", "Unknown provider", 422)
        with self.database.engine.connect() as connection:
            table = db.provider_configs if revision is None else db.provider_revisions
            clause = (
                table.c.id == kind
                if revision is None
                else ((table.c.provider_id == kind) & (table.c.revision == revision))
            )
            row = connection.execute(select(table).where(clause)).mappings().first()
        if not row:
            raise DomainError("MODEL_UNAVAILABLE", "Configure this provider first", 422)
        key = ""
        if row["secret"]:
            try:
                key = self.cipher().decrypt(row["secret"].encode()).decode()
            except InvalidToken as error:
                raise DomainError(
                    "SECRET_STORE_UNAVAILABLE", "Cannot decrypt provider credential", 500
                ) from error
        return {**row["config"], "kind": kind, "provider_id": f"openai-{kind}", "api_key": key}

    def snapshot(self, provider_id, capability):
        kind = provider_id.removeprefix("openai-")
        config = self.runtime(kind)
        if not config["enabled"]:
            raise DomainError("MODEL_UNAVAILABLE", "Enable this provider in settings first", 422)
        if CAPABILITIES[kind] != capability:
            raise DomainError(
                "CAPABILITY_UNSUPPORTED", "Provider category does not match operation", 422
            )
        return {k: v for k, v in config.items() if k != "api_key"}

    def preview_runtime(self, kind, body):
        if body is None:
            return self.runtime(kind)
        if kind == "video" and urlsplit(body.base_url).hostname == "api.openai.com":
            raise DomainError(
                "PROVIDER_RETIRED", "Configure a third-party compatible video service", 422
            )
        key = body.api_key.get_secret_value().strip() if body.api_key else ""
        if not key and not body.clear_api_key:
            try:
                key = self.runtime(kind)["api_key"]
            except DomainError as error:
                if error.code != "MODEL_UNAVAILABLE":
                    raise
        config = body.model_dump(exclude={"api_key", "clear_api_key"})
        return {**config, "kind": kind, "api_key": key}

    def set_defaults(self, body: DefaultUpdate):
        changes = body.model_dump(exclude_none=True)
        for kind, provider_id in changes.items():
            if provider_id == DEFAULTS[kind] and kind in {"llm", "video"}:
                continue
            if provider_id != f"openai-{kind}":
                raise DomainError(
                    "CAPABILITY_UNSUPPORTED", "Default provider category mismatch", 422
                )
            self.snapshot(provider_id, CAPABILITIES[kind])
        with self.database.transaction() as connection:
            old = connection.execute(
                select(db.preferences.c.payload).where(db.preferences.c.id == "providers")
            ).scalar_one_or_none()
            values = {**DEFAULTS, **(old or {}), **changes}
            if old is None:
                connection.execute(insert(db.preferences).values(id="providers", payload=values))
            else:
                connection.execute(
                    update(db.preferences)
                    .where(db.preferences.c.id == "providers")
                    .values(payload=values)
                )
        return values
