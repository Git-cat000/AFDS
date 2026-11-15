import { expect, it } from "vitest";
import {
  addMaterial,
  addNote,
  connectItems,
  loadBoard,
  removeItems,
  visibleItems,
  recordBoard,
  undoBoard,
  redoBoard,
  worldPoint,
  emptyBoard,
} from "../src/canvas-board";
import type { Project } from "../src/types";

const project = {
  project_id: "p",
  shots: [{ id: "shot", status: "ready" }],
  assets: [
    { id: "image", media_type: "image" },
    { id: "video", media_type: "video" },
    { id: "audio", media_type: "audio" },
  ],
} as unknown as Project;

it("loads legacy layouts and filters foreign or malformed material connections", () => {
  const board = loadBoard(
    project,
    JSON.stringify({
      x: 12,
      y: -3,
      zoom: 0.8,
      positions: { shot: { x: -300, y: 80 } },
      nodes: [
        { id: "asset-image", kind: "asset", assetId: "image" },
        { id: "foreign", kind: "asset", assetId: "other-project" },
        { id: "constructor", kind: "note", text: "invalid stored identity" },
      ],
      edges: [
        { id: "line", source: "asset-image", target: "shot", label: "DEMO" },
        { id: "bad", source: "foreign", target: "shot" },
      ],
    }),
  );
  expect(board.positions.shot).toEqual({ x: -300, y: 80 });
  expect(board.nodes).toHaveLength(1);
  expect(board.edges).toHaveLength(1);
  expect(board.zoom).toBe(0.8);
});
it("places mixed owned media and notes at arbitrary coordinates and rejects foreign assets", () => {
  let board = emptyBoard();
  for (const id of ["image", "video", "audio"])
    board = addMaterial(board, project, id, { x: -200, y: 500 }, `asset-${id}`);
  board = addNote(board, { x: 100, y: -80 }, "DEMO idea", "note-one");
  expect(visibleItems(board, project).map((item) => item.kind)).toEqual([
    "shot",
    "asset",
    "asset",
    "asset",
    "note",
  ]);
  expect(addMaterial(board, project, "foreign", { x: 0, y: 0 }, "x")).toBe(
    board,
  );
  expect(board.positions["asset-audio"]).toEqual({ x: -200, y: 500 });
});
it("supports connections, removes dangling lines and restores removed nodes by undo", () => {
  const first = addMaterial(
    emptyBoard(),
    project,
    "image",
    { x: -300, y: 20 },
    "asset-image",
  );
  const linked = connectItems(first, project, "asset-image", "shot", "line");
  expect(
    connectItems(linked, project, "asset-image", "shot", "duplicate"),
  ).toBe(linked);
  expect(connectItems(linked, project, "shot", "shot", "self")).toBe(linked);
  expect(connectItems(linked, project, "foreign", "shot", "foreign")).toBe(
    linked,
  );
  const removed = removeItems(linked, project, ["asset-image"], []);
  expect(removed.nodes).toHaveLength(0);
  expect(removed.edges).toHaveLength(0);
  const history = recordBoard(
    { past: [], present: linked, future: [] },
    removed,
  );
  const undone = undoBoard(history);
  expect(undone.present.nodes).toHaveLength(1);
  expect(undone.present.edges).toHaveLength(1);
  expect(redoBoard(undone).present.nodes).toHaveLength(0);
});
it("keeps model reference inputs in APR when a shot placement is removed from the board", () => {
  const source = {
    ...project,
    shots: [
      { ...project.shots[0], reference_images: { reference_image: "image" } },
    ],
  };
  const board = removeItems(emptyBoard(), source, ["shot"], []);
  expect(visibleItems(board, source)).toHaveLength(0);
  expect(source.shots[0].reference_images).toEqual({
    reference_image: "image",
  });
});
it("converts viewport drops correctly after pan and zoom, including negative space", () => {
  expect(worldPoint({ x: 100, y: 60 }, { x: 300, y: 100, zoom: 0.5 })).toEqual({
    x: -400,
    y: -80,
  });
});
