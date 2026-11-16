import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { emptyBoard } from "../src/canvas-board";
import { useCanvasBoard } from "../src/useCanvasBoard";
import type { Project } from "../src/types";

const project = {
  project_id: "workspace-test",
  revision: 4,
  shots: [],
  assets: [],
} as unknown as Project;
const savedDocument = {
  version: 2,
  positions: { "note-server": { x: -40, y: 20 } },
  nodes: [{ id: "note-server", kind: "note", text: "服务器便签" }],
  edges: [],
  hiddenShots: [],
};
function snapshot(revision = 1, initialized = true) {
  return {
    project_id: project.project_id,
    workspace_revision: revision,
    initialized,
    document: savedDocument,
  };
}
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("loads shared layout while retaining the browser viewport", async () => {
  localStorage.setItem(
    "afds-canvas-workspace-test",
    JSON.stringify({ ...emptyBoard(), zoom: 0.6 }),
  );
  const fetcher = vi.fn(async () => response(snapshot()));
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useCanvasBoard(project));
  await waitFor(() =>
    expect(result.current.board.nodes).toEqual(savedDocument.nodes),
  );
  expect(result.current.board.zoom).toBe(0.6);
  expect(result.current.board.positions["note-server"].x).toBe(-40);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("migrates a legacy browser layout only into an empty server workspace", async () => {
  localStorage.setItem(
    "afds-canvas-workspace-test",
    JSON.stringify({ ...emptyBoard(), ...savedDocument }),
  );
  const requests: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      if (options?.method === "PUT") {
        requests.push(JSON.parse(options.body));
        return response(snapshot());
      }
      return response({
        ...snapshot(0, false),
        document: { ...savedDocument, nodes: [], positions: {} },
      });
    }),
  );
  renderHook(() => useCanvasBoard(project));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toEqual({
    base_workspace_revision: 0,
    document: savedDocument,
  });
  expect(project.revision).toBe(4);
});

it("serializes saves so edits made during a request use the returned revision", async () => {
  let completeFirst: ((value: Response) => void) | undefined;
  const requests: Array<{
    base_workspace_revision: number;
    document: typeof savedDocument;
  }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      if (options?.method !== "PUT") return response(snapshot());
      requests.push(JSON.parse(options.body));
      if (requests.length === 1)
        return new Promise<Response>((resolve) => {
          completeFirst = resolve;
        });
      return response({ ...snapshot(3), document: requests.at(-1)!.document });
    }),
  );
  const { result } = renderHook(() => useCanvasBoard(project));
  await waitFor(() => expect(result.current.board.nodes).toHaveLength(1));
  act(() =>
    result.current.change((board) => ({
      ...board,
      positions: { "note-server": { x: 1, y: 2 } },
    })),
  );
  await waitFor(() => expect(requests).toHaveLength(1));
  act(() =>
    result.current.change((board) => ({
      ...board,
      positions: { "note-server": { x: 8, y: 9 } },
    })),
  );
  await act(async () => completeFirst!(response(snapshot(2))));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests.map((request) => request.base_workspace_revision)).toEqual([
    1, 2,
  ]);
  expect(requests[1].document.positions["note-server"]).toEqual({ x: 8, y: 9 });
});

it("keeps the local draft after a revision conflict", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) =>
      options?.method === "PUT"
        ? response(
            { error: { code: "WORKSPACE_CONFLICT", message: "conflict" } },
            409,
          )
        : response(snapshot()),
    ),
  );
  const { result } = renderHook(() => useCanvasBoard(project));
  await waitFor(() => expect(result.current.board.nodes).toHaveLength(1));
  act(() =>
    result.current.change((board) => ({
      ...board,
      positions: { "note-server": { x: 70, y: 80 } },
    })),
  );
  await waitFor(() => expect(result.current.syncError).toContain("其他页面"));
  expect(result.current.board.positions["note-server"].x).toBe(70);
  expect(
    JSON.parse(localStorage.getItem("afds-workspace-sync-workspace-test")!)
      .dirty,
  ).toBe(true);
  await act(async () => result.current.loadServer());
  expect(result.current.board.positions["note-server"].x).toBe(-40);
});

it("preserves an offline draft across remounts and offers explicit retry", async () => {
  let online = true;
  const put = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      if (!online) throw new TypeError("offline");
      if (options?.method === "PUT") {
        put(JSON.parse(options.body));
        return response(snapshot(2));
      }
      return response(snapshot());
    }),
  );
  const first = renderHook(() => useCanvasBoard(project));
  await waitFor(() => expect(first.result.current.board.nodes).toHaveLength(1));
  online = false;
  act(() =>
    first.result.current.change((board) => ({
      ...board,
      positions: { "note-server": { x: 6, y: 7 } },
    })),
  );
  await waitFor(() => expect(first.result.current.syncError).toBeTruthy());
  first.unmount();
  online = true;
  const next = renderHook(() => useCanvasBoard(project));
  await waitFor(() => expect(next.result.current.syncError).toContain("草稿"));
  expect(next.result.current.board.positions["note-server"].x).toBe(6);
  await act(async () => next.result.current.retrySync());
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put.mock.calls[0][0].document.positions["note-server"].x).toBe(6);
});
