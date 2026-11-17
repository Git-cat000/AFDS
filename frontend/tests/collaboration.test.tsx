import { describe, expect, it } from "vitest";
import { emptyBoard } from "../src/canvas-board";
import {
  diffWorkspace,
  mergeWorkspaceDraft,
} from "../src/workspace-operations";

describe("collaborative canvas drafts", () => {
  it("sends only changed entities with their observed versions", () => {
    const base = emptyBoard();
    base.nodes = [{ id: "note-a", kind: "note", text: "DEMO" }];
    const draft = { ...base, positions: { "note-a": { x: 2, y: 4 } } };
    const operations = diffWorkspace(base, draft, { "node/note-a": 7 });
    expect(operations).toHaveLength(1);
    expect(operations[0]).toMatchObject({
      entity_id: "note-a",
      kind: "set_node",
      expected_entity_version: 7,
    });
    expect(operations[0].operation_id).toBeTruthy();
  });

  it("merges remote disjoint changes while preserving a draft and viewport", () => {
    const base = emptyBoard();
    base.nodes = [{ id: "note-a", kind: "note", text: "DEMO A" }];
    const draft = { ...base, x: 999, positions: { "note-a": { x: 3, y: 5 } } };
    const remote = {
      ...base,
      nodes: [
        ...base.nodes,
        { id: "note-b", kind: "note" as const, text: "DEMO B" },
      ],
    };
    const merged = mergeWorkspaceDraft(base, draft, remote);
    expect(merged.conflicts).toEqual([]);
    expect(merged.board.positions["note-a"]).toEqual({ x: 3, y: 5 });
    expect(merged.board.nodes).toHaveLength(2);
    expect(merged.board.x).toBe(999);
  });

  it("retains the local draft and identifies simultaneous entity edits", () => {
    const base = emptyBoard();
    base.nodes = [{ id: "note-a", kind: "note", text: "DEMO A" }];
    const draft = { ...base, positions: { "note-a": { x: 3, y: 5 } } };
    const remote = {
      ...base,
      nodes: [{ id: "note-a", kind: "note" as const, text: "DEMO remote" }],
    };
    expect(mergeWorkspaceDraft(base, draft, remote).conflicts).toEqual([
      "node/note-a",
    ]);
    expect(draft.nodes[0]).toMatchObject({ text: "DEMO A" });
  });

  it("removes edges before dependent nodes", () => {
    const base = emptyBoard();
    base.nodes = [
      { id: "note-a", kind: "note", text: "A" },
      { id: "note-b", kind: "note", text: "B" },
    ];
    base.edges = [
      { id: "link", source: "note-a", target: "note-b", label: "DEMO" },
    ];
    const draft = { ...base, nodes: [base.nodes[0]], edges: [] };
    expect(diffWorkspace(base, draft, {}).map((op) => op.kind)).toEqual([
      "remove_edge",
      "remove_node",
    ]);
  });
});
