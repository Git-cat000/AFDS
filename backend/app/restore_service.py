"""Worker-only portable recovery into an independent, owned project copy."""

import copy
import hashlib
import json
import math
import stat
import struct
import time
from fractions import Fraction
from pathlib import Path
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from pydantic import ValidationError
from sqlalchemy import insert

from app import db
from app import restore_schema as schema
from app.errors import DomainError
from app.exchange import _json, _load_otio, _redact
from app.execution.queue import Queue
from app.media import Cancelled, run_process
from app.project_engine import ProjectEngine, emit, validate
from app.schemas import new_id
from app.storage import contained, file_hash

CHUNK_BYTES = 512 * 1024
DEMUXERS = {
    ".mp4": "mov",
    ".mov": "mov",
    ".m4a": "mov",
    ".mkv": "matroska",
    ".webm": "matroska",
    ".png": "png_pipe",
    ".jpg": "jpeg_pipe",
    ".jpeg": "jpeg_pipe",
    ".webp": "webp_pipe",
    ".wav": "wav",
    ".mp3": "mp3",
    ".flac": "flac",
    ".ogg": "ogg",
    ".aac": "aac",
}
EXTENSIONS = {
    "video": {".mp4", ".mov", ".mkv", ".webm"},
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"},
}


def invalid(message="Invalid or unsupported AFDS project archive"):
    return DomainError("RESTORE_INVALID_ARCHIVE", message, 422)


def inspect_directory(path):
    # Bound central-directory allocation before ZipFile creates Python entries.
    # Current exports are <2GB/<10005members, so global ZIP64/spanned EOCDs are unnecessary.
    size = path.stat().st_size
    with path.open("rb") as handle:
        # Include the preceding 20-byte locator even with a maximum-length ZIP comment.
        handle.seek(max(0, size - 65577))
        tail = handle.read(65577)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or len(tail) - position < 22:
        raise invalid("ZIP directory is missing")
    if position >= 20 and tail[position - 20 : position - 16] == b"PK\x06\x07":
        # ZipFile otherwise replaces the checked legacy bounds with ZIP64 values.
        # Per-member ZIP64 headers from our exporter remain supported.
        raise invalid("Global ZIP64 directories are unsupported")
    _, disk, start, on_disk, count, length, offset, comment = struct.unpack_from(
        "<4s4H2LH", tail, position
    )
    if (
        disk
        or start
        or on_disk != count
        or count > schema.MAX_MEMBERS
        or length > schema.MAX_METADATA_BYTES
        or offset + length > size - len(tail) + position
        or position + 22 + comment != len(tail)
    ):
        raise invalid("ZIP directory exceeds supported bounds")
    return count


def probe_restored(settings, path, asset, checkpoint):
    suffix = path.suffix.lower()
    if suffix not in EXTENSIONS[asset["media_type"]]:
        raise invalid("Archive media type and extension disagree")
    args = [settings.binary("ffprobe"), "-v", "error", "-f", DEMUXERS[suffix]]
    if DEMUXERS[suffix] == "mov":
        args += ["-enable_drefs", "0"]
    args += [
        "-protocol_whitelist",
        "file",
        "-select_streams",
        "a:0" if asset["media_type"] == "audio" else "v:0",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_frames,duration,sample_rate,channels:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = json.loads(run_process(args, 30, checkpoint))
        stream = result["streams"][0]
        metadata = {"codec": stream["codec_name"]}
        if asset["media_type"] != "audio":
            width, height = int(stream["width"]), int(stream["height"])
            if not 0 < width * height <= 100_000_000:
                raise ValueError("Image dimensions out of bounds")
            metadata.update(width=width, height=height)
        if asset["media_type"] != "image":
            seconds = float(stream.get("duration") or result["format"]["duration"])
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("Invalid duration")
            metadata["duration_seconds"] = seconds
            if asset["media_type"] == "video":
                rate = Fraction(stream["avg_frame_rate"])
                if not 0 < rate <= 240:
                    raise ValueError("Invalid frame rate")
                metadata.update(
                    fps_num=rate.numerator,
                    fps_den=rate.denominator,
                    duration_frames=int(stream.get("nb_frames") or round(seconds * rate)),
                )
            else:
                rate, channels = int(stream["sample_rate"]), int(stream["channels"])
                if not 8000 <= rate <= 384000 or not 1 <= channels <= 8 or seconds > 3600:
                    raise ValueError("Unsupported audio profile")
                metadata.update(sample_rate=rate, channels=channels)
        for key, actual in metadata.items():
            expected = asset.get(key)
            if expected is not None:
                if key == "duration_seconds":
                    if not math.isfinite(expected) or abs(expected - actual) > 0.05:
                        raise ValueError("Duration mismatch")
                elif expected != actual:
                    raise ValueError("Media metadata mismatch")
        return metadata
    except (ValueError, KeyError, IndexError, TypeError, DomainError) as error:
        raise DomainError(
            "RESTORE_INVALID_MEDIA", "Archive media failed verification", 422
        ) from error


class RestoreService:
    def __init__(self, database):
        self.database = database
        self.settings = database.settings
        self.engine = ProjectEngine(database)
        self.queue = Queue(database)

    def execute(self, job, work, checkpoint):
        started = time.monotonic()

        def check():
            if not checkpoint():
                raise Cancelled()
            if time.monotonic() - started > self.settings.media_timeout:
                raise TimeoutError("Restore exceeded time limit")
            return True

        try:
            request = schema.RestoreJobRequest.model_validate(job["request"]).model_dump(
                mode="json"
            )
            pid = str(job["project_id"])
            relative = Path(request["staging_path"])
            if (
                request["project_id"] != pid
                or len(relative.parts) != 3
                or relative.parts[:2] != ("uploads", pid)
                or relative.suffix != ".zip"
            ):
                raise invalid("Restore staging file does not belong to the destination")
            UUID(relative.stem)
            source = contained(self.settings.data_dir, relative)
            if not source.is_file() or source.stat().st_size != request["size_bytes"]:
                raise invalid("Restore staging file is missing or changed")
            work = Path(work).resolve()
            if not work.is_relative_to(self.settings.data_dir / "cache"):
                raise invalid("Restore work directory is invalid")
            work.mkdir(parents=True, exist_ok=True)
            check()
            sha = hashlib.sha256()
            with source.open("rb") as handle:
                while chunk := handle.read(CHUNK_BYTES):
                    check()
                    sha.update(chunk)
            if sha.hexdigest() != request["sha256"]:
                raise invalid("Restore staging file failed integrity check")
            expected_count = inspect_directory(source)
            with ZipFile(source) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if (
                    len(infos) != expected_count
                    or len(infos) > schema.MAX_MEMBERS
                    or len(names) != len(set(names))
                ):
                    raise invalid("Archive member count or duplicate names are invalid")
                if sum(i.file_size for i in infos) > schema.MAX_ARCHIVE_BYTES:
                    raise invalid("Uncompressed project archive exceeds limit")
                for info in infos:
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    if (
                        info.is_dir()
                        or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                        or info.flag_bits & 1
                        or mode not in {0, stat.S_IFREG}
                        or "\\" in info.filename
                        or ":" in info.filename
                        or info.filename.startswith("/")
                        or ".." in info.filename.split("/")
                    ):
                        raise invalid("Archive contains an unsafe member")
                if not schema.METADATA_NAMES <= set(names):
                    raise invalid("Archive metadata is missing")
                if (
                    sum(archive.getinfo(n).file_size for n in schema.METADATA_NAMES)
                    > schema.MAX_METADATA_BYTES
                ):
                    raise invalid("Archive metadata exceeds limit")
                manifest = schema.RestoreManifest.model_validate(
                    schema.strict_json(archive.read("manifest.json"))
                ).model_dump(mode="json")
                raw_apr = schema.strict_json(archive.read("project.apr.json"))
                if (
                    _redact(raw_apr) != raw_apr
                    or hashlib.sha256(_json(raw_apr)).hexdigest() != manifest["snapshot_sha256"]
                ):
                    raise invalid("Project snapshot hash or private fields are invalid")
                apr = validate(raw_apr)
                if (
                    apr["project_id"] != manifest["project_id"]
                    or apr["revision"] != manifest["snapshot_revision"]
                ):
                    raise invalid("Project snapshot binding is invalid")
                assets = {a["id"]: a for a in apr["assets"]}
                rows = manifest["assets"]
                if len({a["id"] for a in rows}) != len(rows) or set(assets) != {
                    a["id"] for a in rows
                }:
                    raise invalid("Project asset manifest is incomplete")
                members = set(schema.METADATA_NAMES)
                for row in rows:
                    if {k: v for k, v in row.items() if k != "uri"} != assets[row["id"]] or Path(
                        row["uri"]
                    ).stem != row["sha256"]:
                        raise invalid("Asset manifest metadata differs from project")
                    members.add(row["uri"])
                if set(names) != members:
                    raise invalid("Archive contains missing or unrelated files")
                otio = _load_otio()
                timeline = otio.adapters.read_from_string(
                    archive.read("timeline.otio").decode("utf-8"), adapter_name="otio_json"
                )
                frames = sum(i["duration_frames"] for i in apr["timeline"]["tracks"][0]["items"])
                rate = apr["format"]["fps_num"] / apr["format"]["fps_den"]
                if (
                    not isinstance(timeline, otio.schema.Timeline)
                    or timeline.duration().to_frames(rate) != frames
                ):
                    raise invalid("OTIO duration differs from APR")
                for clip in timeline.find_clips():
                    if (
                        not isinstance(clip.media_reference, otio.schema.ExternalReference)
                        or clip.media_reference.target_url not in members - schema.METADATA_NAMES
                    ):
                        raise invalid("OTIO references unavailable media")
                files, extracted = [], {}
                total = sum(archive.getinfo(n).file_size for n in schema.METADATA_NAMES)
                for row in rows:
                    check()
                    info = archive.getinfo(row["uri"])
                    if info.file_size != row["size_bytes"]:
                        raise invalid("Archive media size differs from manifest")
                    path = work / Path(row["uri"]).name
                    if row["uri"] not in extracted:
                        digest, count = hashlib.sha256(), 0
                        with archive.open(info) as reader, path.open("xb") as writer:
                            while chunk := reader.read(CHUNK_BYTES):
                                check()
                                count += len(chunk)
                                total += len(chunk)
                                if count > row["size_bytes"] or total > schema.MAX_ARCHIVE_BYTES:
                                    raise invalid("Archive expansion exceeds limit")
                                digest.update(chunk)
                                writer.write(chunk)
                        if count != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                            raise invalid("Archive media hash differs from manifest")
                        extracted[row["uri"]] = path
                    metadata = probe_restored(self.settings, path, assets[row["id"]], check)
                    files.append({"original_id": row["id"], "path": path, "metadata": metadata})
                check()
            restored, mapping = self.remap(apr, request, job["id"])
            for item in files:
                aid = mapping[item.pop("original_id")]
                asset = next(a for a in restored["assets"] if a["id"] == aid)
                asset.update(item.pop("metadata"))
                item["asset_id"] = aid
            restored = validate(restored)
            return {
                "apr": restored,
                "files": files,
                "receipt": {
                    "source_project_id": apr["project_id"],
                    "source_revision": apr["revision"],
                    "snapshot_sha256": manifest["snapshot_sha256"],
                    "asset_count": len(files),
                    "history_restored": False,
                    "cost_ledger_restored": False,
                },
            }
        except (
            ValidationError,
            ValueError,
            KeyError,
            TypeError,
            BadZipFile,
            UnicodeError,
            RuntimeError,
            RecursionError,
        ) as error:
            raise invalid() from error

    def remap(self, apr, request, job_id):
        ids = [apr["project_id"]]
        for key in (
            "scenes",
            "shots",
            "characters",
            "locations",
            "world_states",
            "continuity_links",
            "assets",
            "asset_versions",
        ):
            ids += [item["id"] for item in apr[key]]
        ids += [
            item["id"]
            for key in ("tracks", "captions", "markers")
            for item in apr["timeline"].get(key, [])
        ]
        if len(ids) != len(set(ids)):
            raise invalid("Entity identifiers collide")
        mapping = {key: new_id() for key in ids}
        mapping[apr["project_id"]] = request["project_id"]

        def fields(item, single=(), many=()):
            for key in single:
                if item.get(key) is not None:
                    item[key] = mapping[item[key]]
            for key in many:
                item[key] = [mapping[value] for value in item.get(key, [])]

        # Only schema-defined identities/references are rewritten. Track IDs are
        # ordinary strings and can also occur in prose, enums or arbitrary metadata.
        result = copy.deepcopy(apr)
        fields(result, ("project_id", "render_asset_id"))
        for item in result["scenes"]:
            fields(item, ("id", "location_id"), ("shot_ids",))
        for item in result["shots"]:
            fields(
                item,
                (
                    "id",
                    "scene_id",
                    "input_state_ref",
                    "output_state_ref",
                    "active_asset_version_id",
                ),
                ("character_ids",),
            )
        for collection in ("characters", "locations"):
            for item in result[collection]:
                fields(item, ("id",), ("reference_asset_ids",))
        for item in result["world_states"]:
            fields(item, ("id", "scene_id", "shot_id"))
            for character in item["characters"]:
                fields(character, ("character_id",))
            for prop in item["props"]:
                fields(prop, ("holder_character_id",))
        for item in result["continuity_links"]:
            fields(item, ("id", "source_shot_id", "target_shot_id"))
        for item in result["assets"]:
            fields(item, ("id",))
        for item in result["asset_versions"]:
            fields(item, ("id", "shot_id", "asset_id", "poster_asset_id"))
        for track in result["timeline"]["tracks"]:
            fields(track, ("id",))
            for item in track["items"]:
                fields(item, ("shot_id",) if track["type"] == "video" else ("asset_id",))
        for collection in ("captions", "markers"):
            for item in result["timeline"][collection]:
                fields(item, ("id",))
        result.update(title=request["title"], revision=request["base_revision"] + 1, status="draft")
        result["budget"] = {
            "budget_limit": 0,
            "spent": 0,
            "reserved": 0,
            "currency": apr["budget"]["currency"],
            "allow_remote": False,
            "allow_unknown_cost": False,
        }
        for version in result["asset_versions"]:
            version["generated_by_job_id"] = job_id
        for original, asset in zip(apr["assets"], result["assets"], strict=True):
            asset["provenance"]["restored_from"] = {
                "project_id": apr["project_id"],
                "asset_id": original["id"],
            }
        return result, mapping

    def current(self, connection, job, owner):
        current = self.queue.get(connection, job["project_id"], job["id"])
        if (
            current["lease_owner"] != owner
            or current["attempt"] != job["attempt"]
            or not current["lease_expires_at"]
            or current["lease_expires_at"] < time.time()
        ):
            return None
        if current["state"] == "cancel_requested":
            self.queue.set_state(connection, current, "cancelled")
            return None
        return current if current["state"] in {"running", "verifying"} else None

    def publish(self, job, owner, result, elapsed_ms):
        with self.database.transaction() as connection:
            if not self.current(connection, job, owner):
                return
            before = self.engine.load(connection, job["project_id"])
            self.engine.check_revision(before, job["request"]["base_revision"])
        assets = {a["id"]: a for a in result["apr"]["assets"]}
        paths = {}
        for item in result["files"]:
            asset = assets[item["asset_id"]]
            sha = asset["sha256"]
            relative = (
                Path("projects")
                / job["project_id"]
                / "assets"
                / sha[:2]
                / f"{sha}{item['path'].suffix}"
            )
            destination = contained(self.settings.data_dir, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / f".{new_id()}.partial"
            try:
                if not destination.exists():
                    digest = hashlib.sha256()
                    with item["path"].open("rb") as reader, temporary.open("xb") as writer:
                        while chunk := reader.read(CHUNK_BYTES):
                            if not self.queue.heartbeat(job["id"], owner):
                                raise Cancelled()
                            digest.update(chunk)
                            writer.write(chunk)
                    if digest.hexdigest() != sha:
                        raise invalid("Verified media changed before publication")
                    temporary.replace(destination)
                elif file_hash(destination) != sha:
                    raise invalid("Destination immutable media is corrupt")
            finally:
                temporary.unlink(missing_ok=True)
            paths[asset["id"]] = relative.as_posix()
        with self.database.transaction() as connection:
            current = self.current(connection, job, owner)
            if not current:
                return
            before = self.engine.load(connection, job["project_id"])
            self.engine.check_revision(before, job["request"]["base_revision"])
            for aid, asset in assets.items():
                connection.execute(
                    insert(db.assets).values(
                        id=aid, project_id=job["project_id"], path=paths[aid], payload=asset
                    )
                )
            self.engine.save(
                connection, result["apr"], before, "Restore portable project copy", "worker"
            )
            self.queue.set_state(connection, current, "verifying", progress=95)
            self.queue.set_state(
                connection,
                current,
                "completed",
                progress=100,
                elapsed_ms=elapsed_ms,
                output_asset_ids=list(assets),
                restore_receipt=result["receipt"],
            )
            emit(
                connection,
                job["project_id"],
                "restore.completed",
                {"job_id": job["id"], "asset_count": len(assets)},
                job["id"],
            )
