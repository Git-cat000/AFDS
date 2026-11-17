import type {
  CanvasBoard,
  BoardMaterial,
  BoardEdge,
  Point,
} from "./canvas-board";

export type SharedBoard = Omit<CanvasBoard, "x" | "y" | "zoom">;
type NodeValue = { node?: BoardMaterial; position?: Point; hidden?: boolean };
type EntityValue = NodeValue | BoardEdge;
export type WorkspaceOperation = {
  operation_id: string;
  entity_id: string;
  expected_entity_version: number;
  kind: "set_node" | "remove_node" | "set_edge" | "remove_edge";
  value: EntityValue | null;
};

function entities(board: SharedBoard) {
  const values: Record<string, EntityValue> = {};
  const nodes = new Map(board.nodes.map((node) => [node.id, node]));
  const ids = new Set([
    ...nodes.keys(),
    ...Object.keys(board.positions),
    ...board.hiddenShots,
  ]);
  for (const id of [...ids].sort()) {
    const value: NodeValue = {};
    if (nodes.has(id)) value.node = nodes.get(id);
    if (board.positions[id]) value.position = board.positions[id];
    if (board.hiddenShots.includes(id)) value.hidden = true;
    values[`node/${id}`] = value;
  }
  for (const edge of board.edges) values[`edge/${edge.id}`] = edge;
  return values;
}

const equal = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);

export function diffWorkspace(
  before: SharedBoard,
  after: SharedBoard,
  versions: Record<string, number>,
): WorkspaceOperation[] {
  const old = entities(before);
  const next = entities(after);
  const keys = [...new Set([...Object.keys(old), ...Object.keys(next)])];
  // Deleting a referenced material and its links remains one atomic batch.
  keys.sort((a, b) => {
    const priority = (key: string) =>
      key.startsWith("edge/") && !next[key]
        ? 0
        : key.startsWith("node/")
          ? 1
          : 2;
    return priority(a) - priority(b) || a.localeCompare(b);
  });
  return keys
    .filter((key) => !equal(old[key], next[key]))
    .map((key) => ({
      operation_id: crypto.randomUUID(),
      entity_id: key.slice(5),
      expected_entity_version: versions[key] || 0,
      kind: key.startsWith("node/")
        ? next[key]
          ? "set_node"
          : "remove_node"
        : next[key]
          ? "set_edge"
          : "remove_edge",
      value: next[key] || null,
    }));
}

export function mergeWorkspaceDraft(
  base: SharedBoard,
  draft: CanvasBoard,
  remote: SharedBoard,
) {
  const original = entities(base);
  const local = entities(draft);
  const server = entities(remote);
  const merged = { ...server };
  const conflicts: string[] = [];
  for (const key of new Set([
    ...Object.keys(original),
    ...Object.keys(local),
  ])) {
    if (equal(original[key], local[key])) continue;
    if (!equal(original[key], server[key]) && !equal(local[key], server[key]))
      conflicts.push(key);
    if (local[key]) merged[key] = local[key];
    else delete merged[key];
  }
  return { board: boardFromEntities(draft, merged), conflicts };
}

export function applyWorkspaceOperations(
  board: CanvasBoard,
  operations: WorkspaceOperation[],
) {
  const values = entities(board);
  for (const item of operations) {
    const key = `${item.kind.includes("edge") ? "edge" : "node"}/${item.entity_id}`;
    if (item.value) values[key] = item.value;
    else delete values[key];
  }
  return boardFromEntities(board, values);
}

function boardFromEntities(
  viewport: CanvasBoard,
  values: Record<string, EntityValue>,
) {
  const board: CanvasBoard = {
    ...viewport,
    positions: {},
    nodes: [],
    edges: [],
    hiddenShots: [],
  };
  for (const [key, value] of Object.entries(values)) {
    if (key.startsWith("edge/")) {
      board.edges.push(value as BoardEdge);
      continue;
    }
    const id = key.slice(5);
    const node = value as NodeValue;
    if (node.node) board.nodes.push(node.node);
    if (node.position) board.positions[id] = node.position;
    if (node.hidden) board.hiddenShots.push(id);
  }
  return board;
}
