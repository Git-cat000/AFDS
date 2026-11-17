from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from app.workspace_schema import AssetNode, NoteNode, Point, WorkspaceEdge, WorkspaceModel


class NodeValue(WorkspaceModel):
    node: Annotated[AssetNode | NoteNode, Field(discriminator="kind")] | None = None
    position: Point | None = None
    hidden: bool = False


class WorkspaceOperation(WorkspaceModel):
    operation_id: UUID
    entity_id: str = Field(min_length=1, max_length=200)
    expected_entity_version: int = Field(ge=0)
    kind: Literal[
        "set_node",
        "add_node",
        "move_node",
        "edit_note",
        "remove_node",
        "set_edge",
        "upsert_edge",
        "remove_edge",
        "set_hidden",
    ]
    value: JsonValue = None

    @model_validator(mode="after")
    def valid_value(self):
        if self.kind == "set_node":
            value = NodeValue.model_validate(self.value)
            if value.node and value.node.id != self.entity_id:
                raise ValueError("Node identity must match the operation")
            self.value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
        elif self.kind == "add_node":
            value = NodeValue.model_validate({"node": self.value})
            if value.node is None or value.node.id != self.entity_id:
                raise ValueError("Node identity must match the operation")
            self.value = value.node.model_dump(mode="json", by_alias=True)
        elif self.kind == "move_node":
            self.value = Point.model_validate(self.value).model_dump()
        elif self.kind == "edit_note":
            if not isinstance(self.value, str) or len(self.value) > 8000:
                raise ValueError("Note text must be a bounded string")
        elif self.kind in {"set_edge", "upsert_edge"}:
            edge = WorkspaceEdge.model_validate(self.value)
            if edge.id != self.entity_id:
                raise ValueError("Edge identity must match the operation")
            self.value = edge.model_dump()
        elif self.kind == "set_hidden":
            if not isinstance(self.value, bool):
                raise ValueError("Hidden state must be boolean")
        elif self.value is not None:
            raise ValueError("Removal operations have no value")
        return self


class OperationBatch(WorkspaceModel):
    operations: list[WorkspaceOperation] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.operation_id for item in self.operations}) != len(self.operations):
            raise ValueError("Operation identities must be unique")
        return self


class PresenceUpdate(WorkspaceModel):
    entity_id: str | None = Field(default=None, min_length=1, max_length=200)


class UndoBatch(WorkspaceModel):
    operation_ids: list[UUID] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_ids(self):
        if len(set(self.operation_ids)) != len(self.operation_ids):
            raise ValueError("Operation identities must be unique")
        return self
