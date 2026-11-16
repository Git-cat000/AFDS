"""Worker-only frame-accurate layered video composition and bounded previews."""

import copy
import time
from fractions import Fraction

from app.media import run_process, verify


def seconds(frames, fmt):
    return float(Fraction(frames) * fmt["fps_den"] / fmt["fps_num"])


def compile_video_graph(spec):
    fmt = spec["format"]
    fps = f"{fmt['fps_num']}/{fmt['fps_den']}"
    graph = []
    clips = spec["items"]
    for index in range(len(clips)):
        graph.append(f"[{index}:v]fps={fps},settb=AVTB,setpts=PTS-STARTPTS[v{index}]")
    transitions = {(item["from_clip_id"], item["to_clip_id"]): item for item in spec["transitions"]}
    current = "v0"
    effects = {"dissolve": "fade", "fadeblack": "fadeblack", "wipeleft": "wipeleft"}
    for index in range(1, len(clips)):
        transition = transitions.get((clips[index - 1]["id"], clips[index]["id"]))
        label = f"cut{index}"
        if transition:
            duration = seconds(transition["duration_frames"], fmt)
            offset = seconds(clips[index]["start_frame"], fmt)
            graph.append(
                f"[{current}][v{index}]xfade=transition={effects[transition['kind']]}:duration={duration:.9f}:offset={offset:.9f}[{label}]"
            )
        else:
            graph.append(f"[{current}][v{index}]concat=n=2:v=1:a=0,fps={fps},settb=AVTB[{label}]")
        current = label
    source_index = len(clips)
    for track in spec["video_layers"]:
        for item in track["items"]:
            width = max(2, round(fmt["width"] * item["width"] / 2) * 2)
            height = max(2, round(fmt["height"] * item["height"] / 2) * 2)
            x = round(fmt["width"] * item["x"])
            y = round(fmt["height"] * item["y"])
            start = seconds(item["start_frame"], fmt)
            source_start = item["source_start_frame"]
            source_end = source_start + item["duration_frames"]
            layer, combined = f"layer{source_index}", f"mix{source_index}"
            graph.append(
                f"[{source_index}:v]fps={fps},trim=start_frame={source_start}:end_frame={source_end},"
                f"settb=AVTB,setpts=PTS-STARTPTS+{start:.9f}/TB,format=rgba,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
                f"colorchannelmixer=aa={item['opacity']}[{layer}]"
            )
            first = seconds(item["start_frame"] - 0.5, fmt)
            last = seconds(item["start_frame"] + item["duration_frames"] - 0.5, fmt)
            graph.append(
                f"[{current}][{layer}]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0:enable='between(t,{first:.9f},{last:.9f})'[{combined}]"
            )
            current = combined
            source_index += 1
    graph.append(f"[{current}]format=yuv420p[composed]")
    return ["-filter_complex", ";".join(graph)], "[composed]"


def render_composition(settings, spec, sources, output, checkpoint):
    started = time.monotonic()

    def check():
        if time.monotonic() - started > settings.media_timeout:
            raise TimeoutError("Composition exceeded its time limit")
        return checkpoint()

    fmt = spec["format"]
    fps = f"{fmt['fps_num']}/{fmt['fps_den']}"
    args = [settings.binary("ffmpeg"), "-y", "-v", "error", "-filter_complex_threads", "1"]
    for index, item in enumerate(spec["items"]):
        normalized = output.parent / f"main-{index}.mp4"
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
                str(sources[item["asset_id"]]),
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
                str(normalized),
            ],
            settings.media_timeout,
            check,
        )
        args += ["-i", str(normalized)]
    for track in spec["video_layers"]:
        for item in track["items"]:
            if item["media_type"] == "image":
                args += ["-loop", "1", "-framerate", fps]
            args += ["-i", str(sources[item["asset_id"]])]
    graph, label = compile_video_graph(spec)
    args += graph + [
        "-map",
        label,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        fps,
        "-frames:v",
        str(spec["total_frames"]),
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        str(output),
    ]
    run_process(args, settings.media_timeout, check)
    return verify(settings, output, fmt, spec["total_frames"])


def preview_spec(spec, start_frame, duration_frames, max_width):
    """Retain intersecting source clips and rebase their shared time interval."""
    result = copy.deepcopy(spec)
    end_frame = start_frame + duration_frames
    clips = [
        item
        for item in result["items"]
        if item["start_frame"] < end_frame
        and item["start_frame"] + item["duration_frames"] > start_frame
    ]
    origin = clips[0]["start_frame"]
    end = clips[-1]["start_frame"] + clips[-1]["duration_frames"]
    result["items"] = clips
    for item in clips:
        item["start_frame"] -= origin
    identities = {item["id"] for item in clips}
    result["transitions"] = [
        item
        for item in result["transitions"]
        if item["from_clip_id"] in identities and item["to_clip_id"] in identities
    ]

    def intersect(items, source=False):
        retained = []
        for item in items:
            first = max(origin, item["start_frame"])
            last = min(end, item["start_frame"] + item["duration_frames"])
            if first >= last:
                continue
            selected = {**item, "start_frame": first - origin, "duration_frames": last - first}
            if source:
                selected["source_start_frame"] += first - item["start_frame"]
            retained.append(selected)
        return retained

    for track in result["video_layers"]:
        track["items"] = intersect(track["items"], source=True)
    result["audio_items"] = intersect(result["audio_items"], source=True)
    result["captions"] = intersect(result["captions"])
    result["markers"] = [
        {**marker, "frame": marker["frame"] - origin}
        for marker in result["markers"]
        if origin <= marker["frame"] <= end
    ]
    result["total_frames"] = end - origin
    ratio = min(1, max_width / result["format"]["width"])
    for key in ("width", "height"):
        result["format"][key] = max(2, round(result["format"][key] * ratio / 2) * 2)
    result["preview"] = {
        "start_frame": start_frame - origin,
        "duration_frames": duration_frames,
        "source_start_frame": start_frame,
        "max_width": max_width,
    }
    return result


def trim_preview(settings, request, source, output, checkpoint):
    preview = request["preview"]
    first = preview["start_frame"]
    count = preview["duration_frames"]
    fmt = request["format"]
    start, duration = seconds(first, fmt), seconds(count, fmt)
    args = [
        settings.binary("ffmpeg"),
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-vf",
        f"trim=start_frame={first}:end_frame={first + count},setpts=PTS-STARTPTS",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-frames:v",
        str(count),
        "-af",
        f"atrim=start={start:.9f}:duration={duration:.9f},asetpts=PTS-STARTPTS",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run_process(args, settings.media_timeout, checkpoint)
    return verify(settings, output, fmt, count)
