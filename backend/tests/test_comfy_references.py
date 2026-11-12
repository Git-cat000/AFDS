import pytest
from conftest import storyboard
from test_comfy_contracts import comfy_server as comfy_server
from test_comfy_contracts import template
from test_images_references import upload_image
from test_provider_settings import save_provider

from app.execution.queue import shot_request
from app.execution.worker import Worker
from app.media import MockVideo


@pytest.mark.parametrize("bad_upload", [False, True])
def test_reference_images_use_binary_upload_and_checked_workflow_binding(
    client, comfy_server, tmp_path, bad_upload
):
    apr = storyboard(client)
    assert upload_image(client, apr).status_code == 202
    worker = Worker(client.app.state.database)
    worker.run_once()
    root = f"/api/v1/projects/{apr['project_id']}"
    fresh = client.get(root + "/apr").json()
    asset = fresh["assets"][0]
    changed = client.patch(
        root + f"/shots/{fresh['shots'][0]['id']}",
        json={
            "base_revision": fresh["revision"],
            "approved": True,
            "patch": {"reference_images": {"reference_image": asset["id"]}},
        },
    )
    assert changed.status_code == 200
    fresh = changed.json()
    workflow = template("video")
    workflow["graph"]["3"] = {"class_type": "Load", "inputs": {"image": "existing.png"}}
    workflow["bindings"]["reference_image"] = {"node_id": "3", "input_name": "image"}
    source = tmp_path / "DEMO.mp4"
    MockVideo(client.app.state.settings).generate(
        shot_request(fresh, fresh["shots"][0]), source, lambda: True
    )
    comfy_server.update(
        output=source.read_bytes(),
        output_field="gifs",
        filename="DEMO.mp4",
        bad_upload=bad_upload,
    )
    comfy_server["extra_info"] = {
        "Load": {"input": {"required": {"image": [["existing.png"], {"image_upload": True}]}}}
    }
    saved = save_provider(
        client,
        "video",
        base_url=comfy_server["url"],
        api_key="",
        allow_private_network=True,
        protocol="comfyui",
        workflow=workflow,
    )
    assert saved.status_code == 200, saved.text
    response = client.post(
        root + "/generate",
        json={
            "base_revision": fresh["revision"],
            "shot_ids": [fresh["shots"][0]["id"]],
            "provider_id": "openai-video",
            "allow_remote": True,
        },
    )
    assert response.status_code == 202, response.text
    worker.run_once()
    result = client.get(root + f"/jobs/{response.json()[0]['id']}").json()
    assert comfy_server["uploads"] and comfy_server["uploads"][0].startswith(b"\x89PNG")
    prompts = [row for row in comfy_server["requests"] if row[1] == "/prompt"]
    if bad_upload:
        assert result["error"]["code"] == "REFERENCE_UPLOAD_INVALID"
        assert prompts == []
    else:
        assert result["state"] == "completed", result.get("error")
        bound = prompts[0][2]["prompt"]["3"]["inputs"]["image"]
        assert bound.startswith(f"afds/{apr['project_id']}/") and bound.endswith(".png")
