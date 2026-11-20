"""Immutable model connection versions and legacy default compatibility."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy import update as sql_update

from app import db
from app.candidate_store import CandidateStore, CandidateUpdate, candidate_revisions, candidates
from app.errors import DomainError
from app.provider_settings import ProviderStore, ProviderUpdate


def update(**values):
    return CandidateUpdate.model_validate(
        {
            "label": "DEMO connection",
            "base_url": "https://example.com/v1",
            "model": "DEMO model",
            "enabled": True,
            "api_key": "DEMO-contract-only-key",
            "request_cost": "0.10",
            **values,
        }
    )


def test_candidate_versions_preserve_connection_and_secret(dialect_database):
    store = CandidateStore(dialect_database)
    first = store.save("demo-secondary", "llm", update())
    second = store.save(
        "demo-secondary",
        "llm",
        update(revision=first["revision"], model="DEMO model v2", api_key=None),
    )
    assert second["revision"] == 2
    assert second["key_configured"]
    assert "api_key" not in first and "secret" not in first
    assert store.runtime("demo-secondary", 1)["model"] == "DEMO model"
    assert store.runtime("demo-secondary", 2)["api_key"] == "DEMO-contract-only-key"
    with dialect_database.engine.connect() as connection:
        rows = connection.execute(select(candidate_revisions)).mappings().all()
    assert len(rows) == 2
    assert all("DEMO-contract-only-key" not in json.dumps(dict(row)) for row in rows)
    assert store.snapshot("demo-secondary", "storyboard")["connection_id"] == "demo-secondary"
    assert "api_key" not in store.snapshot("demo-secondary", "storyboard")


def test_candidate_revision_and_kind_are_fenced(dialect_database):
    store = CandidateStore(dialect_database)
    store.save("demo-secondary", "llm", update())
    with pytest.raises(DomainError) as caught:
        store.save("demo-secondary", "llm", update())
    assert caught.value.code == "CONFIG_CONFLICT"
    with pytest.raises(DomainError):
        store.save("demo-secondary", "video", update(revision=1))


@pytest.mark.parametrize("identity", ["auto", "mock-director", "mock-video-local", "openai-llm"])
def test_custom_connections_reject_reserved_route_identities(dialect_database, identity):
    with pytest.raises(DomainError):
        CandidateStore(dialect_database).save(identity, "llm", update())


def test_public_snapshot_does_not_decrypt_a_credential(dialect_database):
    store = CandidateStore(dialect_database)
    store.save("demo-secondary", "llm", update())
    with dialect_database.transaction() as connection:
        connection.execute(sql_update(candidates).values(secret="DEMO corrupt ciphertext"))
    assert store.snapshot("demo-secondary", "storyboard")["model"] == "DEMO model"
    with pytest.raises(DomainError):
        store.runtime("demo-secondary")


def test_legacy_default_is_mirrored_and_old_revisions_remain_usable(dialect_database):
    legacy = ProviderStore(dialect_database)
    profile = legacy.save(
        "llm",
        ProviderUpdate.model_validate(
            {
                "base_url": "https://example.com/v1",
                "model": "DEMO old",
                "enabled": True,
            }
        ),
    )
    store = CandidateStore(dialect_database)
    assert store.runtime("default-llm", profile["revision"])["model"] == "DEMO old"
    saved = store.save("default-llm", "llm", update(revision=profile["revision"]))
    assert legacy.runtime("llm")["model"] == saved["model"]
    legacy.save(
        "llm",
        ProviderUpdate.model_validate(
            {
                "revision": saved["revision"],
                "base_url": "https://example.com/v1",
                "model": "DEMO latest",
                "enabled": True,
            }
        ),
    )
    assert store.runtime("default-llm")["model"] == "DEMO latest"
    assert store.runtime("default-llm", profile["revision"])["model"] == "DEMO old"


def test_project_candidate_listing_redacts_admin_connection_details(dialect_database):
    store = CandidateStore(dialect_database)
    store.save("demo-secondary", "llm", update(all_projects=False, project_ids=[]))
    assert all(
        value.id != "demo-secondary"
        for value in store.candidates("00000000-0000-0000-0000-000000000001")
    )
    assert "base_url" in store.list()[0]
    descriptor = store.descriptor(store.list()[0])
    assert "base_url" not in descriptor.model_dump()
    assert "api_key" not in descriptor.model_dump()


def test_candidate_media_verification_does_not_mark_another_connection_healthy(dialect_database):
    store = CandidateStore(dialect_database)
    ProviderStore(dialect_database).save(
        "image", ProviderUpdate.model_validate({"model": "DEMO default"})
    )
    store.save("demo-secondary", "image", update())
    snapshot = store.snapshot("demo-secondary", "text_to_image")
    with dialect_database.transaction() as connection:
        ProviderStore(dialect_database).mark_media_verified(connection, snapshot, "image")
    profiles = {
        value.id: value for value in store.candidates("00000000-0000-0000-0000-000000000001")
    }
    assert profiles["demo-secondary"].health == "healthy"
    assert profiles["default-image"].health == "untested"


def test_default_migration_copies_all_immutable_legacy_versions(dialect_database):
    legacy = ProviderStore(dialect_database)
    legacy.save("image", ProviderUpdate.model_validate({"model": "DEMO one"}))
    legacy.save("image", ProviderUpdate.model_validate({"revision": 1, "model": "DEMO two"}))
    with dialect_database.engine.connect() as connection:
        assert connection.execute(
            select(db.provider_revisions.c.revision).where(
                db.provider_revisions.c.provider_id == "image"
            )
        ).scalars().all() == [1, 2]
    store = CandidateStore(dialect_database)
    assert store.runtime("default-image", 1)["model"] == "DEMO one"
    assert store.runtime("default-image", 2)["model"] == "DEMO two"
