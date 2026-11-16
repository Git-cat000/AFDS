"""Bounded non-playlist video ingest, normalization and optional audio extraction."""

import json
import math
import time
from fractions import Fraction
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from app.audio_pipeline import probe_audio
from app.errors import DomainError
from app.media import run_process, verify
from app.schemas import Format, Schema
from app.storage import contained, ensure_space, file_hash
from app.timeline_models import MAX_FRAMES

MAX_VIDEO_UPLOAD_BYTES = 256 * 1024**2


class VideoImportRequest(Schema):
    project_id: UUID
    staging_path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=200)
    rights_confirmed: bool
    extract_audio: bool = True
    format: Format
    provider_id: Literal["local-video-import"] = "local-video-import"
    model_id: Literal["ffmpeg-h264"] = "ffmpeg-h264"
    adapter_version: Literal["0.5.0"] = "0.5.0"


def video_input(path):
    with path.open("rb") as handle:
        header = handle.read(16)
    if header[:4] == b"\x1aE\xdf\xa3":
        return ["-f", "matroska", "-protocol_whitelist", "file", "-i", str(path)]
    if header[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"}:
        return ["-f", "mov", "-enable_drefs", "0", "-protocol_whitelist", "file", "-i", str(path)]
    raise DomainError("INVALID_VIDEO", "Upload an MP4, MOV, MKV or WebM video", 422)


def process_video_import(settings, request, work, checkpoint):
    try:
        request = VideoImportRequest.model_validate(request).model_dump(mode="json")
        pid = request["project_id"]
        relative = Path(request["staging_path"])
        if (
            relative.is_absolute()
            or len(relative.parts) != 3
            or relative.parts[:2] != ("uploads", pid)
            or relative.suffix != ".upload"
        ):
            raise ValueError("Foreign staging path")
        UUID(relative.stem)
    except (ValidationError, ValueError, KeyError) as error:
        raise DomainError("INVALID_VIDEO", "Invalid video import request", 422) from error
    if request["rights_confirmed"] is not True:
        raise DomainError("VIDEO_RIGHTS_REQUIRED", "Confirm your rights to use this video", 422)
    source = contained(settings.data_dir, relative)
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_VIDEO_UPLOAD_BYTES:
        raise DomainError("INVALID_VIDEO", "Video upload is missing or exceeds 256 MB", 422)
    if file_hash(source) != request["sha256"]:
        raise DomainError("HASH_MISMATCH", "Uploaded video changed", 422)
    started = time.monotonic()

    def check():
        if time.monotonic() - started > settings.media_timeout:
            raise TimeoutError("Video import exceeded its time limit")
        return checkpoint()

    input_args = video_input(source)
    try:
        raw = run_process(
            [
                settings.binary("ffprobe"),
                "-v",
                "error",
                *input_args[:-2],
                str(source),
                "-show_streams",
                "-show_format",
                "-of",
                "json",
            ],
            30,
            check,
        )
    except DomainError as error:
        if error.code == "FFMPEG_ERROR":
            raise DomainError("INVALID_VIDEO", "Cannot read the uploaded video", 422) from error
        raise
    try:
        info = json.loads(raw)
        stream = next(item for item in info["streams"] if item["codec_type"] == "video")
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(stream.get("duration") or info["format"]["duration"])
        if not 0 < width * height <= 33_177_600 or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Video dimensions or duration are out of bounds")
        rate = Fraction(request["format"]["fps_num"], request["format"]["fps_den"])
        frames = max(1, round(duration * rate))
        if frames > MAX_FRAMES:
            raise ValueError("Video exceeds the supported frame count")
        audio = next((item for item in info["streams"] if item["codec_type"] == "audio"), None)
        if audio and (
            not 1 <= int(audio["channels"]) <= 8 or not 8000 <= int(audio["sample_rate"]) <= 384000
        ):
            raise ValueError("Audio profile is out of bounds")
    except (ValueError, KeyError, StopIteration, TypeError) as error:
        raise DomainError("INVALID_VIDEO", "No supported finite video stream found", 422) from error
    target_seconds = float(Fraction(frames, 1) / rate)
    if audio and request["extract_audio"]:
        ensure_space(settings.data_dir, math.ceil(target_seconds * 48000 * 4))
    fmt = request["format"]
    output = Path(work) / "imported-video.mp4"
    filters = (
        f"scale={fmt['width']}:{fmt['height']}:force_original_aspect_ratio=decrease,"
        f"pad={fmt['width']}:{fmt['height']}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fmt['fps_num']}/{fmt['fps_den']},tpad=stop_mode=clone:stop_duration=1,setsar=1"
    )
    args = [
        settings.binary("ffmpeg"),
        "-y",
        "-v",
        "error",
        *input_args,
        "-map",
        "0:v:0",
        "-vf",
        filters,
        "-frames:v",
        str(frames),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
    ]
    if audio:
        args += [
            "-map",
            "0:a:0",
            "-af",
            f"apad,atrim=duration={target_seconds:.9f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
    else:
        args += ["-an"]
    args += [
        "-t",
        f"{target_seconds:.9f}",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        str(output),
    ]
    run_process(args, settings.media_timeout, check)
    metadata = verify(settings, output, fmt, frames)
    attachments = []
    if audio and request["extract_audio"]:
        sound = Path(work) / "imported-video-audio.wav"
        run_process(
            [
                settings.binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-i",
                str(output),
                "-map",
                "0:a:0",
                "-vn",
                "-af",
                f"apad,atrim=duration={target_seconds:.9f}",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(sound),
            ],
            settings.media_timeout,
            check,
        )
        attachments.append(
            {
                "path": sound,
                "metadata": probe_audio(settings, sound, check, max_seconds=target_seconds + 1),
                "media_type": "audio",
            }
        )
    return {"video": output, "metadata": metadata, "is_mock": False, "attachments": attachments}
