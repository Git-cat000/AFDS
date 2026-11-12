"""Bounded local image ingestion and canonical reference-image preparation."""

import errno
import json
from pathlib import Path
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from app.errors import DomainError
from app.media import Cancelled, run_process
from app.storage import contained, file_hash

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def canonical_image(source, output):
    try:
        with Image.open(source) as image:
            if (
                image.format not in {"PNG", "JPEG", "WEBP"}
                or image.width * image.height > MAX_IMAGE_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("Unsupported image profile")
            image.load()
            image.convert("RGB").save(output, "PNG")
            return {"width": image.width, "height": image.height, "codec": "png"}
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            raise
        raise DomainError(
            "IMAGE_INVALID", "Use a single PNG, JPEG or WebP image up to 16 million pixels", 422
        ) from error


def process_image(settings, job, work, checkpoint):
    request = job["request"]
    relative = Path(request["staging_path"])
    if (
        len(relative.parts) != 3
        or relative.parts[:2] != ("uploads", job["project_id"])
        or relative.suffix != ".image"
    ):
        raise DomainError("IMAGE_INVALID", "Image staging ownership is invalid", 422)
    UUID(relative.stem)
    source = contained(settings.data_dir, relative)
    if source.stat().st_size > MAX_IMAGE_BYTES or file_hash(source) != request["sha256"]:
        raise DomainError("IMAGE_INVALID", "Image staging integrity check failed", 422)
    if not checkpoint():
        raise Cancelled()
    output = work / "image.png"
    metadata = canonical_image(source, output)
    verified = json.loads(
        run_process(
            [
                settings.binary("ffprobe"),
                "-v",
                "error",
                "-f",
                "png_pipe",
                "-protocol_whitelist",
                "file",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(output),
            ],
            30,
            checkpoint,
        )
    )["streams"][0]
    if (verified["codec_name"], verified["width"], verified["height"]) != (
        "png",
        metadata["width"],
        metadata["height"],
    ):
        raise DomainError("IMAGE_INVALID", "Canonical image failed media validation", 422)
    return {"video": output, "metadata": metadata, "media_type": "image", "is_mock": False}
