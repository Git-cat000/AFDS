"""Offline system snapshots. Operator must stop every API and Worker first."""

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, inspect, text
from sqlalchemy.engine import make_url

from app import db
from app.deployment import migration_head
from app.errors import DomainError
from app.storage import file_hash

STORE_ROOTS = {"projects", "uploads", "skills", "state"}


class FileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or ":" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Snapshot paths must be canonical relative paths")
        return value


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    dialect: Literal["sqlite", "postgresql"]
    migration: str = Field(max_length=32)
    database: FileRecord
    files: list[FileRecord] = Field(max_length=100000)


def safe_file(root, relative):
    if Path(root).is_symlink():
        raise DomainError("BACKUP_PATH_INVALID", "Snapshot roots cannot be symlinks", 422)
    root = Path(root).resolve()
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise DomainError("BACKUP_PATH_INVALID", "Snapshot paths cannot contain symlinks", 422)
    if not current.resolve().is_relative_to(root):
        raise DomainError("BACKUP_PATH_INVALID", "Snapshot path escaped its root", 422)
    return current


def record(path, relative):
    return FileRecord(path=relative, sha256=file_hash(path), size=path.stat().st_size)


def pg_environment(address):
    url = make_url(address)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update(
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGDATABASE=url.database or "afds",
        PGUSER=url.username or "afds",
    )
    if url.password is not None:
        environment["PGPASSWORD"] = url.password
    for option in ("sslmode", "sslrootcert", "sslcert", "sslkey", "options"):
        if option in url.query:
            environment["PG" + option.upper()] = str(url.query[option])
    return environment


def postgres_tool(command, address):
    try:
        subprocess.run(
            command, env=pg_environment(address), check=True, capture_output=True, timeout=3600
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DomainError(
            "BACKUP_DATABASE_FAILED",
            "PostgreSQL snapshot tool failed; check installed client version and connectivity",
            503,
        ) from error


def require_stopped(stopped):
    if not stopped:
        raise DomainError(
            "BACKUP_STOP_REQUIRED", "Stop API and all Workers before system backup/restore", 409
        )


def backup_system(database, destination, *, stopped=False):
    require_stopped(stopped)
    root = database.settings.data_dir.resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination) or destination.exists():
        raise DomainError(
            "BACKUP_DESTINATION_INVALID", "Use a new snapshot directory outside the store", 409
        )
    with database.engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if version != migration_head():
        raise DomainError(
            "BACKUP_SCHEMA_MISMATCH", "Use the application version matching the database", 409
        )
    destination.mkdir(parents=True, mode=0o700)
    dialect = database.engine.dialect.name
    dump = destination / ("database.sqlite" if dialect == "sqlite" else "database.dump")
    if dialect == "sqlite":
        with sqlite3.connect(make_url(database.settings.database_url).database) as source:
            with sqlite3.connect(dump) as target:
                source.backup(target)
    else:
        postgres_tool(
            ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)],
            database.settings.database_url,
        )
    files = []
    for name in sorted(STORE_ROOTS):
        directory = root / name
        if directory.is_symlink():
            raise DomainError("BACKUP_PATH_INVALID", "Snapshot store roots cannot be symlinks", 422)
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(root).as_posix()
            safe_file(root, relative)
            if path.is_file():
                output = destination / "store" / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output)
                files.append(record(output, relative))
                if len(files) > 100000:
                    raise DomainError("BACKUP_TOO_LARGE", "Snapshot exceeds the file limit", 422)
    manifest = Snapshot(
        dialect=dialect, migration=version, database=record(dump, dump.name), files=files
    )
    payload = manifest.model_dump(mode="json")
    (destination / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def restore_system(settings, source, *, stopped=False):
    require_stopped(stopped)
    source = Path(source).resolve()
    try:
        manifest_path = safe_file(source, "manifest.json")
        if manifest_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Manifest too large")
        manifest = Snapshot.model_validate_json(manifest_path.read_bytes())
        identities = [item.path.casefold() for item in manifest.files]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate snapshot paths")
        for item in manifest.files:
            if PurePosixPath(item.path).parts[0] not in STORE_ROOTS:
                raise ValueError("Unexpected store root")
        records = [(source, manifest.database)] + [
            (source / "store", item) for item in manifest.files
        ]
        for root, item in records:
            path = safe_file(root, item.path)
            if (
                not path.is_file()
                or path.stat().st_size != item.size
                or file_hash(path) != item.sha256
            ):
                raise ValueError("Snapshot contents differ")
    except (OSError, ValidationError, ValueError) as error:
        raise DomainError(
            "BACKUP_INVALID", "Snapshot manifest or file hash is invalid", 422
        ) from error
    dialect = "sqlite" if settings.database_url.startswith("sqlite:") else "postgresql"
    if manifest.dialect != dialect or manifest.migration != migration_head():
        raise DomainError(
            "BACKUP_SCHEMA_MISMATCH",
            "Restore with matching database dialect and application version",
            409,
        )
    target = settings.data_dir.resolve()
    if target.exists() and any(target.iterdir()):
        raise DomainError("RESTORE_NOT_EMPTY", "Restore into a new empty data directory", 409)
    from app.db import Database

    if dialect == "postgresql":
        check = Database(settings)
        try:
            if inspect(check.engine).get_table_names():
                raise DomainError("RESTORE_NOT_EMPTY", "Restore into an empty database schema", 409)
        finally:
            check.engine.dispose()
    else:
        database_path = Path(make_url(settings.database_url).database).resolve()
        if database_path.exists() or not database_path.is_relative_to(target):
            raise DomainError(
                "RESTORE_NOT_EMPTY", "Use a new SQLite database within the restored store", 409
            )
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = target / ".restore-incomplete"
    marker.write_text("Restore incomplete; do not start services", encoding="utf-8")
    for item in manifest.files:
        output = safe_file(target, item.path)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_file(source / "store", item.path), output)
    dump = safe_file(source, manifest.database.path)
    if dialect == "sqlite":
        database_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dump, database_path)
    else:
        postgres_tool(
            [
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                make_url(settings.database_url).database,
                str(dump),
            ],
            settings.database_url,
        )
    restored = Database(settings)
    try:
        with restored.transaction() as connection:
            connection.execute(delete(db.sessions))
    finally:
        restored.engine.dispose()
    marker.unlink()
