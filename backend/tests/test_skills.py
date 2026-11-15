import base64
import io
import zipfile

import pytest
from conftest import storyboard

GUIDE = """---
name: external-camera
description: Design deliberate camera movement for dialogue scenes.
metadata:
  version: "1.2"
license: MIT
---
# Dialogue framing
Preserve screen direction. Establish the eyeline before using a close-up.
See references/framing.md.
"""


def package_files(extra=None):
    values = {
        "SKILL.md": GUIDE,
        "references/framing.md": "DEMO reference. Keep a stable action axis.",
        "scripts/inspect.py": "import json, sys\nprint(json.dumps({'frames': 48}))\n",
        **(extra or {}),
    }
    return [
        {"path": name, "content_base64": base64.b64encode(text.encode()).decode()}
        for name, text in values.items()
    ]


def import_package(client, files=None):
    return client.post(
        "/api/v1/skills/import",
        json={"files": files or package_files(), "stages": ["shots"], "source": "DEMO external"},
    )


def test_stage_library_import_is_immutable_and_resources_are_inspectable(client):
    response = client.get("/api/v1/skills")
    assert response.status_code == 200, response.text
    assert len(response.json()["stages"]) == 9
    assert len(response.json()["items"]) == 9
    first = import_package(client)
    assert first.status_code == 201, first.text
    skill = first.json()
    assert skill["name"] == "external-camera"
    assert skill["version"] == "1.2"
    assert skill["license"] == "MIT"
    assert skill["stages"] == ["shots"]
    assert skill["scripts"][0]["path"] == "scripts/inspect.py"
    assert import_package(client).json()["id"] == skill["id"]
    assert len(client.get("/api/v1/skills?stage=shots").json()["items"]) == 2
    remapped = client.post(
        "/api/v1/skills/import", json={"files": package_files(), "stages": ["image"]}
    )
    assert remapped.json()["id"] == skill["id"]
    assert set(remapped.json()["stages"]) == {"shots", "image"}
    resource = client.get(
        f"/api/v1/skills/{skill['id']}/resource", params={"path": "references/framing.md"}
    )
    assert resource.status_code == 200
    assert "stable action axis" in resource.text


@pytest.mark.parametrize("path", ["../escape.py", "C:/escape.py", "scripts/CON.py", ".env"])
def test_import_rejects_unsafe_resource_names(client, path):
    response = import_package(client, package_files({path: "invalid"}))
    assert response.status_code == 422, response.text
    assert len(client.get("/api/v1/skills").json()["items"]) == 9


def test_zip_import_accepts_one_root_and_rejects_case_collisions(client):
    def archive(files):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as out:
            for item in files:
                out.writestr(
                    "external-camera/" + item["path"], base64.b64decode(item["content_base64"])
                )
        return data.getvalue()

    response = client.post(
        "/api/v1/skills/import-zip?stages=shots",
        content=archive(package_files()),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/skills/import-zip?stages=shots",
        content=archive(package_files({"skill.md": "collision"})),
    )
    assert response.status_code == 422, response.text


def test_guidance_preview_commit_is_source_pinned_and_preserves_existing_project(client):
    apr = storyboard(client)
    skill = import_package(client).json()
    root = f"/api/v1/projects/{apr['project_id']}"
    body = {
        "base_revision": apr["revision"],
        "selections": {
            "shots": [{"package_id": skill["id"], "resources": ["references/framing.md"]}]
        },
    }
    preview = client.post(f"{root}/skills/guidance/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert client.get(f"{root}/apr").json()["revision"] == apr["revision"]
    committed = client.post(f"{root}/skills/guidance/commit", json={**body, "approved": True})
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["shots"] == apr["shots"]
    assert result["production_guidance"]["shots"][0]["package_id"] == skill["id"]
    assert "stable action axis" in result["production_guidance"]["shots"][0]["resources"][0]["text"]
    assert (
        client.post(f"{root}/skills/guidance/commit", json={**body, "approved": True}).status_code
        == 409
    )
