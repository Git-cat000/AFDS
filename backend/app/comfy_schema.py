"""Validated API-format workflows; AFDS never executes node code."""

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas import Schema


class NodeBinding(Schema):
    node_id: str = Field(min_length=1, max_length=100)
    input_name: str = Field(min_length=1, max_length=100)


class WorkflowTemplate(Schema):
    graph: dict[str, dict[str, Any]] = Field(min_length=1, max_length=300)
    bindings: dict[Literal["prompt", "seed", "width", "height", "frames", "fps"], NodeBinding]
    output_node: str = Field(min_length=1, max_length=100)
    output_field: Literal["images", "gifs", "videos"] = "images"
    media_type: Literal["image", "video"] = "image"
    seed: int = Field(default=0, ge=0, le=2**53 - 1)

    @model_validator(mode="after")
    def valid_graph(self):
        if len(json.dumps(self.graph)) > 2_000_000:
            raise ValueError("Workflow exceeds size limit")
        if self.output_node not in self.graph or "prompt" not in self.bindings:
            raise ValueError("Select an output node and prompt binding")
        for node_id, node in self.graph.items():
            if (
                len(node_id) > 100
                or not isinstance(node.get("class_type"), str)
                or not node["class_type"]
                or not isinstance(node.get("inputs"), dict)
            ):
                raise ValueError("Use an API-format workflow, not the editor JSON")
            for value in node["inputs"].values():
                if isinstance(value, list) and (
                    len(value) != 2
                    or not isinstance(value[0], str)
                    or value[0] not in self.graph
                    or type(value[1]) is not int
                    or value[1] < 0
                ):
                    raise ValueError("Invalid workflow node link")
        targets = set()
        for binding in self.bindings.values():
            node = self.graph.get(binding.node_id, {})
            value = node.get("inputs", {}).get(binding.input_name)
            target = (binding.node_id, binding.input_name)
            if value is None or isinstance(value, (list, dict, bool)) or target in targets:
                raise ValueError("Map each input to a distinct existing literal node input")
            targets.add(target)
        return self
