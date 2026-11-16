"""Bounded shared canvas data, versioned independently from film content."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


class Point(WorkspaceModel):
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)


class AssetNode(WorkspaceModel):
    id: str = Field(pattern=r"^asset-[\w-]{1,154}$")
    kind: Literal["asset"]
    asset_id: UUID = Field(alias="assetId")


class NoteNode(WorkspaceModel):
    id: str = Field(pattern=r"^note-[\w-]{1,154}$")
    kind: Literal["note"]
    text: str = Field(max_length=8000)


class WorkspaceEdge(WorkspaceModel):
    id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    label: str = Field(default="关联", max_length=200)


class WorkspaceDocument(WorkspaceModel):
    version: Literal[2] = 2
    positions: dict[str, Point] = Field(default_factory=dict, max_length=2000)
    nodes: list[Annotated[AssetNode | NoteNode, Field(discriminator="kind")]] = Field(
        default_factory=list, max_length=1000
    )
    edges: list[WorkspaceEdge] = Field(default_factory=list, max_length=3000)
    hidden_shots: list[UUID] = Field(default_factory=list, alias="hiddenShots", max_length=1000)

    @model_validator(mode="after")
    def unique_identities(self):
        ids = [node.id for node in self.nodes]
        edge_ids = [edge.id for edge in self.edges]
        pairs = [(edge.source, edge.target) for edge in self.edges]
        if len(ids) != len(set(ids)) or len(edge_ids) != len(set(edge_ids)):
            raise ValueError("Canvas identities must be unique")
        if len(pairs) != len(set(pairs)) or any(a == b for a, b in pairs):
            raise ValueError("Canvas connections must be unique and join distinct items")
        if len(self.hidden_shots) != len(set(self.hidden_shots)):
            raise ValueError("Hidden shots must be unique")
        if len(self.model_dump_json(by_alias=True).encode()) > 8 * 1024**2:
            raise ValueError("Canvas document exceeds 8 MiB")
        return self


class WorkspaceUpdate(WorkspaceModel):
    base_workspace_revision: int = Field(ge=0)
    document: WorkspaceDocument


def workspace_document(document, apr):
    """Validate every reference against the owned project snapshot."""
    value = WorkspaceDocument.model_validate(document).model_dump(mode="json", by_alias=True)
    shots = {shot["id"] for shot in apr["shots"]}
    assets = {
        asset["id"] for asset in apr["assets"] if asset["media_type"] in {"image", "video", "audio"}
    }
    node_ids = {node["id"] for node in value["nodes"]}
    identities = shots | node_ids
    if node_ids & shots or set(value["positions"]) - identities:
        raise ValueError("Canvas positions must reference project items")
    if set(value["hiddenShots"]) - shots:
        raise ValueError("Hidden shots must belong to the project")
    if any(node["kind"] == "asset" and node["assetId"] not in assets for node in value["nodes"]):
        raise ValueError("Canvas material must belong to the project")
    if any(
        edge["source"] not in identities or edge["target"] not in identities
        for edge in value["edges"]
    ):
        raise ValueError("Canvas connection references a missing item")
    return value
