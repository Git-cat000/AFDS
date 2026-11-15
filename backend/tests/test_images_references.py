import io

import pytest
from conftest import storyboard
from PIL import Image

from app.execution.worker import Worker
from app.schemas import new_id


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (160, 90), "#224466").save(output, format="JPEG")
    return output.getvalue()


def upload_image(client, apr, content=None, **params):
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/images/import",
        params={
            "base_revision": apr["revision"],
            "name": "DEMO reference",
            "rights_confirmed": True,
            **params,
        },
        content=image_bytes() if content is None else content,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_image_import_is_worker_owned_canonical_and_requires_rights(client):
    apr = storyboard(client)
    assert upload_image(client, apr, rights_confirmed=False).status_code == 422
    response = upload_image(client, apr)
    assert response.status_code == 202, response.text
    assert "staging_path" not in response.text
    assert Worker(client.app.state.database).run_once()
    job = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{response.json()['id']}").json()
    assert job["state"] == "completed", job.get("error")
    fresh = client.get(f"/api/v1/projects/{apr['project_id']}/apr").json()
    asset = next(asset for asset in fresh["assets"] if asset["id"] == job["output_asset_ids"][0])
    assert asset["media_type"] == "image" and asset["codec"] == "png"
    assert asset["width"] == 160 and asset["height"] == 90
    data = client.get(f"/api/v1/projects/{apr['project_id']}/assets/{asset['id']}/file").content
    assert data.startswith(b"\x89PNG")
    assert asset["provenance"]["rights_confirmed"]


@pytest.mark.parametrize("content", [b"not an image", b"<svg>DEMO</svg>"])
def test_invalid_reference_upload_never_publishes(client, content):
    apr = storyboard(client)
    response = upload_image(client, apr, content)
    assert response.status_code == 202
    Worker(client.app.state.database).run_once()
    job = client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{response.json()['id']}").json()
    assert job["error"]["code"] == "IMAGE_INVALID"
    assert client.get(f"/api/v1/projects/{apr['project_id']}/apr").json()["assets"] == []


def test_shot_reference_roles_validate_owned_images_and_clear_on_structure_import(client):
    apr = storyboard(client)
    job = upload_image(client, apr)
    assert job.status_code == 202
    Worker(client.app.state.database).run_once()
    root = f"/api/v1/projects/{apr['project_id']}"
    fresh = client.get(root + "/apr").json()
    asset_id = fresh["assets"][0]["id"]
    shot_id = fresh["shots"][0]["id"]
    response = client.patch(
        root + f"/shots/{shot_id}",
        json={
            "base_revision": fresh["revision"],
            "approved": True,
            "patch": {"reference_images": {"reference_image": asset_id, "start_image": asset_id}},
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["apr_schema_version"] == "0.4.0"
    assert updated["shots"][0]["reference_images"]["start_image"] == asset_id
    invalid = client.patch(
        root + f"/shots/{shot_id}",
        json={
            "base_revision": updated["revision"],
            "patch": {"reference_images": {"end_image": new_id()}},
        },
    )
    assert invalid.status_code == 422
    imported = client.post("/api/v1/projects/import", json=updated)
    assert imported.status_code == 201, imported.text
    assert imported.json()["shots"][0]["reference_images"] == {}
    generated = client.post(
        root + "/generate",
        json={
            "base_revision": updated["revision"],
            "shot_ids": [shot_id],
            "provider_id": "mock-video-local",
        },
    )
    assert generated.status_code == 422
    assert generated.json()["error"]["code"] == "REFERENCE_INPUT_UNSUPPORTED"
