import json

from sqlalchemy import insert

from app import db
from app.execution.queue import Queue
from app.project_engine import ProjectEngine
from app.schemas import ProjectCreate, new_id


def seed(client, count=55):
    database = client.app.state.database
    apr = ProjectEngine(database).create(ProjectCreate(title="DEMO history"))
    with database.transaction() as connection:
        for index in range(count):
            Queue(database).enqueue(
                connection,
                apr,
                "generate_video",
                {
                    "number": index,
                    "snapshot": "PRIVATE" * 12000,
                    "provider_id": "mock-video-local",
                },
            )
    return database, apr


def test_job_pages_are_bounded_and_complete_with_stable_cursors(client):
    _, apr = seed(client)
    path = f"/api/v1/projects/{apr['project_id']}/jobs/page"
    collected, cursor = [], None
    while True:
        response = client.get(path, params={"limit": 11, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 55 and page["active_count"] == 55
        assert len(page["items"]) <= 11 and len(response.content) < 20000
        assert "PRIVATE" not in response.text
        assert all("request" not in item for item in page["items"])
        collected.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(collected) == len(set(collected)) == 55
    detail = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{collected[0]}")
    assert "PRIVATE" in detail.text


def test_history_cursor_scope_and_input_validation(client):
    _, apr = seed(client, 3)
    path = f"/api/v1/projects/{apr['project_id']}/jobs/page"
    response = client.get(path, params={"limit": 1})
    assert response.status_code == 200
    cursor = response.json()["next_cursor"]
    assert client.get(path, params={"cursor": "not-valid"}).status_code == 422
    assert client.get(path, params={"limit": 101}).status_code == 422
    assert client.get(path, params={"cursor": cursor, "state": "failed"}).status_code == 422
    foreign = ProjectEngine(client.app.state.database).create(ProjectCreate(title="Other"))
    assert (
        client.get(
            f"/api/v1/projects/{foreign['project_id']}/jobs/page", params={"cursor": cursor}
        ).status_code
        == 422
    )
    assert client.get(f"/api/v1/projects/{new_id()}/jobs/page").status_code == 404


def test_budget_and_revision_pages_preserve_totals_and_details(client):
    database, apr = seed(client, 0)
    rows = [
        {
            "id": new_id(),
            "project_id": apr["project_id"],
            "reference": f"demo:{i}",
            "payload": {
                "created_at": 10,
                "state": "pending",
                "currency": "USD",
                "estimate_micros": 3,
                "actual_micros": None,
                "provider_id": "demo",
                "private": "PRIVATE",
            },
        }
        for i in range(30)
    ]
    with database.transaction() as connection:
        connection.execute(insert(db.cost_entries), rows)
    root = f"/api/v1/projects/{apr['project_id']}"
    summary = client.get(root + "/budget/summary")
    assert summary.status_code == 200
    assert summary.json()["reserved"] == "0.000090" and "entries" not in summary.json()
    page = client.get(root + "/budget/entries", params={"limit": 10}).json()
    assert len(page["items"]) == 10 and page["total"] == 30
    assert page["items"][0]["estimate"] == "0.000003"
    assert page["items"][0]["can_reconcile"]
    assert "PRIVATE" not in json.dumps(page)
    for index in range(5):
        apr = ProjectEngine(database).mutate(
            apr["project_id"],
            apr["revision"],
            f"Edit {index}",
            lambda value, connection: value.update(brief="DEMO"),
        )
    first = client.get(root + "/revisions/page", params={"limit": 2}).json()
    second = client.get(
        root + "/revisions/page", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert first["total"] == 6 and len(first["items"]) == 2
    assert first["items"][-1]["revision"] > second["items"][0]["revision"]


def test_activity_bootstraps_at_recent_history_and_catches_up_in_bounded_batches(client):
    database, apr = seed(client, 0)
    with database.transaction() as connection:
        connection.execute(
            insert(db.events),
            [
                {"project_id": apr["project_id"], "type": "demo", "timestamp": i, "payload": {}}
                for i in range(500)
            ],
        )
    path = f"/api/v1/projects/{apr['project_id']}/activity"
    page = client.get(path).json()
    assert len(page["items"]) == 200 and not page["has_more"]
    assert page["items"][0]["event_id"] > 200
    cursor = page["cursor"]
    with database.transaction() as connection:
        connection.execute(
            insert(db.events),
            [
                {
                    "project_id": apr["project_id"],
                    "type": "job.completed",
                    "timestamp": i,
                    "payload": {},
                }
                for i in range(250)
            ],
        )
    newer = client.get(path, params={"after": cursor}).json()
    assert len(newer["items"]) == 200 and newer["has_more"]
    last = client.get(path, params={"after": newer["cursor"]}).json()
    assert len(last["items"]) == 50 and not last["has_more"]
