"""ComfyUI prompt/history/view protocol with durable prompt IDs."""

import copy
import re
import time
from pathlib import PurePosixPath
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError

from app.comfy_schema import WorkflowTemplate
from app.media import Cancelled
from app.provider_http import ProviderError, ProviderHTTP


def validate_workflow(config):
    if not config.get("workflow"):
        raise ProviderError(
            "WORKFLOW_REQUIRED", "Import an API-format workflow first", rejected=True
        )
    template = WorkflowTemplate.model_validate(config["workflow"])
    info = ProviderHTTP(config).json("GET", "object_info", limit=16 * 1024 * 1024)
    for node in template.graph.values():
        if not isinstance(info.get(node["class_type"]), dict):
            raise ProviderError(
                "WORKFLOW_NODE_MISSING",
                "A workflow node type is unavailable on the server",
                rejected=True,
            )
    output = info[template.graph[template.output_node]["class_type"]]
    if output.get("output_node") is not True:
        raise ProviderError(
            "WORKFLOW_OUTPUT_INVALID", "Selected node is not a server output node", rejected=True
        )
    for binding in template.bindings.values():
        definition = info[template.graph[binding.node_id]["class_type"]].get("input", {})
        if not isinstance(definition, dict) or binding.input_name not in {
            **definition.get("required", {}),
            **definition.get("optional", {}),
        }:
            raise ProviderError(
                "WORKFLOW_INPUT_INVALID", "Mapped input is unavailable on the server", rejected=True
            )
    return {
        "models": [config["model"]],
        "capabilities": [],
        "provisional_capabilities": [f"text_to_{template.media_type}"],
        "protocol": "comfyui",
        "generation_tested": False,
        "message": "工作流节点与映射检查通过，尚未执行生成；输出媒体类型将在生成后验证。",
    }


def build_prompt(config, request):
    from app.remote_media import prompt_for_shot

    template = WorkflowTemplate.model_validate(config["workflow"])
    graph = copy.deepcopy(template.graph)
    values = {
        "prompt": request.get("prompt") or prompt_for_shot(request),
        "seed": template.seed,
        "width": request["format"]["width"],
        "height": request["format"]["height"],
        "frames": request.get("duration_frames", 1),
        "fps": request["format"]["fps_num"] / request["format"]["fps_den"],
    }
    for name, binding in template.bindings.items():
        graph[binding.node_id]["inputs"][binding.input_name] = values[name]
    return graph


def output_file(record, template):
    try:
        item = record["outputs"][template.output_node][template.output_field][0]
        name, folder = item["filename"], item.get("subfolder", "")
        if (
            not isinstance(name, str)
            or not isinstance(folder, str)
            or not name
            or len(name) > 300
            or len(folder) > 500
        ):
            raise ValueError()
        if (
            item.get("type") != "output"
            or "/" in name
            or "\\" in name
            or "\\" in folder
            or ":" in name + folder
        ):
            raise ValueError()
        if PurePosixPath(folder).is_absolute() or any(
            p in {".", ".."} for p in folder.split("/") if p
        ):
            raise ValueError()
        return {"filename": name, "subfolder": folder, "type": "output"}
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ProviderError(
            "PROVIDER_OUTPUT_INVALID", "Workflow returned invalid or missing output media"
        ) from error


def generate_comfy(transport, config, job, work, checkpoint, persist, settings):
    template = WorkflowTemplate.model_validate(config["workflow"])
    prompt_id = job.get("external_task_id")
    if not prompt_id:
        if job.get("submission_started"):
            raise ProviderError(
                "REMOTE_SUBMISSION_UNCERTAIN", "Restore the known ComfyUI prompt ID before retrying"
            )
        validate_workflow(config)
        persist(submission_started=True)
        result = transport.json(
            "POST",
            "prompt",
            payload={"prompt": build_prompt(config, job["request"]), "client_id": job["id"]},
            checkpoint=checkpoint,
        )
        prompt_id = result.get("prompt_id")
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", prompt_id):
            raise ProviderError(
                "PROVIDER_OUTPUT_INVALID", "ComfyUI did not return a valid prompt ID"
            )
        persist(external_task_id=prompt_id, remote_cancel_supported=False)
    deadline = time.monotonic() + config["video_timeout_seconds"]
    while time.monotonic() < deadline:
        if not checkpoint():
            raise Cancelled()
        history = transport.json(
            "GET", f"history/{quote(prompt_id, safe='')}", checkpoint=checkpoint
        )
        record = history.get(prompt_id)
        if record is not None:
            if not isinstance(record, dict) or not isinstance(record.get("status"), dict):
                raise ProviderError("PROVIDER_OUTPUT_INVALID", "Malformed ComfyUI history")
            status = record["status"]
            if status.get("status_str") == "error":
                raise ProviderError(
                    "PROVIDER_JOB_FAILED",
                    "ComfyUI workflow failed; inspect server execution history",
                )
            if status.get("completed") is True:
                break
        until = time.monotonic() + config["video_poll_seconds"]
        while time.monotonic() < until:
            if not checkpoint():
                raise Cancelled()
            time.sleep(0.2)
    else:
        raise ProviderError(
            "PROVIDER_TIMEOUT",
            "ComfyUI is still pending; retry resumes the prompt ID",
            transient=True,
        )
    source = work / "comfy-output.bin"
    transport.request(
        "GET",
        "view",
        params=output_file(record, template),
        output=source,
        checkpoint=checkpoint,
        limit=128 * 1024 * 1024,
    )
    if template.media_type == "video":
        from app.remote_media import normalize_video

        return normalize_video(settings, job["request"], source, work, checkpoint)
    try:
        with Image.open(source) as image:
            if image.width * image.height > 64_000_000:
                raise ValueError()
            image.load()
            output = work / "output.png"
            image.convert("RGB").save(output, "PNG")
            metadata = {"width": image.width, "height": image.height}
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ProviderError(
            "PROVIDER_OUTPUT_INVALID", "Workflow output is not a valid image"
        ) from error
    return {"video": output, "metadata": metadata, "media_type": "image"}
