from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import project, storyboard


def document(shot_id):
    return {
        "version": 2,
        "positions": {shot_id: {"x": -35, "y": 90}, "note-test": {"x": 1, "y": 2}},
        "nodes": [{"id": "note-test", "kind": "note", "text": "制作便签"}],
        "edges": [{"id": "edge-1", "source": shot_id, "target": "note-test", "label": "说明"}],
        "hiddenShots": [],
    }


def test_workspace_save_keeps_apr_revision(client):
    apr = storyboard(client)
    path = f"/api/v1/projects/{apr['project_id']}/workspace"
    initial = client.get(path)
    assert initial.status_code == 200
    assert initial.json()["workspace_revision"] == 0
    assert initial.json()["initialized"] is False
    body = document(apr["shots"][0]["id"])
    saved = client.put(path, json={"base_workspace_revision": 0, "document": body})
    assert saved.status_code == 200, saved.text
    assert saved.json()["document"] == body
    assert saved.json()["workspace_revision"] == 1
    assert saved.json()["initialized"] is True
    assert client.get(path).json() == saved.json()
    after = client.get(f"/api/v1/projects/{apr['project_id']}").json()
    assert after == apr


def test_concurrent_workspace_save_conflicts(client):
    apr = storyboard(client)
    path = f"/api/v1/projects/{apr['project_id']}/workspace"
    body = {"base_workspace_revision": 0, "document": document(apr["shots"][0]["id"])}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.put(path, json=body), range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    conflict = next(result for result in results if result.status_code == 409)
    assert conflict.json()["error"]["code"] == "WORKSPACE_CONFLICT"
    assert client.get(path).json()["workspace_revision"] == 1


@pytest.mark.parametrize("case", ["foreign", "dangling", "nonfinite", "prototype", "limit"])
def test_workspace_rejects_invalid_references(client, case):
    apr = storyboard(client)
    body = document(apr["shots"][0]["id"])
    if case == "foreign":
        body["nodes"].append({"id": "asset-test", "kind": "asset", "assetId": str(uuid4())})
    elif case == "dangling":
        body["edges"][0]["target"] = "note-missing"
    elif case == "nonfinite":
        body["positions"]["note-test"]["x"] = "NaN"
    elif case == "prototype":
        body["positions"]["__proto__"] = {"x": 0, "y": 0}
    else:
        body["nodes"] = [
            {"id": f"note-{index}", "kind": "note", "text": "x"} for index in range(1001)
        ]
    path = f"/api/v1/projects/{apr['project_id']}/workspace"
    response = client.put(path, json={"base_workspace_revision": 0, "document": body})
    assert response.status_code == 422, response.text
    assert client.get(path).json()["workspace_revision"] == 0


def test_workspace_checks_project_and_persists_after_reopen(client):
    from fastapi.testclient import TestClient

    from app.main import create_app

    apr = project(client)
    path = f"/api/v1/projects/{apr['project_id']}/workspace"
    body = {"version": 2, "positions": {}, "nodes": [], "edges": [], "hiddenShots": []}
    assert (
        client.put(path, json={"base_workspace_revision": 0, "document": body}).status_code == 200
    )
    with TestClient(create_app(client.app.state.settings)) as reopened:
        assert reopened.get(path).json()["workspace_revision"] == 1
    assert client.get(f"/api/v1/projects/{uuid4()}/workspace").status_code == 404
