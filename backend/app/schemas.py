from fractions import Fraction
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_id() -> str:
    return str(uuid4())


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Format(Schema):
    fps_num: int = Field(default=24, ge=1, le=120000)
    fps_den: int = Field(default=1, ge=1, le=1001)
    width: int = Field(default=1280, ge=160, le=1920, multiple_of=2)
    height: int = Field(default=720, ge=90, le=1080, multiple_of=2)

    @model_validator(mode="after")
    def valid_rate(self):
        if not 1 <= self.fps_num / self.fps_den <= 60:
            raise ValueError("Frame rate must be between 1 and 60 fps")
        return self

    def seconds(self, frames: int) -> Fraction:
        return Fraction(frames * self.fps_den, self.fps_num)


class Camera(Schema):
    shot_size: Literal["wide", "medium", "close_up", "detail"] = "wide"
    movement: str = Field(default="static", max_length=200)


class Shot(Schema):
    id: UUID = Field(default_factory=uuid4)
    scene_id: UUID
    order: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    duration_frames: int = Field(default=120, gt=0, le=1800)
    camera: Camera = Field(default_factory=Camera)
    action: str = Field(default="", max_length=5000)
    emotion: str = Field(default="", max_length=500)
    character_ids: list[UUID] = Field(default_factory=list)
    input_state_ref: UUID | None = None
    output_state_ref: UUID | None = None
    status: Literal[
        "draft",
        "planned",
        "generating",
        "ready",
        "approved",
        "failed",
        "needs_review",
        "archived",
        "stale",
    ] = "draft"
    locked: bool = False
    active_asset_version_id: UUID | None = None


class Scene(Schema):
    id: UUID = Field(default_factory=uuid4)
    order: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    location_id: UUID | None = None
    shot_ids: list[UUID] = Field(default_factory=list)


class Placement(Schema):
    shot_id: UUID
    start_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)


class Track(Schema):
    id: str = "v1"
    type: Literal["video", "audio"] = "video"
    items: list[Placement] = Field(default_factory=list)


class Timeline(Schema):
    version: int = 1
    tracks: list[Track] = Field(default_factory=lambda: [Track()])
    markers: list[dict[str, Any]] = Field(default_factory=list)


class ProjectCreate(Schema):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=10000)
    format: Format = Field(default_factory=Format)
    style_bible: dict[str, Any] = Field(default_factory=dict)


class Asset(Schema):
    id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: Literal["video", "image", "audio"]
    size_bytes: int = Field(gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    codec: str | None = None
    fps_num: int | None = None
    fps_den: int | None = None
    duration_frames: int | None = None
    duration_seconds: float | None = None
    provenance: dict[str, Any]
    license: str
    source: str
    consent: str


class AssetVersion(Schema):
    id: UUID
    shot_id: UUID
    asset_id: UUID
    poster_asset_id: UUID
    generated_by_job_id: UUID
    status: Literal["ready", "stale"]
    created_at: float


class Character(Schema):
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    invariant_traits: list[str] = Field(default_factory=list)
    reference_asset_ids: list[UUID] = Field(default_factory=list)


class Location(Schema):
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    spatial_notes: str = ""
    reference_asset_ids: list[UUID] = Field(default_factory=list)


class APR(ProjectCreate):
    apr_schema_version: Literal["0.1.0"] = "0.1.0"
    project_id: UUID = Field(default_factory=uuid4)
    revision: int = Field(default=1, ge=1)
    status: Literal["draft", "ready", "archived"] = "draft"
    characters: list[dict[str, Any]] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)
    timeline: Timeline = Field(default_factory=Timeline)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    asset_versions: list[dict[str, Any]] = Field(default_factory=list)
    render_asset_id: UUID | None = None
    render_status: str = "missing"
    budget: dict[str, Any] = Field(
        default_factory=lambda: {
            "budget_limit": 0,
            "spent": 0,
            "reserved": 0,
            "currency": "USD",
        }
    )

    @field_validator("assets")
    @classmethod
    def validate_assets(cls, values):
        return [Asset.model_validate(value).model_dump(mode="json") for value in values]

    @field_validator("characters")
    @classmethod
    def validate_characters(cls, values):
        return [Character.model_validate(value).model_dump(mode="json") for value in values]

    @field_validator("locations")
    @classmethod
    def validate_locations(cls, values):
        return [Location.model_validate(value).model_dump(mode="json") for value in values]

    @field_validator("asset_versions")
    @classmethod
    def validate_versions(cls, values):
        return [AssetVersion.model_validate(value).model_dump(mode="json") for value in values]

    @model_validator(mode="after")
    def references(self):
        scenes = {s.id for s in self.scenes}
        shots = {s.id: s for s in self.shots}
        chars = {str(c.get("id")) for c in self.characters}
        assets = {a["id"] for a in self.assets}
        versions = {v["id"]: v for v in self.asset_versions}
        locations = {str(item.get("id")) for item in self.locations}
        if len(assets) != len(self.assets) or len(versions) != len(self.asset_versions):
            raise ValueError("Duplicate asset identifiers")
        if len(scenes) != len(self.scenes) or len(shots) != len(self.shots):
            raise ValueError("Duplicate entity IDs")
        for shot in self.shots:
            if shot.scene_id not in scenes:
                raise ValueError("Shot references a missing scene")
            if any(str(c) not in chars for c in shot.character_ids):
                raise ValueError("Shot references a missing character")
            if shot.active_asset_version_id:
                version = versions.get(str(shot.active_asset_version_id))
                if not version or version["shot_id"] != str(shot.id):
                    raise ValueError("Missing or foreign asset version")
        for scene in self.scenes:
            if scene.location_id and str(scene.location_id) not in locations:
                raise ValueError("Missing location")
            expected = {s.id for s in self.shots if s.scene_id == scene.id}
            if set(scene.shot_ids) != expected or len(scene.shot_ids) != len(expected):
                raise ValueError("Scene shot references are inconsistent")
        for version in self.asset_versions:
            if version["poster_asset_id"] not in assets:
                raise ValueError("Missing poster asset")
            if version["asset_id"] not in assets or UUID(version["shot_id"]) not in shots:
                raise ValueError("Asset version reference is invalid")
        if self.render_asset_id and str(self.render_asset_id) not in assets:
            raise ValueError("Missing render asset")
        for track in self.timeline.tracks:
            end = 0
            for item in track.items:
                if item.shot_id not in shots:
                    raise ValueError("Timeline references a missing shot")
                if item.start_frame != end:
                    raise ValueError("v0.1 requires a contiguous non-overlapping timeline")
                if item.duration_frames != shots[item.shot_id].duration_frames:
                    raise ValueError("Timeline duration differs from the shot")
                end += item.duration_frames
        if len(self.timeline.tracks) != 1 or self.timeline.tracks[0].type != "video":
            raise ValueError("v0.1 renders one video track")
        return self


class RevisionRequest(Schema):
    base_revision: int = Field(ge=1)
    approved: bool = False


class PatchRequest(RevisionRequest):
    patch: dict[str, Any] = Field(min_length=1, max_length=20)
    reason: str = Field(default="Manual edit", max_length=1000)


class Operation(Schema):
    op: Literal["set"]
    entity_type: Literal["shot", "scene", "project"]
    entity_id: UUID
    field: str
    value: Any


class ChangeSet(RevisionRequest):
    reason: str = Field(default="Manual edit", max_length=1000)
    operations: list[Operation] = Field(min_length=1, max_length=100)


class DirectorRequest(RevisionRequest):
    message: str = Field(default="生成四镜头分镜", min_length=1, max_length=5000)
    provider_id: str | None = None
    allow_remote: bool = False


class TimelineRequest(RevisionRequest):
    shot_ids: list[UUID] = Field(min_length=1, max_length=100)


class GenerateRequest(RevisionRequest):
    shot_ids: list[UUID] = Field(min_length=1, max_length=100)
    provider_id: str | None = None
    capability: str = "text_to_video"
    allow_remote: bool = False


class MediaGenerateRequest(RevisionRequest):
    operation: Literal["generate_image", "generate_speech"]
    prompt: str = Field(min_length=1, max_length=5000)
    provider_id: str | None = None
    allow_remote: bool = False
    request_id: UUID | None = None


class RemoteRecoveryRequest(Schema):
    external_task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    approved: bool = False


class JobRetryRequest(Schema):
    restart_remote: bool = False
    allow_remote: bool = False
