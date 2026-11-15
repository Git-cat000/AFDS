"""Immutable local Agent Skills packages, with inspectable bounded resources."""

import base64
import binascii
import hashlib
import json
import mimetypes
import re
import stat
import tempfile
import time
import zipfile
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError
from sqlalchemy import func, insert, select

from app import db
from app.errors import DomainError
from app.restore_service import inspect_directory
from app.skill_schema import (
    MAX_FILE,
    MAX_FILES,
    MAX_UNPACKED,
    MAX_UPLOAD,
    STAGES,
    GuidanceSnapshot,
    SkillFile,
)

SECRET = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    rb"sk-[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def invalid(message):
    return DomainError("SKILL_INVALID", message, 422)


def safe_name(name):
    if not isinstance(name, str) or len(name) > 240 or not name or "\\" in name:
        raise invalid("Use bounded relative POSIX resource names")
    parts = name.split("/")
    if len(parts) > 12:
        raise invalid("Resource path is too deep")
    for part in parts:
        stem = part.split(".")[0].upper()
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or re.search(r'[\x00-\x1f<>:"|?*]', part)
            or stem
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)],
            }
            or part.lower() in {".git", ".ssh", ".aws", ".venv", "node_modules", "__pycache__"}
            or part.lower() == ".env"
            or part.lower().startswith(".env.")
        ):
            raise invalid("Resource name contains a forbidden path or credential directory")
    return name


def decode_files(files):
    values = {}
    seen = set()
    size = 0
    for item in files:
        name = safe_name(item.path)
        if name.casefold() in seen:
            raise invalid("Resource names collide")
        seen.add(name.casefold())
        try:
            data = base64.b64decode(item.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise invalid("Resource has invalid base64 data") from error
        size += len(data)
        if len(data) > MAX_FILE or size > MAX_UNPACKED:
            raise invalid("Skill resources exceed the package size limit")
        if SECRET.search(data):
            raise invalid("Remove private credentials from skill resources before importing")
        values[name] = data
    for name in seen:
        if any(
            str(parent).casefold() in seen
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            raise invalid("Resource file and directory names collide")
    roots = [name for name in values if PurePosixPath(name).name == "SKILL.md"]
    if len(roots) != 1:
        raise invalid("Import one skill directory containing exactly one SKILL.md")
    parent = str(PurePosixPath(roots[0]).parent)
    prefix = "" if parent == "." else parent + "/"
    if any(not name.startswith(prefix) for name in values):
        raise invalid("Select the skill directory itself, including its bundled resources")
    return {name[len(prefix) :]: data for name, data in values.items()}


def package_data(files, stages, source, builtin=False):
    raw = files["SKILL.md"]
    if len(raw) > 80_000:
        raise invalid("SKILL.md exceeds 80000 bytes")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        pieces = text.split("\n---\n", 1)
        if not text.startswith("---\n") or len(pieces) != 2 or len(pieces[0]) > 16_000:
            raise ValueError("Missing bounded YAML frontmatter")
        metadata = yaml.safe_load(pieces[0][4:])
        if not isinstance(metadata, dict):
            raise ValueError("Frontmatter must be a mapping")
        name, description = metadata.get("name"), metadata.get("description")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or len(name) > 64
        ):
            raise ValueError("Use a lowercase hyphenated skill name up to64characters")
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
            raise ValueError("Provide a description up to1024characters")
        extra = metadata.get("metadata") or {}
        if not isinstance(extra, dict):
            raise ValueError("metadata must be a mapping")
        version = extra.get("version", "unversioned")
        if not isinstance(version, (str, int, float)):
            raise ValueError("Version must be plain text")
        license_name = metadata.get("license", "")
        compatibility = metadata.get("compatibility", "")
        if not isinstance(license_name, str) or not isinstance(compatibility, str):
            raise ValueError("License and compatibility must be plain text")
        if len(license_name) > 2000 or len(compatibility) > 500 or len(str(version)) > 100:
            raise ValueError("Skill metadata exceeds supported bounds")
        instructions = pieces[1].strip()
        if not instructions:
            raise ValueError("Provide skill instructions after the frontmatter")
    except (UnicodeError, ValueError, yaml.YAMLError, RecursionError) as error:
        raise invalid("Invalid SKILL.md frontmatter or instructions") from error
    inventory = [
        {
            "path": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        }
        for name, data in sorted(files.items())
    ]
    identity = [{"path": item["path"], "sha256": item["sha256"]} for item in inventory]
    identifier = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "id": identifier,
        "name": name,
        "description": description.strip(),
        "version": str(version),
        "license": license_name,
        "compatibility": compatibility,
        "stages": list(dict.fromkeys(stages)),
        "source": source,
        "builtin": builtin,
        "instructions": instructions,
        "resources": inventory,
        "scripts": [
            item
            for item in inventory
            if item["path"].startswith("scripts/")
            and Path(item["path"]).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ps1", ".sh"}
        ],
        "files": {name: base64.b64encode(data).decode() for name, data in files.items()},
    }


@lru_cache(maxsize=1)
def builtins():
    result = {}
    for directory in sorted((Path(__file__).parent / "builtin_skills").iterdir()):
        if directory.is_dir():
            package = package_data(
                {"SKILL.md": (directory / "SKILL.md").read_bytes()}, [directory.name], "AFDS", True
            )
            result[package["id"]] = package
    return result


def public_package(package, detail=True):
    excluded = {"files"}
    if not detail:
        excluded |= {"instructions", "resources"}
    return {key: value for key, value in package.items() if key not in excluded}


class SkillLibrary:
    def __init__(self, database):
        self.database = database

    def list(self, stage=None, offset=0, limit=50):
        defaults = [
            public_package(value, False)
            for value in builtins().values()
            if stage is None or stage in value["stages"]
        ]
        defaults.sort(key=lambda value: (value["name"], value["id"]))
        conditions = []
        if stage:
            stages = func.json_each(db.skill_packages.c.summary["stages"]).table_valued("value")
            conditions.append(select(stages.c.value).where(stages.c.value == stage).exists())
        values = defaults[offset : offset + limit]
        with self.database.engine.connect() as connection:
            total = connection.execute(
                select(func.count()).select_from(db.skill_packages).where(*conditions)
            ).scalar_one()
            if len(values) < limit:
                values.extend(
                    connection.execute(
                        select(db.skill_packages.c.summary)
                        .where(*conditions)
                        .order_by(
                            db.skill_packages.c.summary["name"].as_string(), db.skill_packages.c.id
                        )
                        .offset(max(0, offset - len(defaults)))
                        .limit(limit - len(values))
                    ).scalars()
                )
        return {
            "stages": STAGES,
            "items": values,
            "total": len(defaults) + total,
            "offset": offset,
        }

    def get(self, identifier):
        if identifier in builtins():
            return builtins()[identifier]
        with self.database.engine.connect() as connection:
            value = connection.execute(
                select(db.skill_packages.c.payload).where(db.skill_packages.c.id == identifier)
            ).scalar_one_or_none()
        if value is None:
            raise DomainError(
                "SKILL_NOT_FOUND", "Reimport the selected immutable skill package", 404
            )
        return value

    def import_files(self, files, stages, source=""):
        package = package_data(decode_files(files), stages, source)
        with self.database.transaction() as connection:
            if not connection.execute(
                select(db.skill_packages.c.id).where(db.skill_packages.c.id == package["id"])
            ).first():
                connection.execute(
                    insert(db.skill_packages).values(
                        id=package["id"],
                        created_at=time.time(),
                        summary=public_package(package, False),
                        payload=package,
                    )
                )
        return public_package(self.get(package["id"]))

    def import_zip(self, content, stages, source=""):
        if not content or len(content) > MAX_UPLOAD:
            raise invalid("ZIP exceeds16MiB")
        root = self.database.settings.data_dir / "uploads" / "skills"
        root.mkdir(parents=True, exist_ok=True)
        files = []
        try:
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                path = Path(temporary) / "skill.zip"
                path.write_bytes(content)
                if inspect_directory(path) > MAX_FILES:
                    raise invalid("ZIP exceeds256members")
                with zipfile.ZipFile(path) as archive:
                    total = 0
                    for item in archive.infolist():
                        safe_name(item.filename.rstrip("/"))
                        if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                            raise invalid("Encrypted or linked ZIP resources are unsupported")
                        total += item.file_size
                        if item.file_size > MAX_FILE or total > MAX_UNPACKED:
                            raise invalid("Unpacked ZIP exceeds supported limits")
                        if not item.is_dir():
                            with archive.open(item) as handle:
                                data = handle.read(MAX_FILE + 1)
                            if len(data) > MAX_FILE:
                                raise invalid("ZIP member exceeds8MiB")
                            files.append(
                                SkillFile(
                                    path=item.filename,
                                    content_base64=base64.b64encode(data).decode(),
                                )
                            )
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
            raise invalid("Cannot read skill ZIP") from error
        return self.import_files(files, stages, source)

    def resource(self, identifier, path):
        package = self.get(identifier)
        name = safe_name(path)
        if name not in package["files"]:
            raise DomainError("NOT_FOUND", "Skill resource not found", 404)
        return base64.b64decode(package["files"][name])

    def snapshot(self, selection):
        package = self.get(selection.package_id)
        resources = []
        for name in selection.resources:
            if name.startswith("scripts/") or name == "SKILL.md":
                raise invalid("Select supporting text references for model context")
            try:
                text = self.resource(package["id"], name).decode("utf-8-sig")
            except UnicodeError as error:
                raise invalid("Selected context resource must be UTF-8 text") from error
            resources.append({"path": name, "text": text})
        try:
            return GuidanceSnapshot.model_validate(
                {
                    **{
                        key: package[key]
                        for key in ("name", "description", "version", "source", "instructions")
                    },
                    "package_id": package["id"],
                    "resources": resources,
                }
            ).model_dump(mode="json")
        except ValidationError as error:
            raise invalid(
                "Select guidance up to16000characters and references up to12000 each, 24000 total"
            ) from error
