"""OpenAI-compatible media contracts with durable submission recovery."""

import base64
import binascii
import json
import re
import threading
import time
from contextlib import contextmanager
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError

from app.errors import DomainError
from app.media import Cancelled, probe, render, run_process
from app.provider_http import ProviderError, ProviderHTTP
from app.provider_settings import ProviderStore


@contextmanager
def remote_lease(queue, job, owner):
    stopped, lost = threading.Event(), threading.Event()

    def renew():
        interval = max(0.5, queue.database.settings.lease_seconds / 3)
        while not stopped.wait(interval):
            try:
                if not queue.heartbeat(job["id"], owner):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield lambda: not lost.is_set() and queue.heartbeat(job["id"], owner)
    finally:
        stopped.set()
        thread.join(timeout=5)


def prompt_for_shot(request):
    return json.dumps(
        {key: request[key] for key in ("action", "emotion", "camera", "style_bible")},
        ensure_ascii=False,
    )


def execute_remote_media(queue, job, owner, work, checkpoint):
    from app.budget import BudgetService

    snapshot = job["request"]["provider_config"]
    store = ProviderStore(queue.database)
    if not store.runtime(snapshot["kind"])["enabled"]:
        raise DomainError("MODEL_UNAVAILABLE", "Provider was disabled in settings", 422)
    config = store.runtime(snapshot["kind"], snapshot["revision"])
    BudgetService(queue.database).enforce(queue.engine.read(job["project_id"]), config)
    transport = ProviderHTTP(config)
    settings = queue.database.settings

    def persist(**values):
        queue.update_remote(job, owner, **values)

    if not checkpoint():
        raise Cancelled()
    try:
        if config.get("protocol") == "comfyui":
            from app.comfy_adapter import generate_comfy

            result = generate_comfy(transport, config, job, work, checkpoint, persist, settings)
        elif job["operation"] == "generate_video":
            result = generate_video(transport, config, job, work, checkpoint, persist, settings)
        else:
            if job.get("submission_started"):
                raise ProviderError(
                    "REMOTE_SUBMISSION_UNCERTAIN",
                    "Previous submission may have been accepted. "
                    "Create a new request only after checking provider usage.",
                )
            persist(submission_started=True)
            if job["operation"] == "generate_image":
                result = generate_image(
                    transport,
                    config,
                    job["request"],
                    work,
                    checkpoint,
                    lambda: persist(generation_accepted=True),
                )
            else:
                result = generate_speech(
                    transport, config, job["request"], work, checkpoint, settings
                )
        result["is_mock"] = False
        return result
    except ProviderError as error:
        if (
            config.get("protocol") != "comfyui"
            and job["operation"] != "generate_video"
            and error.code
            in {
                "PROVIDER_OUTPUT_INVALID",
                "PROVIDER_OUTPUT_TOO_LARGE",
            }
        ):
            raise
        if (
            error.rejected
            and not job.get("external_task_id")
            and not job.get("generation_accepted")
        ):
            persist(submission_started=False)
        elif job.get("submission_started") and not job.get("external_task_id"):
            raise ProviderError(
                "REMOTE_SUBMISSION_UNCERTAIN",
                "Submission outcome is unknown. "
                "Check the provider dashboard before any new generation.",
            ) from error
        raise


def generate_video(transport, config, job, work, checkpoint, persist, settings):
    external_id = job.get("external_task_id")
    if not external_id:
        if job.get("submission_started"):
            raise ProviderError(
                "REMOTE_SUBMISSION_UNCERTAIN",
                "Recover with the existing provider video ID. Automatic resubmission is blocked.",
            )
        persist(submission_started=True)
        response = transport.json(
            "POST",
            "videos",
            multipart={
                "model": config["model"],
                "prompt": prompt_for_shot(job["request"]),
                "seconds": config["video_seconds"],
                "size": config["video_size"],
            },
            checkpoint=checkpoint,
        )
        external_id = response.get("id")
        if not isinstance(external_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,200}", external_id
        ):
            raise ProviderError(
                "PROVIDER_OUTPUT_INVALID", "Video submission did not return a valid task ID"
            )
        persist(external_task_id=external_id, remote_cancel_supported=False)
    encoded = quote(external_id, safe="")
    deadline = time.monotonic() + config["video_timeout_seconds"]
    while time.monotonic() < deadline:
        if not checkpoint():
            raise Cancelled()
        response = transport.json("GET", f"videos/{encoded}", checkpoint=checkpoint)
        state = response.get("status")
        if state == "completed":
            break
        if state in {"failed", "cancelled"}:
            raise ProviderError(
                "PROVIDER_JOB_FAILED", "The remote video task failed or was cancelled"
            )
        if state not in {"queued", "in_progress", "processing", "running", "pending"}:
            raise ProviderError("PROVIDER_OUTPUT_INVALID", "Unrecognized remote video status")
        progress = response.get("progress", 0)
        progress = min(85, max(10, int(progress))) if isinstance(progress, (int, float)) else 10
        persist(progress=progress)
        until = time.monotonic() + config["video_poll_seconds"]
        while time.monotonic() < until:
            if not checkpoint():
                raise Cancelled()
            time.sleep(0.2)
    else:
        raise ProviderError(
            "PROVIDER_TIMEOUT",
            "Remote video still pending. Retry resumes its task ID.",
            transient=True,
        )
    source = work / "remote.mp4"
    transport.request(
        "GET",
        f"videos/{encoded}/content",
        output=source,
        checkpoint=checkpoint,
        limit=128 * 1024 * 1024,
    )
    return normalize_video(settings, job["request"], source, work, checkpoint)


def normalize_video(settings, request, source, work, checkpoint):
    original = probe(settings, source)
    output = work / "output.mp4"
    metadata = render(
        settings,
        {"format": request["format"], "items": [{"duration_frames": request["duration_frames"]}]},
        [source],
        output,
        checkpoint,
    )
    poster = work / "poster.png"
    run_process(
        [
            settings.binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(output),
            "-frames:v",
            "1",
            str(poster),
        ],
        settings.media_timeout,
        checkpoint,
    )
    return {
        "video": output,
        "poster": poster,
        "metadata": metadata,
        "media_type": "video",
        "normalization": {"source": original, "target": metadata, "audio_policy": "silent"},
    }


def generate_image(transport, config, request, work, checkpoint, accepted=lambda: None):
    response = transport.json(
        "POST",
        "images/generations",
        payload={
            "model": config["model"],
            "prompt": request["prompt"],
            "n": 1,
            "size": config["image_size"],
        },
        checkpoint=checkpoint,
    )
    accepted()
    try:
        first = response["data"][0]
        if not isinstance(first, dict):
            raise ValueError("Image result must be an object")
        source = work / "image-source.bin"
        if first.get("b64_json"):
            source.write_bytes(base64.b64decode(first["b64_json"], validate=True))
        elif isinstance(first.get("url"), str):
            transport.request(
                "GET",
                "",
                external_url=first["url"],
                output=source,
                checkpoint=checkpoint,
                limit=32 * 1024 * 1024,
            )
        else:
            raise ValueError("Missing image content")
        with Image.open(source) as image:
            if image.width * image.height > 64_000_000:
                raise ValueError("Image dimensions too large")
            image.load()
            output = work / "output.png"
            image.convert("RGB").save(output, format="PNG")
            metadata = {"width": image.width, "height": image.height}
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        binascii.Error,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as error:
        raise ProviderError(
            "PROVIDER_OUTPUT_INVALID", "Provider returned invalid image data"
        ) from error
    return {"video": output, "metadata": metadata, "media_type": "image"}


def generate_speech(transport, config, request, work, checkpoint, settings):
    source = work / "speech-source.bin"
    transport.request(
        "POST",
        "audio/speech",
        payload={
            "model": config["model"],
            "input": request["prompt"],
            "voice": config["speech_voice"],
            "response_format": "mp3",
        },
        output=source,
        checkpoint=checkpoint,
        limit=32 * 1024 * 1024,
    )
    output = work / "output.mp3"
    run_process(
        [
            settings.binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ar",
            "48000",
            "-c:a",
            "libmp3lame",
            str(output),
        ],
        settings.media_timeout,
        checkpoint,
    )
    raw = run_process(
        [
            settings.binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output),
        ],
        30,
        checkpoint,
    )
    try:
        result = json.loads(raw)
        codec = result["streams"][0]["codec_name"]
        duration = float(result["format"]["duration"])
        if duration <= 0 or duration > 600:
            raise ValueError("Invalid duration")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ProviderError(
            "PROVIDER_OUTPUT_INVALID", "Provider returned invalid speech audio"
        ) from error
    return {
        "video": output,
        "metadata": {"codec": codec, "duration_seconds": duration},
        "media_type": "audio",
    }
