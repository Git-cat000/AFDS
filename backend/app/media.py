import json
import subprocess
import time
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.errors import DomainError


class Cancelled(Exception):
    pass


def run_process(arguments, timeout, checkpoint):
    # communicate(timeout) drains pipes and preserves buffered output across retries.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with subprocess.Popen(
        arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags
    ) as process:
        started = time.monotonic()
        try:
            while True:
                if not checkpoint():
                    raise Cancelled()
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Media process exceeded its time limit")
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.kill()
            process.communicate()
            raise
    if process.returncode:
        # Exclude stderr paths and input text from public task errors.
        raise DomainError(
            "FFMPEG_ERROR", f"Media command exited with code {process.returncode}", 500
        )
    return stdout


def probe(settings, path):
    raw = run_process(
        [
            settings.binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        30,
        lambda: True,
    )
    result = json.loads(raw)
    if not result.get("streams"):
        raise DomainError("INVALID_MEDIA", "No video stream found", 422)
    stream = result["streams"][0]
    fps = Fraction(stream["avg_frame_rate"])
    duration = float(stream.get("duration", result["format"]["duration"]))
    return {
        "codec": stream["codec_name"],
        "width": stream["width"],
        "height": stream["height"],
        "fps_num": fps.numerator,
        "fps_den": fps.denominator,
        "duration_seconds": duration,
        "duration_frames": int(stream.get("nb_frames") or round(duration * float(fps))),
    }


def verify(settings, path, fmt, frames):
    metadata = probe(settings, path)
    if (
        metadata["codec"] != "h264"
        or metadata["width"] != fmt["width"]
        or metadata["height"] != fmt["height"]
        or metadata["duration_frames"] != frames
        or Fraction(metadata["fps_num"], metadata["fps_den"])
        != Fraction(fmt["fps_num"], fmt["fps_den"])
    ):
        raise DomainError(
            "MEDIA_VERIFICATION_FAILED", "Output differs from requested media profile", 422
        )
    expected = frames * fmt["fps_den"] / fmt["fps_num"]
    if abs(metadata["duration_seconds"] - expected) > fmt["fps_den"] / fmt["fps_num"]:
        raise DomainError(
            "MEDIA_DURATION_MISMATCH", "Output duration differs by over one frame", 422
        )
    return metadata


def poster(request, path):
    width, height = request["format"]["width"], request["format"]["height"]
    palette = ["#234d58", "#775743", "#584e76", "#326458"]
    image = Image.new("RGB", (width, height), palette[request["order"] % len(palette)])
    draw = ImageDraw.Draw(image)
    large = ImageFont.load_default(size=max(18, width // 22))
    small = ImageFont.load_default(size=max(12, width // 45))
    draw.rectangle(
        (width * 0.07, height * 0.1, width * 0.93, height * 0.9), outline="#d9e0d5", width=2
    )
    draw.text((width * 0.12, height * 0.2), "AFDS / TEST FOOTAGE", fill="#d9e0d5", font=small)
    draw.text(
        (width * 0.12, height * 0.38), f"SHOT {request['order'] + 1:02d}", fill="white", font=large
    )
    draw.text((width * 0.12, height * 0.57), "DEMO / MOCK", fill="#f5d38c", font=large)
    draw.text(
        (width * 0.12, height * 0.76),
        request["camera"]["shot_size"].upper(),
        fill="white",
        font=small,
    )
    image.save(path)


class MockVideo:
    provider_id = "mock-video-local"

    def __init__(self, settings):
        self.settings = settings

    def describe(self):
        from app.adapters import providers

        return providers(self.settings)[1]

    def generate(self, request, output: Path, checkpoint):
        from app.execution.queue import digest

        fmt = request["format"]
        thumbnail = output.with_suffix(".png")
        poster(request, thumbnail)
        fps = f"{fmt['fps_num']}/{fmt['fps_den']}"
        run_process(
            [
                self.settings.binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-loop",
                "1",
                "-framerate",
                fps,
                "-i",
                str(thumbnail),
                "-frames:v",
                str(request["duration_frames"]),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-metadata",
                f"comment=AFDS DEMO/MOCK {digest(request)}",
                str(output),
            ],
            self.settings.media_timeout,
            checkpoint,
        )
        return {
            "video": output,
            "poster": thumbnail,
            "metadata": verify(self.settings, output, fmt, request["duration_frames"]),
        }


def render(settings, request, sources, output, checkpoint):
    fmt = request["format"]
    fps = f"{fmt['fps_num']}/{fmt['fps_den']}"
    normalized = []
    for index, (item, source) in enumerate(zip(request["items"], sources, strict=True)):
        segment = output.parent / f"normalized-{index}.mp4"
        filters = (
            f"scale={fmt['width']}:{fmt['height']}:force_original_aspect_ratio=decrease,"
            f"pad={fmt['width']}:{fmt['height']}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},tpad=stop_mode=clone:stop_duration=30,setsar=1"
        )
        run_process(
            [
                settings.binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-vf",
                filters,
                "-frames:v",
                str(item["duration_frames"]),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(segment),
            ],
            settings.media_timeout,
            checkpoint,
        )
        normalized.append(segment)
    # Generated basenames only, so no user-controlled concat quoting or paths.
    listing = output.parent / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in normalized), encoding="utf-8")
    run_process(
        [
            settings.binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ],
        settings.media_timeout,
        checkpoint,
    )
    return verify(settings, output, fmt, sum(i["duration_frames"] for i in request["items"]))
