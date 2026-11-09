"""Validated composition edits and explicit audio-first timing proposals."""

import copy
import hashlib
import json
import math
from fractions import Fraction
from uuid import UUID

from pydantic import Field, model_validator

from app.errors import DomainError
from app.timeline_models import (
    MAX_AUDIO_ITEMS,
    MAX_FRAMES,
    Caption,
    TimelineData,
    TimelineMarker,
    TimelineModel,
    TimelineTrack,
)


def validate_timeline(apr: dict) -> None:
    timeline = TimelineData.model_validate(apr.get("timeline", {}))
    tracks = timeline.tracks
    if tracks[0].type != "video" or any(t.type != "audio" for t in tracks[1:]):
        raise ValueError("One video backbone must be first, followed by audio tracks")
    if len({t.id for t in tracks}) != len(tracks):
        raise ValueError("Duplicate track identifiers")
    shots = {str(s["id"]): s for s in apr["shots"]}
    assets = {str(a["id"]): a for a in apr["assets"]}
    end = 0
    selected = set()
    for item in tracks[0].items:
        sid = str(item.shot_id)
        shot = shots.get(sid)
        if not shot or shot.get("status") == "archived" or sid in selected:
            raise ValueError("Timeline references a missing, archived or duplicate shot")
        if item.start_frame != end or item.duration_frames != shot["duration_frames"]:
            raise ValueError("Video backbone must be contiguous and use shot durations")
        selected.add(sid)
        end += item.duration_frames
    if end > MAX_FRAMES:
        raise ValueError("Timeline is too long")
    rate = Fraction(apr["format"]["fps_num"], apr["format"]["fps_den"])
    if not 1 <= rate <= 60:
        raise ValueError("Invalid project frame rate")
    if sum(len(t.items) for t in tracks[1:]) > MAX_AUDIO_ITEMS:
        raise ValueError("Too many audio placements")
    for track in tracks[1:]:
        for item in track.items:
            asset = assets.get(str(item.asset_id))
            if not asset or asset["media_type"] != "audio":
                raise ValueError("Audio placement references a missing or non-audio asset")
            duration = asset.get("duration_seconds")
            if (
                not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration <= 0
            ):
                raise ValueError("Audio asset has no verified finite duration")
            source_end = Fraction(item.source_start_frame + item.duration_frames, 1) / rate
            if float(source_end) > duration + 1 / 48000:
                raise ValueError("Audio placement exceeds source duration")
            if item.start_frame + item.duration_frames > end:
                raise ValueError("Audio placement exceeds the video backbone")
    for items in (timeline.captions, timeline.markers):
        if len({item.id for item in items}) != len(items):
            raise ValueError("Duplicate caption or marker identifier")
    if any(c.start_frame + c.duration_frames > end for c in timeline.captions):
        raise ValueError("Caption exceeds the video backbone")
    if any(m.frame > end for m in timeline.markers):
        raise ValueError("Marker exceeds the video backbone")


def composition_request(apr: dict) -> dict:
    validate_timeline(apr)
    timeline = TimelineData.model_validate(apr["timeline"])
    assets = {str(a["id"]): a for a in apr["assets"]}
    audio_items = []
    audio_tracks = []
    for track in timeline.tracks[1:]:
        audio_tracks.append({"id": track.id, "muted": track.muted, "gain_db": track.gain_db})
        for item in track.items:
            asset = assets[str(item.asset_id)]
            audio_items.append(
                {
                    **item.model_dump(mode="json"),
                    "sha256": asset["sha256"],
                    "track_id": track.id,
                    "track_gain_db": track.gain_db,
                    "muted": track.muted,
                }
            )
    return {
        "audio_policy": "mix" if audio_items else "silent",
        "audio_items": audio_items,
        "audio_tracks": audio_tracks,
        "captions": [c.model_dump(mode="json") for c in timeline.captions],
        "markers": [m.model_dump(mode="json") for m in timeline.markers],
        "composition_version": "0.4.0",
    }


class CompositionUpdate(TimelineModel):
    base_revision: int = Field(ge=1)
    audio_tracks: list[TimelineTrack] = Field(default_factory=list, max_length=15)
    captions: list[Caption] = Field(default_factory=list, max_length=500)
    markers: list[TimelineMarker] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def audio_only(self):
        if any(t.type != "audio" for t in self.audio_tracks):
            raise ValueError("Composition updates contain audio tracks only")
        return self


class AnchorRequest(TimelineModel):
    base_revision: int = Field(ge=1)
    boundary_shot_ids: list[UUID] = Field(min_length=1, max_length=99)
    marker_ids: list[UUID] | None = Field(default=None, max_length=1000)
    bpm: float | None = Field(default=None, ge=20, le=300)
    origin_frame: int = Field(default=0, ge=0, le=MAX_FRAMES)
    allow_protected: bool = False
    approved: bool = False
    preview_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def preview_anchors(apr: dict, body: AnchorRequest) -> dict:
    if apr["revision"] != body.base_revision:
        raise DomainError("REVISION_CONFLICT", "Project changed. Refresh and preview again.", 409)
    validate_timeline(apr)
    timeline = TimelineData.model_validate(apr["timeline"])
    items = timeline.tracks[0].items
    shots = {str(s["id"]): s for s in apr["shots"]}
    total = sum(i.duration_frames for i in items)
    boundaries = [0] + [i.start_frame + i.duration_frames for i in items]
    wanted = [str(x) for x in body.boundary_shot_ids]
    available = {str(i.shot_id): n + 1 for n, i in enumerate(items[:-1])}
    if len(set(wanted)) != len(wanted) or any(sid not in available for sid in wanted):
        raise DomainError("INVALID_BOUNDARY", "Choose distinct internal shot boundaries", 422)
    marker_ids = {str(x) for x in body.marker_ids} if body.marker_ids is not None else None
    known_ids = {str(m.id) for m in timeline.markers}
    if marker_ids is not None and not marker_ids <= known_ids:
        raise DomainError("NOT_FOUND", "Anchor marker does not belong to this project", 404)
    anchors = {
        m.frame
        for m in timeline.markers
        if m.kind in {"beat", "dialogue"} and (marker_ids is None or str(m.id) in marker_ids)
    }
    if body.bpm is not None:
        rate = Fraction(apr["format"]["fps_num"], apr["format"]["fps_den"])
        step = rate * 60 / Fraction(str(body.bpm))
        count = int((total - body.origin_frame) / step) + 1
        if count > 1000:
            raise DomainError("TOO_MANY_ANCHORS", "BPM grid exceeds 1000 anchors", 422)
        anchors.update(round(body.origin_frame + n * step) for n in range(max(0, count)))
    anchors = sorted(a for a in anchors if 0 < a < total)
    if not anchors:
        raise DomainError("MISSING_ANCHORS", "Add beat/dialogue markers or a BPM grid first", 422)
    protected = set()
    snapped = []
    for sid in sorted(wanted, key=lambda x: available[x]):
        position = available[sid]
        adjacent = [str(items[position - 1].shot_id), str(items[position].shot_id)]
        guarded = [
            s
            for s in adjacent
            if shots[s].get("locked") or shots[s].get("status") in {"approved", "needs_review"}
        ]
        protected.update(guarded)
        if guarded and not body.allow_protected:
            continue
        valid = [
            a
            for a in anchors
            if boundaries[position - 1] < a < boundaries[position + 1]
            and a - boundaries[position - 1] <= 1800
            and boundaries[position + 1] - a <= 1800
        ]
        if not valid:
            continue
        before = boundaries[position]
        after = min(valid, key=lambda a: (abs(a - before), a))
        boundaries[position] = after
        if before != after:
            snapped.append({"shot_id": sid, "before_frame": before, "after_frame": after})
    changes = []
    for n, item in enumerate(items):
        duration = boundaries[n + 1] - boundaries[n]
        if duration != item.duration_frames:
            changes.append(
                {"shot_id": str(item.shot_id), "before": item.duration_frames, "after": duration}
            )
    from app.continuity import impacted_shots

    impacted = impacted_shots(apr, {c["shot_id"] for c in changes})
    protected.update(
        s["id"]
        for s in apr["shots"]
        if s["id"] in impacted and (s["locked"] or s["status"] in {"approved", "needs_review"})
    )
    result = {
        "base_revision": apr["revision"],
        "changes": changes,
        "boundaries": snapped,
        "affected_shot_ids": impacted,
        "protected_shot_ids": sorted(protected),
        "total_frames": total,
        "anchors": anchors,
    }
    token_body = body.model_dump(mode="json", exclude={"approved", "preview_token"})
    result["preview_token"] = _digest({"request": token_body, "result": result})
    return result


class TimelineService:
    def __init__(self, database):
        from app.project_engine import ProjectEngine

        self.database = database
        self.engine = ProjectEngine(database)

    def update(self, project_id, body: CompositionUpdate):
        def apply(apr, connection):
            before = composition_request(apr)
            apr["timeline"]["tracks"] = [apr["timeline"]["tracks"][0]] + [
                t.model_dump(mode="json") for t in body.audio_tracks
            ]
            apr["timeline"]["captions"] = [c.model_dump(mode="json") for c in body.captions]
            apr["timeline"]["markers"] = [m.model_dump(mode="json") for m in body.markers]
            try:
                after = composition_request(apr)
            except ValueError as error:
                raise DomainError("INVALID_TIMELINE", str(error), 422) from error
            if before != after:
                apr["timeline"]["version"] += 1
                apr["render_status"] = "stale"

        return self.engine.mutate(
            project_id, body.base_revision, "Edit audio, captions and anchors", apply
        )

    def preview(self, project_id, body: AnchorRequest):
        with self.database.transaction() as connection:
            apr = self.engine.load(connection, project_id)
            return preview_anchors(apr, body)

    def commit(self, project_id, body: AnchorRequest):
        if not body.approved or not body.preview_token:
            raise DomainError("APPROVAL_REQUIRED", "Preview and approve timing changes first", 409)

        def apply(apr, connection):
            preview = preview_anchors(apr, body)
            if body.preview_token != preview["preview_token"]:
                raise DomainError("PREVIEW_CHANGED", "Timing proposal changed. Preview again.", 409)
            if not preview["changes"]:
                raise DomainError(
                    "NO_TIMING_CHANGES", "This proposal does not change shot timing", 409
                )
            from app.project_engine import rebuild_timeline

            by_id = {s["id"]: s for s in apr["shots"]}
            for change in preview["changes"]:
                by_id[change["shot_id"]]["duration_frames"] = change["after"]
            self.engine.invalidate(connection, apr, preview["affected_shot_ids"])
            rebuild_timeline(apr)
            self.engine.approve(
                connection, project_id, "audio_anchor_timing", copy.deepcopy(preview)
            )

        return self.engine.mutate(
            project_id, body.base_revision, "Approve audio-first timing plan", apply
        )
