"""Frame-based composition models, independent from the APR/schema module."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FRAMES = 216_000
MAX_AUDIO_ITEMS = 64


class TimelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class VideoPlacement(TimelineModel):
    shot_id: UUID
    start_frame: int = Field(ge=0, le=MAX_FRAMES)
    duration_frames: int = Field(gt=0, le=1800)


class AudioPlacement(TimelineModel):
    asset_id: UUID
    start_frame: int = Field(ge=0, le=MAX_FRAMES)
    source_start_frame: int = Field(default=0, ge=0, le=MAX_FRAMES)
    duration_frames: int = Field(gt=0, le=MAX_FRAMES)
    gain_db: float = Field(default=0, ge=-96, le=24)


class TimelineTrack(TimelineModel):
    id: str = Field(default="v1", pattern=r"^[A-Za-z0-9_-]{1,80}$")
    type: Literal["video", "audio"] = "video"
    name: str = Field(default="", max_length=120)
    muted: bool = False
    gain_db: float = Field(default=0, ge=-96, le=24)
    items: list[VideoPlacement | AudioPlacement] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def matching_items(self):
        expected = VideoPlacement if self.type == "video" else AudioPlacement
        if any(not isinstance(item, expected) for item in self.items):
            raise ValueError("Placement type differs from track type")
        if self.type == "audio" and len(self.items) > MAX_AUDIO_ITEMS:
            raise ValueError("Too many audio placements")
        if self.type == "video" and (self.muted or self.gain_db):
            raise ValueError("The video backbone does not contain source audio")
        return self


class Caption(TimelineModel):
    id: UUID = Field(default_factory=uuid4)
    start_frame: int = Field(ge=0, le=MAX_FRAMES)
    duration_frames: int = Field(gt=0, le=MAX_FRAMES)
    text: str = Field(min_length=1, max_length=2000)


class TimelineMarker(TimelineModel):
    id: UUID = Field(default_factory=uuid4)
    frame: int = Field(ge=0, le=MAX_FRAMES)
    label: str = Field(default="", max_length=200)
    kind: Literal["beat", "dialogue", "note"] = "note"


class TimelineData(TimelineModel):
    version: int = Field(default=1, ge=1)
    tracks: list[TimelineTrack] = Field(
        default_factory=lambda: [TimelineTrack()], min_length=1, max_length=16
    )
    captions: list[Caption] = Field(default_factory=list, max_length=500)
    markers: list[TimelineMarker] = Field(default_factory=list, max_length=1000)


Track = TimelineTrack
Placement = VideoPlacement
