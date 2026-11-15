import type { Asset, Project, Shot } from "./types";

export type Point = { x: number; y: number };
export type BoardMaterial =
  | { id: string; kind: "asset"; assetId: string }
  | { id: string; kind: "note"; text: string };
export type BoardEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
};
export type CanvasBoard = {
  version: 2;
  x: number;
  y: number;
  zoom: number;
  positions: Record<string, Point>;
  nodes: BoardMaterial[];
  edges: BoardEdge[];
  hiddenShots: string[];
};
export type CanvasItem = { id: string; position: Point } & (
  | { kind: "shot"; shot: Shot }
  | { kind: "asset"; asset: Asset }
  | { kind: "note"; text: string }
);
export type BoardHistory = {
  past: CanvasBoard[];
  present: CanvasBoard;
  future: CanvasBoard[];
};

export const emptyBoard = (): CanvasBoard => ({
  version: 2,
  x: 36,
  y: 42,
  zoom: 1,
  positions: {},
  nodes: [],
  edges: [],
  hiddenShots: [],
});
export const worldPoint = (
  point: Point,
  view: { x: number; y: number; zoom: number },
): Point => ({
  x: (point.x - view.x) / view.zoom,
  y: (point.y - view.y) / view.zoom,
});
const finitePoint = (value: unknown): value is Point =>
  !!value &&
  typeof value === "object" &&
  "x" in value &&
  "y" in value &&
  Number.isFinite(value.x) &&
  Number.isFinite(value.y);
const supported = (asset: Asset) =>
  ["image", "video", "audio"].includes(asset.media_type);

export function visibleItems(
  board: CanvasBoard,
  project: Project,
): CanvasItem[] {
  const shots: CanvasItem[] = project.shots
    .filter((shot) => shot.status !== "archived")
    .map((shot, index) => ({
      id: shot.id,
      kind: "shot",
      shot,
      position: board.positions[shot.id] || {
        x: (index % 3) * 310,
        y: Math.floor(index / 3) * 390,
      },
    }));
  const assets = new Map(
    project.assets.filter(supported).map((asset) => [asset.id, asset]),
  );
  const materials: CanvasItem[] = [];
  for (const node of board.nodes) {
    const position = board.positions[node.id] || { x: 340, y: 420 };
    if (node.kind === "note")
      materials.push({ id: node.id, kind: "note", text: node.text, position });
    else {
      const asset = assets.get(node.assetId);
      if (asset)
        materials.push({ id: node.id, kind: "asset", asset, position });
    }
  }
  return [
    ...shots.filter((item) => !board.hiddenShots.includes(item.id)),
    ...materials,
  ];
}

export function loadBoard(project: Project, raw: string | null): CanvasBoard {
  const board = emptyBoard();
  try {
    const value = JSON.parse(raw || "null");
    if (!value || typeof value !== "object") return board;
    if (
      [value.x, value.y, value.zoom].every(Number.isFinite) &&
      value.zoom >= 0.05 &&
      value.zoom <= 4
    )
      Object.assign(board, { x: value.x, y: value.y, zoom: value.zoom });
    const assets = new Set(
      project.assets.filter(supported).map((asset) => asset.id),
    );
    const shots = new Set(project.shots.map((shot) => shot.id));
    const ids = new Set(shots);
    for (const node of Array.isArray(value.nodes) ? value.nodes : []) {
      if (
        !node ||
        typeof node.id !== "string" ||
        ids.has(node.id) ||
        !/^(asset|note)-[\w-]{1,154}$/.test(node.id)
      )
        continue;
      if (node.kind === "asset" && assets.has(node.assetId))
        board.nodes.push({ id: node.id, kind: "asset", assetId: node.assetId });
      else if (node.kind === "note" && typeof node.text === "string")
        board.nodes.push({ id: node.id, kind: "note", text: node.text });
      else continue;
      ids.add(node.id);
    }
    for (const [id, point] of Object.entries(value.positions || {})) {
      if (ids.has(id) && finitePoint(point))
        board.positions[id] = { x: point.x, y: point.y };
    }
    board.hiddenShots = Array.isArray(value.hiddenShots)
      ? value.hiddenShots.filter(
          (id: unknown) => typeof id === "string" && shots.has(id),
        )
      : [];
    const visible = new Set(
      visibleItems(board, project).map((item) => item.id),
    );
    const pairs = new Set<string>();
    const edgeIds = new Set<string>();
    for (const edge of Array.isArray(value.edges) ? value.edges : []) {
      if (
        !edge ||
        typeof edge.id !== "string" ||
        edgeIds.has(edge.id) ||
        !visible.has(edge.source) ||
        !visible.has(edge.target) ||
        edge.source === edge.target
      )
        continue;
      const pair = JSON.stringify([edge.source, edge.target]);
      if (pairs.has(pair)) continue;
      pairs.add(pair);
      edgeIds.add(edge.id);
      board.edges.push({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: typeof edge.label === "string" ? edge.label : "关联",
      });
    }
  } catch {
    return emptyBoard();
  }
  return board;
}

export function addMaterial(
  board: CanvasBoard,
  project: Project,
  assetId: string,
  point: Point,
  id = `asset-${crypto.randomUUID()}`,
): CanvasBoard {
  if (
    !project.assets.some((asset) => asset.id === assetId && supported(asset)) ||
    !finitePoint(point) ||
    visibleItems(board, project).some((item) => item.id === id)
  )
    return board;
  return {
    ...board,
    nodes: [...board.nodes, { id, kind: "asset", assetId }],
    positions: { ...board.positions, [id]: point },
  };
}

export function addNote(
  board: CanvasBoard,
  point: Point,
  text = "",
  id = `note-${crypto.randomUUID()}`,
): CanvasBoard {
  return {
    ...board,
    nodes: [...board.nodes, { id, kind: "note", text }],
    positions: { ...board.positions, [id]: point },
  };
}

export function connectItems(
  board: CanvasBoard,
  project: Project,
  source: string,
  target: string,
  id = `edge-${crypto.randomUUID()}`,
): CanvasBoard {
  const ids = new Set(visibleItems(board, project).map((item) => item.id));
  if (
    source === target ||
    !ids.has(source) ||
    !ids.has(target) ||
    board.edges.some((edge) => edge.source === source && edge.target === target)
  )
    return board;
  return {
    ...board,
    edges: [...board.edges, { id, source, target, label: "关联" }],
  };
}

export function removeItems(
  board: CanvasBoard,
  project: Project,
  nodeIds: string[],
  edgeIds: string[],
): CanvasBoard {
  const removed = new Set(nodeIds);
  const positions = { ...board.positions };
  for (const id of removed) delete positions[id];
  return {
    ...board,
    positions,
    nodes: board.nodes.filter((node) => !removed.has(node.id)),
    edges: board.edges.filter(
      (edge) =>
        !removed.has(edge.source) &&
        !removed.has(edge.target) &&
        !edgeIds.includes(edge.id),
    ),
    hiddenShots: [
      ...new Set([
        ...board.hiddenShots,
        ...project.shots
          .filter((shot) => removed.has(shot.id))
          .map((shot) => shot.id),
      ]),
    ],
  };
}

export function recordBoard(
  history: BoardHistory,
  next: CanvasBoard,
): BoardHistory {
  if (next === history.present) return history;
  return {
    past: [...history.past, history.present].slice(-50),
    present: next,
    future: [],
  };
}
function retainView(board: CanvasBoard, current: CanvasBoard): CanvasBoard {
  return { ...board, x: current.x, y: current.y, zoom: current.zoom };
}
export function undoBoard(history: BoardHistory): BoardHistory {
  const previous = history.past.at(-1);
  return previous
    ? {
        past: history.past.slice(0, -1),
        present: retainView(previous, history.present),
        future: [history.present, ...history.future],
      }
    : history;
}
export function redoBoard(history: BoardHistory): BoardHistory {
  const next = history.future[0];
  return next
    ? {
        past: [...history.past, history.present],
        present: retainView(next, history.present),
        future: history.future.slice(1),
      }
    : history;
}

export function itemLabel(item: CanvasItem): string {
  if (item.kind === "shot") return item.shot.title;
  if (item.kind === "note") return item.text.trim().slice(0, 24) || "文字便签";
  return (
    item.asset.provenance.name ||
    `${({ image: "图像", video: "视频", audio: "音频" } as Record<string, string>)[item.asset.media_type]} ${item.asset.id.slice(0, 8)}`
  );
}
