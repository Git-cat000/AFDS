import hashlib
import io
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from test_exchange import demo_media, export_project, run_export  # noqa: F401
from test_restore import destination, job_state, upload  # noqa: F401

from app.execution.worker import Worker
from app.main import create_app


@pytest.fixture
def source_data(request):
    return request.getfixturevalue("export_project")


@pytest.fixture
def destination_data(request):
    return request.getfixturevalue("destination")


def save_workspace(client, apr, revision=0, text="DEMO portable note"):
    shot = apr["shots"][0]["id"]
    image = next(asset["id"] for asset in apr["assets"] if asset["media_type"] == "image")
    document = {
        "version": 2,
        "positions": {shot: {"x": -40, "y": 50}, "note-demo": {"x": 2, "y": 3}},
        "nodes": [
            {"id": "note-demo", "kind": "note", "text": text},
            {"id": "asset-demo", "kind": "asset", "assetId": image},
        ],
        "edges": [
            {"id": "line-demo", "source": shot, "target": "asset-demo", "label": "DEMO reference"}
        ],
        "hiddenShots": [],
    }
    response = client.put(
        f"/api/v1/projects/{apr['project_id']}/workspace",
        json={
            "base_workspace_revision": revision,
            "document": document,
        },
    )
    assert response.status_code == 200, response.text
    return document


def enqueue_export(client, apr):
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/exports",
        json={
            "base_revision": apr["revision"],
            "format": "zip",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def download_export(client, database, job):
    assert Worker(database).run_once()
    assert job_state(database, job)["state"] == "completed"
    response = client.get(f"/api/v1/projects/{job['project_id']}/exports/{job['id']}/file")
    assert response.status_code == 200, response.text
    return response.content


def test_workspace_restore_remaps_references(source_data, destination_data):
    source, apr = source_data
    with TestClient(create_app(source.settings)) as client:
        original = save_workspace(client, apr)
        archive = download_export(client, source, enqueue_export(client, apr))
    with ZipFile(io.BytesIO(archive)) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["export_version"] == "0.3.0"
        assert manifest["workspace_revision"] == 1
        assert (
            manifest["workspace_sha256"]
            == hashlib.sha256(zipped.read("workspace.json")).hexdigest()
        )
    target, database = destination_data
    restore = upload(target, archive).json()
    assert Worker(database).run_once()
    state = job_state(database, restore)
    assert state["state"] == "completed", state.get("error")
    restored = target.get(f"/api/v1/projects/{restore['project_id']}/apr").json()
    workspace = target.get(f"/api/v1/projects/{restore['project_id']}/workspace").json()
    assert workspace["workspace_revision"] == 1
    assert workspace["document"]["nodes"][0] == original["nodes"][0]
    shot = restored["shots"][0]["id"]
    assert workspace["document"]["positions"][shot] == {"x": -40, "y": 50}
    assert workspace["document"]["edges"][0]["source"] == shot
    asset = workspace["document"]["nodes"][1]["assetId"]
    assert asset != original["nodes"][1]["assetId"]
    assert asset in {item["id"] for item in restored["assets"]}


@pytest.mark.parametrize("case", ["hash", "foreign", "missing", "limit"])
def test_workspace_manifest_tamper_rejected(source_data, destination_data, case):
    source, apr = source_data
    with TestClient(create_app(source.settings)) as client:
        save_workspace(client, apr)
        archive = download_export(client, source, enqueue_export(client, apr))
    with ZipFile(io.BytesIO(archive)) as zipped:
        files = {name: zipped.read(name) for name in zipped.namelist()}
    assert "workspace.json" in files
    document = json.loads(files["workspace.json"])
    if case == "missing":
        del files["workspace.json"]
    else:
        if case == "foreign":
            document["nodes"][1]["assetId"] = str(uuid4())
        elif case == "limit":
            document["nodes"] = [
                {"id": f"note-{i}", "kind": "note", "text": "x"} for i in range(1001)
            ]
        else:
            document["nodes"][0]["text"] = "changed"
        files["workspace.json"] = json.dumps(document).encode()
        if case != "hash":
            manifest = json.loads(files["manifest.json"])
            manifest["workspace_sha256"] = hashlib.sha256(files["workspace.json"]).hexdigest()
            files["manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with ZipFile(output, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    target, database = destination_data
    restore = upload(target, output.getvalue()).json()
    assert Worker(database).run_once()
    assert job_state(database, restore)["error"]["code"] == "RESTORE_INVALID_ARCHIVE"
    assert (
        target.get(f"/api/v1/projects/{restore['project_id']}/workspace").json()["initialized"]
        is False
    )


def test_export_reads_one_database_snapshot(source_data):
    database, apr = source_data
    with TestClient(create_app(database.settings)) as client:
        first = save_workspace(client, apr)
        job = enqueue_export(client, apr)
        save_workspace(client, apr, revision=1, text="later edit")
        archive = download_export(client, database, job)
    with ZipFile(io.BytesIO(archive)) as zipped:
        assert json.loads(zipped.read("workspace.json")) == first
        assert json.loads(zipped.read("manifest.json"))["workspace_revision"] == 1
        assert json.loads(zipped.read("project.apr.json"))["revision"] == apr["revision"]


def test_legacy_archive_restores_with_empty_workspace(source_data, destination_data):
    source, apr = source_data
    archive = run_export(source, apr)["path"].read_bytes()
    target, database = destination_data
    restore = upload(target, archive).json()
    assert Worker(database).run_once()
    assert job_state(database, restore)["state"] == "completed"
    assert (
        target.get(f"/api/v1/projects/{restore['project_id']}/workspace").json()["initialized"]
        is False
    )
