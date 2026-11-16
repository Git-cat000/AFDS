"""Canonical frame-based video layers, transitions and resolved media inputs."""

import math
from fractions import Fraction
from typing import TypedDict

from app.errors import DomainError


class CompositionSpec(TypedDict):
    items: list[dict]
    video_layers: list[dict]
    transitions: list[dict]
    total_frames: int
    format: dict
    audio_policy: str
    audio_items: list[dict]
    audio_tracks: list[dict]
    captions: list[dict]
    markers: list[dict]
    adapter_version: str
    composition_version: str


def backbone_duration(timeline):
    items = timeline["tracks"][0]["items"]
    return items[-1]["start_frame"] + items[-1]["duration_frames"] if items else 0


def position_backbone(timeline):
    transitions = {
        (item["from_clip_id"], item["to_clip_id"]): item["duration_frames"]
        for item in timeline.get("transitions", [])
    }
    end, previous = 0, None
    for item in timeline["tracks"][0]["items"]:
        overlap = transitions.get((previous, item.get("id")), 0)
        item["start_frame"] = end - overlap
        end = item["start_frame"] + item["duration_frames"]
        previous = item.get("id")


def validate_video_layers(apr, timeline, end):
    assets = {asset["id"]: asset for asset in apr["assets"]}
    versions = {version["id"]: version for version in apr.get("asset_versions", [])}
    track_ids = [track.id for track in timeline.tracks] + [
        track.id for track in timeline.video_layers
    ]
    if len(track_ids) != len(set(track_ids)):
        raise ValueError("Timeline track identifiers must be unique")
    identities = [str(item.id) for item in timeline.tracks[0].items if item.id]
    identities += [str(item.id) for track in timeline.video_layers for item in track.items]
    if len(identities) != len(set(identities)):
        raise ValueError("Video clip identifiers must be unique")
    rate = Fraction(apr["format"]["fps_num"], apr["format"]["fps_den"])
    for track in timeline.video_layers:
        for item in track.items:
            asset = assets.get(str(item.asset_id))
            if not asset or asset["media_type"] not in {"image", "video"}:
                raise ValueError("Video layer requires an owned image or video")
            if item.start_frame + item.duration_frames > end:
                raise ValueError("Video layer exceeds the film duration")
            if item.asset_version_id:
                version = versions.get(str(item.asset_version_id))
                if not version or version["asset_id"] != str(item.asset_id):
                    raise ValueError("Video layer version does not match its asset")
            if asset["media_type"] == "image":
                if item.source_start_frame:
                    raise ValueError("Still images start at source frame zero")
                continue
            seconds = asset.get("duration_seconds")
            if seconds is None and asset.get("duration_frames") is not None:
                source_rate = Fraction(
                    asset.get("fps_num") or rate.numerator, asset.get("fps_den") or rate.denominator
                )
                seconds = float(Fraction(asset["duration_frames"], 1) / source_rate)
            if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("Video layer has no verified duration")
            if (
                float(Fraction(item.source_start_frame + item.duration_frames, 1) / rate)
                > seconds + 1e-6
            ):
                raise ValueError("Video layer exceeds its source duration")


def transition_overlaps(timeline):
    """Validate neighboring cuts and return overlap frames keyed by incoming clip."""
    clips = timeline.tracks[0].items
    pairs = {(str(a.id), str(b.id)): (a, b) for a, b in zip(clips, clips[1:])}
    seen, ids, result = set(), set(), {}
    for transition in timeline.transitions:
        pair = (str(transition.from_clip_id), str(transition.to_clip_id))
        if pair not in pairs or pair in seen or transition.id in ids:
            raise ValueError("Transitions must uniquely join adjacent clips")
        first, second = pairs[pair]
        if transition.duration_frames * 2 > min(first.duration_frames, second.duration_frames):
            raise ValueError("Transition exceeds half the shorter clip duration")
        seen.add(pair)
        ids.add(transition.id)
        result[pair[1]] = transition.duration_frames
    return result


def build_composition(apr) -> CompositionSpec:
    from app.project_engine import find
    from app.timeline_service import composition_request

    audio = composition_request(apr)
    items = []
    for placement in apr["timeline"]["tracks"][0]["items"]:
        shot = find(apr["shots"], placement["shot_id"])
        if not shot["active_asset_version_id"]:
            raise DomainError("MISSING_ASSET", "Generate every timeline shot first", 409)
        version = find(apr["asset_versions"], shot["active_asset_version_id"])
        if version["status"] == "stale" or shot["status"] in {"stale", "needs_review"}:
            raise DomainError("STALE_ASSET", "Regenerate or review stale timeline shots", 409)
        asset = find(apr["assets"], version["asset_id"])
        items.append({**placement, "asset_id": asset["id"], "sha256": asset["sha256"]})
    if not items:
        raise DomainError("EMPTY_TIMELINE", "Add shots to the timeline first", 422)
    layers = []
    for track in apr["timeline"].get("video_layers", []):
        if track["hidden"]:
            continue
        resolved = []
        for item in track["items"]:
            asset = find(apr["assets"], item["asset_id"])
            if item.get("asset_version_id"):
                version = find(apr["asset_versions"], item["asset_version_id"])
                if version["status"] == "stale":
                    raise DomainError("STALE_ASSET", "Review the stale video layer version", 409)
            resolved.append({**item, "sha256": asset["sha256"], "media_type": asset["media_type"]})
        layers.append({**track, "items": resolved})
    return {
        "items": items,
        "video_layers": layers,
        "transitions": apr["timeline"].get("transitions", []),
        "total_frames": backbone_duration(apr["timeline"]),
        "format": apr["format"],
        **audio,
        "adapter_version": "0.5.0",
        "composition_version": "0.5.0",
    }
