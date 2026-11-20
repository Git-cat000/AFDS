"""Public model-routing policies and evidence, independent of connection secrets."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.provider_settings import ProviderKind
from app.schemas import Schema

Cost = Annotated[Decimal, Field(ge=0, le=1_000_000_000, decimal_places=6)]
CandidateId = Annotated[
    str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
]
Capability = Literal[
    "storyboard",
    "text_to_video",
    "text_to_image",
    "text_to_speech",
    "transcription",
    "visual_review",
]
ReferenceRole = Literal["reference_image", "start_image", "end_image"]


class QualityObservation(Schema):
    grade: int = Field(ge=0, le=5)
    source_type: Literal["manual", "benchmark"]
    source: str = Field(min_length=1, max_length=500)


class LatencyObservation(Schema):
    p95_ms: int = Field(ge=0, le=86_400_000)
    source: str = Field(min_length=1, max_length=500)


class ProviderCandidate(Schema):
    id: CandidateId
    label: str = Field(min_length=1, max_length=120)
    kind: ProviderKind
    config_revision: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=200)
    capabilities: list[Capability] = Field(min_length=1, max_length=6)
    enabled: bool = False
    estimated_cost: Cost | None = None
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    all_projects: bool = True
    project_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    quality: QualityObservation | None = None
    latency: LatencyObservation | None = None
    priority: int = Field(default=0, ge=0, le=10_000)
    health: Literal["untested", "healthy", "unhealthy"] = "untested"
    is_mock: bool = False
    timestamps: bool = False
    reference_roles: list[ReferenceRole] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def valid_lists(self):
        for values in (self.capabilities, self.project_ids, self.reference_roles):
            if len(values) != len(set(values)):
                raise ValueError("Candidate lists must contain unique values")
        if self.is_mock and self.estimated_cost != Decimal(0):
            raise ValueError("Built-in Mock candidates have a known zero charge")
        return self


class RoutingPolicy(Schema):
    strategy: Literal["preferred", "lowest_estimated_cost", "quality_first", "balanced"] = (
        "preferred"
    )
    preferred_ids: list[CandidateId] = Field(default_factory=list, max_length=100)
    allowed_ids: list[CandidateId] | None = Field(default=None, min_length=1, max_length=100)
    minimum_quality: int | None = Field(default=None, ge=0, le=5)
    max_latency_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    unknown_quality: Literal["allow", "exclude"] = "allow"
    unknown_latency: Literal["allow", "exclude"] = "allow"
    unknown_cost: Literal["inherit", "exclude"] = "inherit"
    max_estimated_cost: Cost | None = None
    allow_fallback: bool = False

    @model_validator(mode="after")
    def unique_candidates(self):
        for values in (self.preferred_ids, self.allowed_ids or []):
            if len(values) != len(set(values)):
                raise ValueError("Choose each candidate once")
        return self


class RoutingRequirements(Schema):
    units: int = Field(default=1, ge=1, le=1000)
    timestamps: bool = False
    remote_only: bool = False
    reference_roles: list[ReferenceRole] = Field(default_factory=list, max_length=3)


class CandidateEvaluation(Schema):
    candidate_id: CandidateId
    config_revision: int
    model: str
    estimated_cost: Annotated[Decimal, Field(ge=0, le=1_000_000_000_000, decimal_places=6)] | None
    currency: str
    rejections: list[str]
    quality: QualityObservation | None = None
    latency: LatencyObservation | None = None


class RankedCandidates(Schema):
    selected_id: CandidateId
    eligible_ids: list[CandidateId]
    evaluations: list[CandidateEvaluation]
