"""Bounded continuity documents and explicitly selected semantic field edits."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.schemas import RevisionRequest, Schema

ShortText = Annotated[str, Field(max_length=1000)]


class CharacterBible(Schema):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    invariant_traits: list[ShortText] = Field(default_factory=list, max_length=40)
    reference_asset_ids: list[UUID] = Field(default_factory=list, max_length=20)


class LocationBible(Schema):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    spatial_notes: str = Field(default="", max_length=5000)
    reference_asset_ids: list[UUID] = Field(default_factory=list, max_length=20)


class CharacterState(Schema):
    character_id: UUID
    costume: ShortText = ""
    pose: ShortText = ""
    emotion: ShortText = ""


class PropState(Schema):
    name: str = Field(min_length=1, max_length=200)
    description: ShortText = ""
    holder_character_id: UUID | None = None


class WorldState(Schema):
    id: UUID = Field(default_factory=uuid4)
    scene_id: UUID
    shot_id: UUID | None = None
    characters: list[CharacterState] = Field(default_factory=list, max_length=40)
    props: list[PropState] = Field(default_factory=list, max_length=40)
    environment: dict[Annotated[str, Field(min_length=1, max_length=100)], ShortText] = Field(
        default_factory=dict, max_length=30
    )


class ContinuityLink(Schema):
    id: UUID = Field(default_factory=uuid4)
    source_shot_id: UUID
    target_shot_id: UUID
    kind: Literal["character", "prop", "environment", "action", "continuity"] = "continuity"
    note: str = Field(default="", max_length=1000)


class ShotReferences(Schema):
    character_ids: list[UUID] = Field(default_factory=list, max_length=40)
    input_state_ref: UUID | None = None
    output_state_ref: UUID | None = None


class SceneLocation(Schema):
    location_id: UUID | None = None


ENTITY_MODELS = {
    "character": CharacterBible,
    "location": LocationBible,
    "world_state": WorldState,
    "link": ContinuityLink,
    "shot_refs": ShotReferences,
    "scene_location": SceneLocation,
}


class ContinuityChange(RevisionRequest):
    entity_type: Literal[
        "character", "location", "world_state", "shot_refs", "scene_location", "link"
    ]
    entity_id: UUID | None = None
    delete: bool = False
    value: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def typed_value(self):
        if self.delete:
            if (
                self.entity_id is None
                or self.value
                or self.entity_type in {"shot_refs", "scene_location"}
            ):
                raise ValueError("Deletion requires an entity ID and no replacement value")
            return self
        value = dict(self.value)
        if self.entity_type in {"shot_refs", "scene_location"}:
            if self.entity_id is None:
                raise ValueError("A shot or scene ID is required")
        elif self.entity_id:
            if value.get("id") and str(value["id"]) != str(self.entity_id):
                raise ValueError("Replacement ID differs from target")
            value["id"] = str(self.entity_id)
        self.value = ENTITY_MODELS[self.entity_type].model_validate(value).model_dump(mode="json")
        if self.entity_type not in {"shot_refs", "scene_location"}:
            self.entity_id = UUID(self.value["id"])
        return self


class SemanticCamera(Schema):
    shot_size: Literal["wide", "medium", "close_up", "detail"] | None = None
    movement: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("Choose at least one camera field")
        return self


class SemanticFields(Schema):
    action: str | None = Field(default=None, max_length=5000)
    emotion: str | None = Field(default=None, max_length=500)
    camera: SemanticCamera | None = None
    duration_frames: int | None = Field(default=None, gt=0, le=1800, strict=True)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("Choose at least one explicit field")
        return self


class SemanticEditRequest(RevisionRequest):
    shot_ids: list[UUID] = Field(min_length=1, max_length=20)
    mode: Literal["structured", "llm"] = "structured"
    fields: SemanticFields | None = None
    message: str = Field(default="", max_length=5000)
    allow_remote: bool = False

    @model_validator(mode="after")
    def valid_scope(self):
        if len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValueError("Selected shots must be unique")
        if self.mode == "structured" and self.fields is None:
            raise ValueError("Structured mode needs explicit field values")
        if self.mode == "llm" and (not self.message or self.fields is not None):
            raise ValueError("LLM mode needs an instruction and no structured fields")
        return self


class SemanticShotEdit(Schema):
    shot_id: UUID
    fields: SemanticFields


class SemanticProposal(Schema):
    edits: list[SemanticShotEdit] = Field(min_length=1, max_length=20)
