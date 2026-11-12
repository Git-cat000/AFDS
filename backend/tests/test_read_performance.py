"""Regressions for bounded read paths and exact cost totals on long-lived projects."""

import time

import pytest
from sqlalchemy import insert, select, update

from app import db
from app.budget import BudgetService, money
from app.errors import DomainError
from app.execution.queue import Queue
from app.project_engine import ProjectEngine
from app.schemas import Asset, ProjectCreate, new_id


def charge(project_id, number, **changes):
    return {
        "id": new_id(),
        "project_id": project_id,
        "reference": f"synthetic:{project_id}:{number}",
        "payload": {
            "state": "settled",
            "estimate_micros": 1,
            "actual_micros": 999_999_999_999_999,
            "currency": "USD",
            "provider_id": "offline-fixture",
            "created_at": time.time(),
            **changes,
        },
    }


def test_project_reads_stream_exact_totals_without_building_public_ledger(client, monkeypatch):
    database = client.app.state.database
    engine = ProjectEngine(database)
    apr = engine.create(ProjectCreate(title="DEMO long-lived ledger"))
    foreign = engine.create(ProjectCreate(title="DEMO foreign ledger"))
    count = 10000
    rows = [charge(apr["project_id"], i) for i in range(count)]
    rows += [
        charge(apr["project_id"], count, state="pending", actual_micros=None, estimate_micros=3),
        charge(
            apr["project_id"], count + 1, state="reserved", actual_micros=None, estimate_micros=None
        ),
        charge(apr["project_id"], count + 2, state="released", actual_micros=None),
        charge(foreign["project_id"], 0, currency="EUR"),
    ]
    with database.transaction() as connection:
        connection.execute(insert(db.cost_entries), rows)

    def no_public_entries(*args):
        raise AssertionError("APR reads must not materialize the public ledger")

    monkeypatch.setattr(BudgetService, "entries", no_public_entries)
    restored = engine.read(apr["project_id"])
    expected = money(count * 999_999_999_999_999)
    assert restored["budget"]["spent"] == expected
    assert restored["budget"]["reserved"] == "0.000003"
    with database.engine.connect() as connection:
        totals = BudgetService(database).summary(connection, apr, include_entries=False)
    assert totals["spent"] == expected
    assert totals["unknown_count"] == 1 and totals["over_budget"]
    assert "entries" not in totals


def test_budget_totals_preserve_public_details_reservation_and_currency_guard(client, monkeypatch):
    database = client.app.state.database
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO budget projection"))
    service = BudgetService(database)
    with database.transaction() as connection:
        entry_id = service.reserve(
            connection, apr, "demo:unknown", {"provider_id": "offline-fixture"}
        )
        service.finalize(connection, entry_id, True)
    public = service.public(apr["project_id"])
    assert len(public["entries"]) == 1
    assert public["entries"][0]["id"] == entry_id
    assert public["entries"][0]["estimate"] is None
    assert public["entries"][0]["actual"] is None
    assert public["entries"][0]["can_reconcile"]
    with database.engine.connect() as connection:
        totals = service.summary(connection, apr, include_entries=False)
    assert totals == {key: value for key, value in public.items() if key != "entries"}
    monkeypatch.setattr(
        service, "entries", lambda *args: pytest.fail("Reservation read full ledger")
    )
    with database.transaction() as connection:
        service.reserve(connection, apr, "demo:another", {"provider_id": "offline-fixture"})
        connection.execute(insert(db.cost_entries), charge(apr["project_id"], 9, currency="EUR"))
    with database.engine.connect() as connection, pytest.raises(DomainError) as error:
        service.summary(connection, apr, include_entries=False)
    assert error.value.code == "BUDGET_CURRENCY_LOCKED"


def test_operations_uses_owned_metadata_and_counts_duplicate_paths_without_loading_apr(
    client, monkeypatch
):
    database = client.app.state.database
    engine = ProjectEngine(database)
    apr = engine.create(ProjectCreate(title="DEMO operations metadata"))
    foreign = engine.create(ProjectCreate(title="DEMO foreign metadata"))
    assets = []
    for index in range(20):
        project_id = apr["project_id"] if index < 19 else foreign["project_id"]
        payload = Asset(
            id=new_id(),
            sha256=f"{index // 2:064x}",
            media_type="image",
            size_bytes=1024,
            provenance={"prompt": "PRIVATE" * 2000},
            license="DEMO",
            source="DEMO",
            consent="DEMO",
        ).model_dump(mode="json")
        assets.append(
            {
                "id": payload["id"],
                "project_id": project_id,
                "path": f"projects/{project_id}/assets/{index // 2}.png",
                "payload": payload,
            }
        )
    with database.transaction() as connection:
        connection.execute(insert(db.assets), assets)
        Queue(database).enqueue(
            connection,
            apr,
            "review_shot",
            {
                "asset_id": assets[0]["id"],
                "neighbors": [{"asset_id": assets[-1]["id"]}],
            },
        )

    def no_apr_load(*args):
        raise AssertionError("Operations must not load all APR media/provenance")

    monkeypatch.setattr(ProjectEngine, "load", no_apr_load)
    response = client.get(f"/api/v1/projects/{apr['project_id']}/operations")
    assert response.status_code == 200
    data = response.json()
    assert data["media"] == {"asset_count": 19, "unique_files": 10, "bytes": 10240}
    assert data["budget"]["spent"] == "0.000000"
    nodes = {node["id"]: node for node in data["graph"]["nodes"]}
    assert nodes[f"asset:{assets[0]['id']}"]["label"] == "existing_media"
    assert nodes[f"asset:{assets[0]['id']}"]["media_type"] == "image"
    assert nodes[f"asset:{assets[-1]['id']}"]["label"] == "unavailable_media"
    assert "PRIVATE" not in response.text
    assert client.get(f"/api/v1/projects/{new_id()}/operations").status_code == 404
    with database.transaction() as connection:
        connection.execute(
            update(db.projects).where(db.projects.c.id == apr["project_id"]).values(archived=True)
        )
    assert client.get(f"/api/v1/projects/{apr['project_id']}/operations").status_code == 404


def test_operations_normalizes_budget_defaults_for_legacy_project_payload(client):
    database = client.app.state.database
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO legacy budget defaults"))
    with database.transaction() as connection:
        payload = connection.execute(
            select(db.projects.c.payload).where(db.projects.c.id == apr["project_id"])
        ).scalar_one()
        payload.pop("budget")
        connection.execute(
            update(db.projects).where(db.projects.c.id == apr["project_id"]).values(payload=payload)
        )
    assert ProjectEngine(database).read(apr["project_id"])["budget"]["currency"] == "USD"
    response = client.get(f"/api/v1/projects/{apr['project_id']}/operations")
    assert response.status_code == 200
    assert response.json()["budget"]["currency"] == "USD"
    assert response.json()["budget"]["budget_limit"] == "0.000000"
