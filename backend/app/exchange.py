"""Snapshot-bound editorial exchange artifacts produced by the independent Worker.

OTIO's native adapter is intentional: emitting similar-looking JSON without the
native schema is not a valid fallback. Media are copied only from owned immutable
asset rows, never from paths supplied by the caller or embedded in an APR.
"""

import hashlib
import importlib
import json
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app import db
from app.errors import DomainError
from app.media import Cancelled

MAX_EXPORT_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 16 * 1024**2
MAX_EXPORT_ASSETS = 10000
CHUNK_BYTES = 512 * 1024
EXPORT_VERSION = "0.2.0"
LIMITATIONS = [
    "Captions are timed markers and AFDS metadata; editors may not create subtitle tracks.",
    "Audio gain and mute are AFDS metadata; receiving editors must apply these values.",
    "Overlapping audio clips use parallel lanes retaining their original AFDS track ID.",
    "Standalone OTIO references media/ URIs; download the ZIP for the referenced media.",
]
_PRIVATE_KEYS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "token",
    "secret",
    "password",
    "privatekey",
    "credentials",
    "authorization",
    "headers",
    "providerconfig",
    "providerconfigs",
    "jobs",
}
_EXTENSIONS = {
    "video": {".mp4", ".mov", ".mkv", ".webm"},
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"},
}


class ManifestAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: Literal["video", "image", "audio"]


class ExportSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_format: Literal["otio", "zip"]
    snapshot_revision: int = Field(ge=1)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot: dict
    asset_manifest: list[ManifestAsset] = Field(max_length=MAX_EXPORT_ASSETS)
    provider_id: Literal["local-exchange"] = "local-exchange"
    model_id: Literal["opentimelineio"] = "opentimelineio"
    adapter_version: Literal["0.2.0"] = EXPORT_VERSION


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _redact(value):
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if key.casefold().replace("_", "").replace("-", "") not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def export_request(apr, format):
    """Detach and validate an APR; pin the revision and every exported asset hash.

    Deliberately includes all assets listed in the APR, including posters and
    reference images, so the packaged APR remains a complete editable snapshot.
    No files or provider settings are read here.
    """
    from app.project_engine import validate

    if format not in {"otio", "zip"}:
        raise DomainError("VALIDATION_ERROR", "Export format must be otio or zip", 422)
    snapshot = validate(_redact(apr))
    encoded = _json(snapshot)
    if len(encoded) > MAX_METADATA_BYTES or len(snapshot["assets"]) > MAX_EXPORT_ASSETS:
        raise DomainError("EXPORT_TOO_LARGE", "Project exceeds export limits", 413)
    manifest = [
        {
            "asset_id": asset["id"],
            "sha256": asset["sha256"],
            "size_bytes": asset["size_bytes"],
            "media_type": asset["media_type"],
        }
        for asset in sorted(snapshot["assets"], key=lambda item: item["id"])
    ]
    return ExportSnapshot(
        export_format=format,
        snapshot_revision=snapshot["revision"],
        snapshot_sha256=hashlib.sha256(encoded).hexdigest(),
        snapshot=snapshot,
        asset_manifest=manifest,
    ).model_dump(mode="json")


def _load_otio():
    try:
        return importlib.import_module("opentimelineio")
    except (ImportError, OSError) as error:
        raise DomainError(
            "EXPORT_DEPENDENCY_MISSING",
            "Native OpenTimelineIO is unavailable. Install the project's export dependency.",
            503,
        ) from error


def _relative_file(root, relative, required_root):
    """Reject absolute/traversal paths and resolved symlink/junction escapes."""
    if not isinstance(relative, str):
        raise DomainError("INVALID_PATH", "Invalid stored artifact path", 403)
    parts = relative.replace("\\", "/").split("/")
    if (
        PureWindowsPath(relative).drive
        or PureWindowsPath(relative).is_absolute()
        or PurePosixPath(relative).is_absolute()
        or ".." in parts
    ):
        raise DomainError("INVALID_PATH", "Stored file is outside the project store", 403)
    root = Path(root).resolve()
    path = (root / relative).resolve()
    required_root = Path(required_root).resolve()
    if not required_root.is_relative_to(root) or not path.is_relative_to(required_root):
        raise DomainError("INVALID_PATH", "Stored file is outside the project store", 403)
    if not path.is_file():
        raise DomainError("MISSING_ASSET", "An exported file is missing", 404)
    return path


def _hash_file(path, checkpoint):
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            checkpoint()
            sha.update(chunk)
            size += len(chunk)
            if size > MAX_EXPORT_BYTES:
                raise DomainError("EXPORT_TOO_LARGE", "Media exceeds export size limits", 413)
    return sha.hexdigest(), size


def _owned_assets(database, project_id, snapshot, checkpoint):
    with database.engine.connect() as connection:
        rows = {
            row["id"]: row
            for row in connection.execute(
                select(db.assets).where(db.assets.c.project_id == project_id)
            ).mappings()
        }
    assets = []
    total = 0
    root = database.settings.data_dir
    asset_root = root / "projects" / project_id / "assets"
    for asset in snapshot["assets"]:
        checkpoint()
        row = rows.get(asset["id"])
        if not row:
            raise DomainError("NOT_FOUND", "Asset not found in this project", 404)
        stored = row["payload"]
        if any(
            stored.get(key) != asset[key] for key in ("id", "sha256", "size_bytes", "media_type")
        ):
            raise DomainError(
                "EXPORT_SNAPSHOT_MISMATCH", "Stored media differs from the export snapshot", 409
            )
        path = _relative_file(root, row["path"], asset_root)
        suffix = path.suffix.lower()
        if suffix not in _EXTENSIONS[asset["media_type"]]:
            raise DomainError("INVALID_MEDIA", "Unsupported export media extension", 422)
        if path.stat().st_size != asset["size_bytes"]:
            raise DomainError("HASH_MISMATCH", "Export media integrity check failed", 422)
        total += asset["size_bytes"]
        if total > MAX_EXPORT_BYTES:
            raise DomainError("EXPORT_TOO_LARGE", "Project exceeds export size limits", 413)
        sha, size = _hash_file(path, checkpoint)
        if sha != asset["sha256"] or size != asset["size_bytes"]:
            raise DomainError("HASH_MISMATCH", "Export media integrity check failed", 422)
        assets.append({**asset, "uri": f"media/{sha}{suffix}", "_path": path})
    return assets


def _otio_document(otio, snapshot, assets):
    fmt = snapshot["format"]
    rate = fmt["fps_num"] / fmt["fps_den"]

    def span(start, duration, fps=rate):
        return otio.opentime.TimeRange(
            otio.opentime.RationalTime(start, fps), otio.opentime.RationalTime(duration, fps)
        )

    timeline = otio.schema.Timeline(
        name=snapshot["title"], global_start_time=otio.opentime.RationalTime(0, rate)
    )
    timeline.metadata["AFDS"] = {
        "project_id": snapshot["project_id"],
        "revision": snapshot["revision"],
        "format": fmt,
        "limitations": LIMITATIONS,
        "captions": snapshot["timeline"].get("captions", []),
        "markers": snapshot["timeline"].get("markers", []),
        "world_states": snapshot.get("world_states", []),
        "continuity_links": snapshot.get("continuity_links", []),
    }
    by_id = {asset["id"]: asset for asset in assets}
    versions = {version["id"]: version for version in snapshot["asset_versions"]}
    shots = {shot["id"]: shot for shot in snapshot["shots"]}

    def reference(asset):
        available = None
        if asset.get("duration_frames") and asset.get("fps_num") and asset.get("fps_den"):
            available = span(0, asset["duration_frames"], asset["fps_num"] / asset["fps_den"])
        elif asset.get("duration_seconds"):
            available = span(0, asset["duration_seconds"], 1)
        return otio.schema.ExternalReference(
            target_url=asset["uri"],
            available_range=available,
            metadata={"AFDS": {"asset_id": asset["id"], "sha256": asset["sha256"]}},
        )

    for original in snapshot["timeline"]["tracks"]:
        if original["type"] == "audio":
            # OTIO tracks concatenate their children. Split overlaps into the
            # first available lane instead of silently changing their timing.
            lanes = [[]]
            lane_ends = [0]
            for item in sorted(original["items"], key=lambda item: item["start_frame"]):
                lane = next(
                    (index for index, end in enumerate(lane_ends) if end <= item["start_frame"]),
                    None,
                )
                if lane is None:
                    lane = len(lanes)
                    lanes.append([])
                    lane_ends.append(0)
                lanes[lane].append(item)
                lane_ends[lane] = item["start_frame"] + item["duration_frames"]
            for index, items in enumerate(lanes):
                track = otio.schema.Track(
                    name=original.get("name") or original["id"],
                    kind=otio.schema.TrackKind.Audio,
                )
                track.enabled = not original.get("muted", False)
                track.metadata["AFDS"] = {
                    "track_id": original["id"],
                    "lane": index,
                    "muted": original.get("muted", False),
                    "gain_db": original.get("gain_db", 0),
                }
                end = 0
                for item in items:
                    asset = by_id[item["asset_id"]]
                    if asset["media_type"] != "audio":
                        raise DomainError("INVALID_MEDIA", "Audio track requires audio media", 422)
                    if item["start_frame"] > end:
                        track.append(
                            otio.schema.Gap(source_range=span(0, item["start_frame"] - end))
                        )
                    track.append(
                        otio.schema.Clip(
                            name=asset["id"],
                            media_reference=reference(asset),
                            source_range=span(
                                item.get("source_start_frame", 0), item["duration_frames"]
                            ),
                            metadata={"AFDS": {**item, "sha256": asset["sha256"]}},
                        )
                    )
                    end = item["start_frame"] + item["duration_frames"]
                timeline.tracks.append(track)
            continue
        track = otio.schema.Track(name=original["id"], kind=otio.schema.TrackKind.Video)
        track.metadata["AFDS"] = {"track_id": original["id"], "source_audio_policy": "silent"}
        end = 0
        for item in original["items"]:
            shot = shots[item["shot_id"]]
            version = versions.get(shot["active_asset_version_id"])
            if not version:
                raise DomainError("MISSING_ASSET", "Select media for each timeline shot", 409)
            asset = by_id[version["asset_id"]]
            if asset["media_type"] != "video":
                raise DomainError("INVALID_MEDIA", "Timeline shot requires video media", 422)
            if item["start_frame"] > end:
                track.append(otio.schema.Gap(source_range=span(0, item["start_frame"] - end)))
            track.append(
                otio.schema.Clip(
                    name=shot["title"],
                    media_reference=reference(asset),
                    source_range=span(0, item["duration_frames"]),
                    metadata={
                        "AFDS": {
                            "shot_id": shot["id"],
                            "asset_id": asset["id"],
                            "asset_version_id": version["id"],
                            "sha256": asset["sha256"],
                            "status": shot["status"],
                            "locked": shot["locked"],
                        }
                    },
                )
            )
            end = item["start_frame"] + item["duration_frames"]
        timeline.tracks.append(track)
    for caption in snapshot["timeline"].get("captions", []):
        timeline.tracks.markers.append(
            otio.schema.Marker(
                name=caption["text"],
                marked_range=span(caption["start_frame"], caption["duration_frames"]),
                metadata={"AFDS": {**caption, "kind": "caption"}},
            )
        )
    for marker in snapshot["timeline"].get("markers", []):
        timeline.tracks.markers.append(
            otio.schema.Marker(
                name=marker["label"],
                marked_range=span(marker["frame"], 0),
                metadata={"AFDS": marker},
            )
        )
    return otio.adapters.write_to_string(timeline, adapter_name="otio_json").encode("utf-8")


def execute_export(database, job, work, checkpoint):
    """Produce a work-local artifact; the caller must fence immutable publication.

    This function never mutates APR/jobs or publishes a download path. The
    independent Worker supplies its lease checkpoint and performs publication.
    """
    started = time.monotonic()

    def check():
        if not checkpoint():
            raise Cancelled()
        if time.monotonic() - started > database.settings.media_timeout:
            raise TimeoutError("Project export exceeded its time limit")

    check()
    try:
        request = ExportSnapshot.model_validate(job["request"]).model_dump(mode="json")
        expected = export_request(request["snapshot"], request["export_format"])
    except (ValidationError, KeyError, ValueError, TypeError) as error:
        raise DomainError("VALIDATION_ERROR", "Invalid export snapshot", 422) from error
    if job.get("operation") != "export_project" or request != expected:
        raise DomainError("EXPORT_SNAPSHOT_MISMATCH", "Export snapshot binding is invalid", 409)
    snapshot = request["snapshot"]
    if snapshot["project_id"] != str(job["project_id"]):
        raise DomainError("NOT_FOUND", "Export does not belong to this project", 404)
    root = database.settings.data_dir.resolve()
    work = Path(work).resolve()
    if not work.is_relative_to(root / "cache"):
        raise DomainError("INVALID_PATH", "Export work must be within the worker cache", 403)
    otio = _load_otio()
    assets = _owned_assets(database, snapshot["project_id"], snapshot, check)
    check()
    document = _otio_document(otio, snapshot, assets)
    manifest = {
        "export_version": EXPORT_VERSION,
        "project_id": snapshot["project_id"],
        "snapshot_revision": request["snapshot_revision"],
        "snapshot_sha256": request["snapshot_sha256"],
        "apr": "project.apr.json",
        "timeline": "timeline.otio",
        "assets": [{k: v for k, v in asset.items() if k != "_path"} for asset in assets],
        "limitations": LIMITATIONS,
    }
    metadata_files = {
        "project.apr.json": _json(snapshot),
        "manifest.json": _json(manifest),
        "timeline.otio": document,
        "README.txt": (
            "AFDS portable project export\n"
            "Mock assets remain labelled DEMO/MOCK in their provenance and media.\n"
            + "\n".join(LIMITATIONS)
            + "\n"
        ).encode(),
    }
    metadata_size = sum(map(len, metadata_files.values()))
    if metadata_size > MAX_METADATA_BYTES:
        raise DomainError("EXPORT_TOO_LARGE", "Project metadata exceeds export limits", 413)
    if metadata_size + sum(a["size_bytes"] for a in assets) > MAX_EXPORT_BYTES:
        raise DomainError("EXPORT_TOO_LARGE", "Project exceeds export size limits", 413)
    work.mkdir(parents=True, exist_ok=True)
    kind = request["export_format"]
    output = work / f"project.{kind}"
    try:
        handle = output.open("xb")
    except FileExistsError as error:
        raise DomainError("EXPORT_ALREADY_EXISTS", "Export artifact is immutable", 409) from error
    try:
        with handle:
            if kind == "otio":
                handle.write(document)
            else:
                with ZipFile(handle, "w", allowZip64=True) as archive:
                    for name, content in metadata_files.items():
                        check()
                        archive.writestr(name, content, compress_type=ZIP_DEFLATED)
                    copied = set()
                    for asset in assets:
                        check()
                        if asset["uri"] in copied:
                            continue
                        sha, size = hashlib.sha256(), 0
                        with (
                            asset["_path"].open("rb") as source,
                            archive.open(asset["uri"], "w", force_zip64=True) as destination,
                        ):
                            while chunk := source.read(CHUNK_BYTES):
                                check()
                                size += len(chunk)
                                if size > asset["size_bytes"]:
                                    raise DomainError(
                                        "HASH_MISMATCH", "Media changed during export", 422
                                    )
                                sha.update(chunk)
                                destination.write(chunk)
                        if sha.hexdigest() != asset["sha256"] or size != asset["size_bytes"]:
                            raise DomainError("HASH_MISMATCH", "Media changed during export", 422)
                        copied.add(asset["uri"])
        check()
        if output.stat().st_size > MAX_EXPORT_BYTES:
            raise DomainError("EXPORT_TOO_LARGE", "Artifact exceeds export size limits", 413)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "path": output,
        "filename": f"afds-{snapshot['project_id']}-r{snapshot['revision']}.{kind}",
        "media_type": "application/zip" if kind == "zip" else "application/vnd.otio+json",
        "metadata": {
            "snapshot_revision": request["snapshot_revision"],
            "snapshot_sha256": request["snapshot_sha256"],
            "asset_count": len(assets),
            "limitations": LIMITATIONS,
        },
    }
