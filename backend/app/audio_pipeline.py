"""Worker-only audio import and bounded FFmpeg composition."""

import json
import math
import shutil
from fractions import Fraction
from pathlib import Path
from uuid import UUID, uuid4

from app.errors import DomainError
from app.media import run_process, verify
from app.storage import contained, file_hash
from app.timeline_models import MAX_AUDIO_ITEMS, Caption

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 3600


def probe_audio(settings, path, checkpoint, demuxer=None, *, max_seconds=MAX_AUDIO_SECONDS):
    arguments = [settings.binary("ffprobe"), "-v", "error"]
    if demuxer:
        arguments += ["-f", demuxer]
    arguments += [
        "-protocol_whitelist",
        "file",
        "-select_streams",
        "a:0",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = json.loads(run_process(arguments, 30, checkpoint))
        stream = result["streams"][0]
        seconds = float(stream.get("duration") or result["format"]["duration"])
        if not math.isfinite(seconds) or not 0 < seconds <= max_seconds:
            raise ValueError("Audio duration is out of bounds")
        rate, channels = int(stream["sample_rate"]), int(stream["channels"])
        if not 8000 <= rate <= 384000 or not 1 <= channels <= 8:
            raise ValueError("Unsupported audio profile")
        return {
            "codec": stream["codec_name"],
            "duration_seconds": seconds,
            "sample_rate": rate,
            "channels": channels,
        }
    except (ValueError, KeyError, IndexError) as error:
        raise DomainError("INVALID_AUDIO", "No supported finite audio stream found", 422) from error


def _upload_demuxer(path):
    # Force a non-playlist demuxer before ffprobe/FFmpeg see untrusted bytes.
    # This also prevents a disguised playlist from reading arbitrary local files.
    with path.open("rb") as handle:
        header = handle.read(16)
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:4] == b"fLaC":
        return "flac"
    if header[:4] == b"OggS":
        return "ogg"
    if header[:3] == b"ID3" or (len(header) > 1 and header[0] == 255 and header[1] & 0xE0 == 0xE0):
        return "mp3"
    raise DomainError("INVALID_AUDIO", "Upload a WAV, MP3, FLAC or Ogg audio file", 422)


def process_audio_import(settings, request, work, checkpoint):
    if request.get("rights_confirmed") is not True:
        raise DomainError("AUDIO_RIGHTS_REQUIRED", "Confirm rights to use this audio", 422)
    try:
        pid = str(UUID(request["project_id"]))
        relative = Path(request["staging_path"])
        if relative.is_absolute() or len(relative.parts) != 3:
            raise ValueError("Invalid staging path")
        if relative.parts[:2] != ("uploads", pid) or relative.suffix != ".upload":
            raise ValueError("Foreign staging path")
        UUID(relative.stem)
    except (ValueError, KeyError, TypeError) as error:
        raise DomainError(
            "INVALID_PATH", "Audio staging file does not belong to this project", 403
        ) from error
    source = contained(settings.data_dir, relative)
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_UPLOAD_BYTES:
        raise DomainError("INVALID_AUDIO", "Audio staging file is missing or exceeds 32 MB", 422)
    if request.get("sha256") and file_hash(source) != request["sha256"]:
        raise DomainError("HASH_MISMATCH", "Uploaded audio has changed", 422)
    demuxer = _upload_demuxer(source)
    metadata = probe_audio(settings, source, checkpoint, demuxer)
    output = Path(work) / "imported-audio.wav"
    run_process(
        [
            settings.binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-f",
            demuxer,
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-t",
            str(metadata["duration_seconds"]),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            "-map_metadata",
            "-1",
            str(output),
        ],
        settings.media_timeout,
        checkpoint,
    )
    verified = probe_audio(settings, output, checkpoint)
    if (
        verified["codec"] != "pcm_s16le"
        or verified["sample_rate"] != 48000
        or verified["channels"] != 2
    ):
        raise DomainError("INVALID_AUDIO", "Imported audio does not match the storage profile", 422)
    return {"video": output, "metadata": verified, "media_type": "audio", "is_mock": False}


def _ass_time(seconds):
    centiseconds = round(seconds * 100)
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, hundredths = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{hundredths:02}"


def _subtitle_file(request, directory):
    fmt = request["format"]
    rate = Fraction(fmt["fps_num"], fmt["fps_den"])
    font_size = max(14, round(fmt["height"] * 0.045))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        f"PlayResX: {fmt['width']}",
        f"PlayResY: {fmt['height']}",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,1,1,2,12,12,12,1",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for raw in request.get("captions", []):
        caption = Caption.model_validate(raw)
        # ASS override syntax has no dependable literal brace escape. Full-width
        # glyphs preserve visible punctuation while preventing commands/\N injection.
        text = caption.text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")
        text = "".join(c for c in text if ord(c) >= 32)
        start = _ass_time(float(Fraction(caption.start_frame, 1) / rate))
        end = _ass_time(float(Fraction(caption.start_frame + caption.duration_frames, 1) / rate))
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
    path = directory / f"captions-{uuid4().hex}.ass"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _filter_filename(path):
    # The option parser consumes one layer; the filter graph consumes another.
    # Use escaping at both levels rather than mixing shell-style quote rules.
    value = "".join("\\" + char if char in "\\':" else char for char in path.resolve().as_posix())
    return "".join("\\" + char if char in "\\'[],;" else char for char in value)


def finish_audio(settings, request, audio_sources, video_source, output, checkpoint):
    items = request.get("audio_items", [])
    captions = request.get("captions", [])
    if len(items) > MAX_AUDIO_ITEMS or len(captions) > 500 or len(items) != len(audio_sources):
        raise DomainError("INVALID_TIMELINE", "Composition exceeds supported limits", 422)
    fmt = request["format"]
    rate = Fraction(fmt["fps_num"], fmt["fps_den"])
    frames = sum(i["duration_frames"] for i in request["items"])
    duration = float(Fraction(frames, 1) / rate)
    if Path(output).resolve() == Path(video_source).resolve():
        raise DomainError("INVALID_PATH", "Finishing requires a separate immutable output", 422)
    if not items and not captions:
        shutil.copyfile(video_source, output)
        return verify(settings, output, fmt, frames)
    args = [settings.binary("ffmpeg"), "-y", "-v", "error", "-i", str(video_source)]
    graph = []
    labels = []
    if items:
        for index, (item, source) in enumerate(zip(items, audio_sources, strict=True), start=1):
            args += ["-i", str(source)]
            start = float(Fraction(item["source_start_frame"], 1) / rate)
            length = float(Fraction(item["duration_frames"], 1) / rate)
            delay_samples = round(Fraction(item["start_frame"] * 48000, 1) / rate)
            gain = item["gain_db"] + item.get("track_gain_db", 0)
            if not math.isfinite(gain) or not -192 <= gain <= 48:
                raise DomainError("INVALID_TIMELINE", "Invalid audio gain", 422)
            volume = 0 if item.get("muted") else 10 ** (gain / 20)
            label = f"a{index}"
            graph.append(
                f"[{index}:a:0]atrim=start={start:.9f}:duration={length:.9f},"
                f"asetpts=PTS-STARTPTS,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={volume:.9f},"
                f"adelay={delay_samples}S:all=1[{label}]"
            )
            labels.append(f"[{label}]")
        # A full-length silent bed ensures exact leading/trailing silence even
        # when every placed clip is short, delayed or muted.
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.9f}[bed]")
        graph.append(
            f"[bed]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:"
            f"dropout_transition=0:normalize=0,alimiter=limit=0.98:level=0:latency=1,"
            f"atrim=duration={duration:.9f}[mixed]"
        )
    if captions:
        subtitle = _subtitle_file(request, Path(output).parent)
        graph.append(f"[0:v:0]subtitles=filename={_filter_filename(subtitle)}[captioned]")
    if graph:
        args += ["-filter_complex", ";".join(graph)]
    args += ["-map", "[captioned]" if captions else "0:v:0"]
    if items:
        args += ["-map", "[mixed]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        args += ["-an"]
    args += ["-c:v", "libx264" if captions else "copy"]
    if captions:
        args += ["-preset", "ultrafast", "-pix_fmt", "yuv420p", "-frames:v", str(frames)]
    args += ["-t", f"{duration:.9f}", "-movflags", "+faststart", "-map_metadata", "-1", str(output)]
    run_process(args, settings.media_timeout, checkpoint)
    metadata = verify(settings, output, fmt, frames)
    if items:
        audio = probe_audio(settings, output, checkpoint, max_seconds=duration + 1)
        if audio["codec"] != "aac" or audio["sample_rate"] != 48000 or audio["channels"] != 2:
            raise DomainError("INVALID_AUDIO", "Final audio does not match AAC 48 kHz stereo", 422)
        if abs(audio["duration_seconds"] - duration) > max(1 / float(rate), 0.025):
            raise DomainError(
                "INVALID_AUDIO", "Final audio duration differs from the backbone", 422
            )
        metadata["audio"] = audio
    metadata["caption_count"] = len(captions)
    return metadata
