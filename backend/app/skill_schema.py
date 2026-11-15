"""Portable creative guidance and bounded local skill-package inputs."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from app.schemas import RevisionRequest, Schema

Stage = Literal[
    "story", "continuity", "shots", "image", "video", "audio", "editing", "review", "delivery"
]
STAGES = {
    "story": "剧本编排",
    "continuity": "角色与连续性",
    "shots": "镜头设计",
    "image": "图像与视觉",
    "video": "视频生成",
    "audio": "对白、配音与音乐",
    "editing": "剪辑与节奏",
    "review": "审片",
    "delivery": "成片交付",
}
MAX_UPLOAD = 16 * 1024**2
MAX_UNPACKED = 32 * 1024**2
MAX_FILE = 8 * 1024**2
MAX_FILES = 256
MAX_GUIDANCE = 24_000
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SkillFile(Schema):
    path: str = Field(min_length=1, max_length=240)
    content_base64: str = Field(max_length=12 * 1024**2)


class SkillImport(Schema):
    files: list[SkillFile] = Field(min_length=1, max_length=MAX_FILES)
    stages: list[Stage] = Field(min_length=1, max_length=9)
    source: str = Field(default="", max_length=1000)


class GuideResource(Schema):
    path: str = Field(min_length=1, max_length=240)
    text: str = Field(max_length=12_000)


class GuidanceSnapshot(Schema):
    package_id: Digest
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    version: str = Field(max_length=100)
    source: str = Field(max_length=1000)
    instructions: str = Field(min_length=1, max_length=16_000)
    resources: list[GuideResource] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_context(self):
        if len(self.instructions) + sum(len(item.text) for item in self.resources) > MAX_GUIDANCE:
            raise ValueError("Selected guidance exceeds 24000 characters")
        if len({item.path for item in self.resources}) != len(self.resources):
            raise ValueError("Resource selections must be unique")
        return self


Guidance = dict[Stage, Annotated[list[GuidanceSnapshot], Field(max_length=3)]]


def validate_guidance(value):
    return TypeAdapter(Guidance).validate_python(value)


def compile_guidance(apr, stages):
    result = []
    used = set()
    total = 0
    for stage in stages:
        for guide in apr.get("production_guidance", {}).get(stage, []):
            identifier = guide["package_id"]
            key = (identifier, tuple(item["path"] for item in guide["resources"]))
            if key in used:
                continue
            total += len(guide["instructions"]) + sum(
                len(item["text"]) for item in guide["resources"]
            )
            if total > MAX_GUIDANCE * 3:
                from app.errors import DomainError

                raise DomainError(
                    "SKILL_CONTEXT_LIMIT", "Select less guidance for this operation", 422
                )
            used.add(key)
            result.append({"stage": stage, **guide})
    return result


def add_guidance(request, apr, stages):
    guides = compile_guidance(apr, stages)
    if guides:
        request["production_guidance"] = guides
    return request


class GuideSelection(Schema):
    package_id: Digest
    resources: list[Annotated[str, Field(max_length=240)]] = Field(
        default_factory=list, max_length=8
    )


class GuidanceUpdate(RevisionRequest):
    selections: dict[Stage, Annotated[list[GuideSelection], Field(max_length=3)]]


class ProductionCheckRequest(RevisionRequest):
    stage: Stage
    shot_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ScriptPreviewRequest(ProductionCheckRequest):
    package_id: Digest
    script_path: str = Field(min_length=1, max_length=240)
    arguments: list[
        Annotated[
            str, StringConstraints(strip_whitespace=False, max_length=2000, pattern=r"^[^\x00]*$")
        ]
    ] = Field(default_factory=list, max_length=50)
    timeout_seconds: int = Field(default=60, ge=5, le=300)


class ScriptApproval(RevisionRequest):
    fingerprint: Digest
