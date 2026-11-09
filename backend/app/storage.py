import hashlib
import shutil
from pathlib import Path

from sqlalchemy import insert, select

from app import db
from app.errors import DomainError
from app.schemas import new_id


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def contained(root, relative):
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise DomainError("INVALID_PATH", "Media path is outside the asset store", 403)
    return path


def asset_path(database, project_id, asset_id):
    with database.engine.connect() as connection:
        row = connection.execute(
            select(db.assets.c.path).where(
                db.assets.c.id == str(asset_id),
                db.assets.c.project_id == str(project_id),
            )
        ).first()
    if not row:
        raise DomainError("NOT_FOUND", "Asset not found in this project", 404)
    path = contained(database.settings.data_dir, row[0])
    if not path.is_file():
        raise DomainError("MISSING_ASSET", "Media file is missing from local storage", 404)
    return path


def import_asset(
    connection, settings, project_id, source, metadata, provenance, media_type="video"
):
    sha = file_hash(source)
    relative = Path("projects") / project_id / "assets" / sha[:2] / f"{sha}{source.suffix}"
    destination = contained(settings.data_dir, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_hash(destination) != sha:
            raise DomainError("HASH_MISMATCH", "Stored immutable asset is corrupt", 500)
    else:
        shutil.copyfile(source, destination)
    asset = {
        "id": new_id(),
        "sha256": sha,
        "media_type": media_type,
        "size_bytes": destination.stat().st_size,
        **metadata,
        "provenance": provenance,
        "license": provenance.get(
            "license", "generated-test-media" if provenance.get("is_mock") else "unverified"
        ),
        "source": provenance.get(
            "source", "local-mock" if provenance.get("is_mock") else provenance["provider_id"]
        ),
        "consent": provenance.get(
            "consent",
            "not_applicable" if provenance.get("is_mock") else "explicit_generation_approval",
        ),
    }
    connection.execute(
        insert(db.assets).values(
            id=asset["id"],
            project_id=project_id,
            path=relative.as_posix(),
            payload=asset,
        )
    )
    return asset
