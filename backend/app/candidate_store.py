"""Versioned model connections. Only server-side runtimes decrypt credentials."""

import time
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import Field
from sqlalchemy import JSON, Column, Integer, String, Table, insert, select, update

from app import db
from app.errors import DomainError
from app.provider_settings import CAPABILITIES, KINDS, ProviderConfig, ProviderStore, ProviderUpdate
from app.routing_schema import (
    CandidateId,
    LatencyObservation,
    ProviderCandidate,
    QualityObservation,
)

candidates = Table(
    "provider_candidates",
    db.metadata,
    Column("id", String(100), primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("config", JSON, nullable=False),
    Column("secret", String),
)
candidate_revisions = Table(
    "candidate_revisions",
    db.metadata,
    Column("candidate_id", String(100), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("config", JSON, nullable=False),
    Column("secret", String),
)


class CandidateUpdate(ProviderUpdate):
    label: str = Field(min_length=1, max_length=120)
    all_projects: bool = True
    project_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    quality: QualityObservation | None = None
    latency: LatencyObservation | None = None
    priority: int = Field(default=0, ge=0, le=10_000)


METADATA_FIELDS = {"label", "all_projects", "project_ids", "quality", "latency", "priority"}


def descriptor_values(identity, kind, revision, config):
    capabilities = [CAPABILITIES[kind]]
    if kind == "llm" and config.get("vision_enabled"):
        capabilities.append("visual_review")
    roles = []
    if config.get("protocol") == "comfyui":
        roles = [
            role
            for role in (config.get("workflow") or {}).get("bindings", {})
            if role in {"reference_image", "start_image", "end_image"}
        ]
    return {
        "id": identity,
        "label": config.get("label", f"默认 {kind}"),
        "kind": kind,
        "config_revision": revision,
        "model": config.get("model") or "(未设置)",
        "capabilities": capabilities,
        "enabled": config["enabled"],
        "estimated_cost": config.get("request_cost"),
        "currency": config.get("cost_currency", "USD"),
        "timestamps": config.get("transcription_timestamps", False),
        "reference_roles": roles,
        **{key: config[key] for key in METADATA_FIELDS if key in config},
    }


class CandidateStore:
    def __init__(self, database):
        self.database = database
        self.legacy = ProviderStore(database)

    @staticmethod
    def health(identity, kind, revision, observations):
        keys = [f"candidate-health:{identity}:{revision}"]
        if identity == f"default-{kind}":
            keys.append(f"provider-health:{kind}:{revision}")
        latest = max(
            (observations.get(key, {}) for key in keys),
            key=lambda value: value.get("checked_at", 0),
        )
        status = latest.get("status", "untested")
        return {"connected": "healthy", "failed": "unhealthy"}.get(status, status)

    @staticmethod
    def mirror_legacy(connection, kind, values):
        identity = f"default-{kind}"
        old = (
            connection.execute(select(candidates).where(candidates.c.id == identity))
            .mappings()
            .first()
        )
        metadata = {
            key: old["config"][key] for key in METADATA_FIELDS if old and key in old["config"]
        }
        row = {
            **values,
            "kind": kind,
            "config": {
                "label": f"默认 {kind}",
                "all_projects": True,
                **values["config"],
                **metadata,
            },
        }
        if old:
            connection.execute(update(candidates).where(candidates.c.id == identity).values(**row))
        else:
            connection.execute(insert(candidates).values(id=identity, **row))
        connection.execute(insert(candidate_revisions).values(candidate_id=identity, **row))

    def public(self, row):
        return {
            **row["config"],
            "id": row["id"],
            "kind": row["kind"],
            "revision": row["revision"],
            "key_configured": bool(row["secret"]),
            "has_api_key": bool(row["secret"]),
            "health": "untested",
        }

    def list(self):
        with self.database.engine.connect() as connection:
            rows = connection.execute(select(candidates).order_by(candidates.c.id)).mappings().all()
            observations = dict(
                connection.execute(
                    select(db.preferences.c.id, db.preferences.c.payload).where(
                        db.preferences.c.id.startswith("candidate-health:")
                        | db.preferences.c.id.startswith("provider-health:")
                    )
                ).all()
            )
        result = []
        for row in rows:
            profile = self.public(row)
            profile["health"] = self.health(row["id"], row["kind"], row["revision"], observations)
            result.append(profile)
        return result

    def descriptor(self, profile):
        return ProviderCandidate.model_validate(
            {
                **descriptor_values(profile["id"], profile["kind"], profile["revision"], profile),
                "health": profile.get("health", "untested"),
            }
        )

    def candidates(self, project_id):
        values = [self.descriptor(profile) for profile in self.list()]
        return [
            value
            for value in values
            if value.all_projects or str(project_id) in map(str, value.project_ids)
        ]

    def save(self, identity: str, kind: str, body: CandidateUpdate):
        from pydantic import TypeAdapter

        TypeAdapter(CandidateId).validate_python(identity)
        if identity in {"auto", "mock-director", "mock-video-local"} or identity.startswith(
            "openai-"
        ):
            raise DomainError("RESERVED_CONNECTION_ID", "Choose a custom connection ID", 422)
        if kind not in KINDS or (identity.startswith("default-") and identity != f"default-{kind}"):
            raise DomainError("CAPABILITY_UNSUPPORTED", "Connection category is fixed", 422)
        connection_body = ProviderUpdate.model_validate(body.model_dump(exclude=METADATA_FIELDS))
        self.legacy.validate_protocol(kind, connection_body)
        # Apply the same target checks as legacy saves without making a network request.
        self.legacy.preview_runtime(kind, connection_body)
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, "provider-store")
            old = (
                connection.execute(select(candidates).where(candidates.c.id == identity))
                .mappings()
                .first()
            )
            revision = old["revision"] if old else 0
            if body.revision != revision:
                raise DomainError("CONFIG_CONFLICT", "Settings changed. Reload before saving.", 409)
            if old and old["kind"] != kind:
                raise DomainError("CAPABILITY_UNSUPPORTED", "Connection category is fixed", 422)
            secret = old["secret"] if old else None
            key = body.api_key.get_secret_value().strip() if body.api_key else ""
            if any(ord(char) < 32 or ord(char) > 126 for char in key):
                raise DomainError(
                    "VALIDATION_ERROR", "API Key must contain printable ASCII characters", 422
                )
            if body.clear_api_key:
                secret = None
            elif key:
                secret = self.legacy.cipher().encrypt(key.encode()).decode()
            config = body.model_dump(mode="json", exclude={"api_key", "clear_api_key"})
            config["revision"] = revision + 1
            # Validate project UUIDs and the public summary before persisting anything.
            ProviderCandidate.model_validate(
                descriptor_values(identity, kind, revision + 1, config)
            )
            values = {"kind": kind, "revision": revision + 1, "config": config, "secret": secret}
            if old:
                connection.execute(
                    update(candidates).where(candidates.c.id == identity).values(**values)
                )
            else:
                connection.execute(insert(candidates).values(id=identity, **values))
            connection.execute(insert(candidate_revisions).values(candidate_id=identity, **values))
            if identity == f"default-{kind}":
                legacy_values = {
                    "revision": revision + 1,
                    "secret": secret,
                    "config": {
                        key: value for key, value in config.items() if key not in METADATA_FIELDS
                    },
                }
                exists = connection.execute(
                    select(db.provider_configs.c.id).where(db.provider_configs.c.id == kind)
                ).first()
                if exists:
                    connection.execute(
                        update(db.provider_configs)
                        .where(db.provider_configs.c.id == kind)
                        .values(**legacy_values)
                    )
                else:
                    connection.execute(insert(db.provider_configs).values(id=kind, **legacy_values))
                connection.execute(
                    insert(db.provider_revisions).values(provider_id=kind, **legacy_values)
                )
            return self.public({"id": identity, **values})

    def row(self, identity, revision=None):
        with self.database.engine.connect() as connection:
            table = candidates if revision is None else candidate_revisions
            clause = (
                table.c.id == identity
                if revision is None
                else ((table.c.candidate_id == identity) & (table.c.revision == revision))
            )
            row = connection.execute(select(table).where(clause)).mappings().first()
        if row is None:
            raise DomainError("MODEL_UNAVAILABLE", "Model connection version is unavailable", 422)
        return dict(row)

    def profile(self, identity, revision=None):
        profile = self.public({**self.row(identity, revision), "id": identity})
        with self.database.engine.connect() as connection:
            observations = dict(
                connection.execute(
                    select(db.preferences.c.id, db.preferences.c.payload).where(
                        db.preferences.c.id.in_(
                            [
                                f"candidate-health:{identity}:{profile['revision']}",
                                f"provider-health:{profile['kind']}:{profile['revision']}",
                            ]
                        )
                    )
                ).all()
            )
        profile["health"] = self.health(
            identity, profile["kind"], profile["revision"], observations
        )
        return profile

    def record_health(self, config, *, error=None):
        identity = config["connection_id"]
        key = f"candidate-health:{identity}:{config['revision']}"
        with self.database.transaction() as connection:
            db.lock_named_resource(connection, "provider-store")
            current = connection.execute(
                select(candidates.c.revision).where(candidates.c.id == identity)
            ).scalar_one_or_none()
            if current != config["revision"]:
                return
            payload = {
                "status": "unhealthy" if error else "healthy",
                "checked_at": time.time(),
                "error_code": error,
                "generation_tested": False,
            }
            exists = connection.execute(
                select(db.preferences.c.id).where(db.preferences.c.id == key)
            ).first()
            if exists:
                connection.execute(
                    update(db.preferences).where(db.preferences.c.id == key).values(payload=payload)
                )
            else:
                connection.execute(insert(db.preferences).values(id=key, payload=payload))

    def runtime(self, identity, revision=None):
        row = self.row(identity, revision)
        key = ""
        if row["secret"]:
            try:
                key = self.legacy.cipher().decrypt(row["secret"].encode()).decode()
            except InvalidToken as error:
                raise DomainError(
                    "SECRET_STORE_UNAVAILABLE", "Cannot decrypt provider credential", 500
                ) from error
        return {
            **row["config"],
            "kind": row["kind"],
            "connection_id": identity,
            "provider_id": f"openai-{row['kind']}",
            "api_key": key,
        }

    def snapshot(self, identity, capability):
        profile = self.profile(identity)
        descriptor = self.descriptor(profile)
        if not descriptor.enabled:
            raise DomainError("MODEL_UNAVAILABLE", "Enable this connection first", 422)
        if capability not in descriptor.capabilities:
            raise DomainError("CAPABILITY_UNSUPPORTED", "Connection capability does not match", 422)
        config = {key: profile[key] for key in ProviderConfig.model_fields if key in profile}
        return {
            **config,
            "kind": profile["kind"],
            "connection_id": identity,
            "provider_id": f"openai-{profile['kind']}",
        }
