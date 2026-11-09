"""Bounded portable archive metadata; untrusted ZIP names never become paths."""

import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas import Asset, Schema

MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 16 * 1024**2
MAX_MEMBERS = 10004
METADATA_NAMES = {"project.apr.json", "manifest.json", "timeline.otio", "README.txt"}


class PortableAsset(Asset):
    uri: str = Field(
        pattern=r"^media/[a-f0-9]{64}\.(mp4|mov|mkv|webm|png|jpg|jpeg|webp|wav|mp3|m4a|aac|flac|ogg)$"
    )


class RestoreManifest(Schema):
    export_version: Literal["0.2.0"]
    project_id: UUID
    snapshot_revision: int = Field(ge=1)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    apr: Literal["project.apr.json"]
    timeline: Literal["timeline.otio"]
    assets: list[PortableAsset] = Field(max_length=10000)
    limitations: list[str] = Field(max_length=20)


class RestoreJobRequest(Schema):
    project_id: UUID
    request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    staging_path: str = Field(max_length=500)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)
    base_revision: int = Field(ge=1)
    provider_id: Literal["local-restore"] = "local-restore"
    model_id: Literal["portable-project"] = "portable-project"
    adapter_version: Literal["0.5.0"] = "0.5.0"


def strict_json(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Nonfinite JSON number")

    return json.loads(content, object_pairs_hook=unique, parse_constant=invalid)
