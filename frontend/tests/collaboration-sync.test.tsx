import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { emptyBoard } from "../src/canvas-board";
import { useCollaborativeBoard } from "../src/useCollaborativeBoard";
import type { Project } from "../src/types";
import type { WorkspaceOperation } from "../src/workspace-operations";

const project = {
  project_id: "team-workspace-test",
  revision: 1,
  shots: [],
  assets: [],
} as unknown as Project;
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

function server(loseFirst = false) {
  const state = {
    project_id: project.project_id,
    workspace_revision: 0,
    initialized: false,
    document: emptyBoard(),
    entity_versions: {} as Record<string, number>,
  };
  const requests: WorkspaceOperation[][] = [];
  const replies = new Map<string, typeof state>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      if (url.endsWith("/presence")) return response([]);
      if (url.endsWith("/collaboration")) return response(state);
      const operations = JSON.parse(String(options.body))
        .operations as WorkspaceOperation[];
      requests.push(operations);
      if (operations.length > 64)
        return response(
          { error: { code: "VALIDATION_ERROR", message: "At most64" } },
          422,
        );
      const replay = replies.get(operations[0].operation_id);
      if (replay) return response(replay);
      for (const item of operations) {
        const value = item.value as {
          node: { id: string; kind: "note"; text: string };
        };
        state.document.nodes = state.document.nodes.filter(
          (node) => node.id !== item.entity_id,
        );
        state.document.nodes.push(value.node);
        state.entity_versions[`node/${item.entity_id}`] =
          (state.entity_versions[`node/${item.entity_id}`] || 0) + 1;
      }
      state.workspace_revision++;
      state.initialized = true;
      replies.set(operations[0].operation_id, structuredClone(state));
      if (loseFirst && requests.length === 1)
        throw new Error("DEMO response lost");
      return response(state);
    }),
  );
  return { state, requests };
}

it("splits a supported large canvas draft into bounded operation batches", async () => {
  const backend = server();
  const { result } = renderHook(() => useCollaborativeBoard(project, true));
  await waitFor(() => expect(result.current.syncStatus).toBe("协作画布已同步"));
  act(() =>
    result.current.change((board) => ({
      ...board,
      nodes: Array.from({ length: 80 }, (_, index) => ({
        id: `note-${index}`,
        kind: "note" as const,
        text: "DEMO",
      })),
    })),
  );
  await waitFor(() => expect(backend.state.document.nodes).toHaveLength(80), {
    timeout: 4000,
  });
  expect(backend.requests.map((items) => items.length)).toEqual([64, 16]);
  expect(result.current.board.nodes).toHaveLength(80);
});

it("reuses operation identities after an unknown response and page reload", async () => {
  const backend = server(true);
  const first = renderHook(() => useCollaborativeBoard(project, true));
  await waitFor(() =>
    expect(first.result.current.syncStatus).toBe("协作画布已同步"),
  );
  act(() =>
    first.result.current.change((board) => ({
      ...board,
      nodes: [{ id: "note-a", kind: "note", text: "DEMO" }],
    })),
  );
  await waitFor(() =>
    expect(first.result.current.syncError).toContain("请求身份"),
  );
  first.unmount();
  const restored = renderHook(() => useCollaborativeBoard(project, true));
  await waitFor(() =>
    expect(restored.result.current.syncStatus).toBe("协作画布已同步"),
  );
  expect(backend.requests).toHaveLength(2);
  expect(backend.requests[1]).toEqual(backend.requests[0]);
  expect(backend.state.workspace_revision).toBe(1);
});

it("retains a draft and marks it pending when a background read fails before submission", async () => {
  const backend = server();
  const original = fetch;
  let offline = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      if (offline && url.endsWith("/collaboration"))
        throw new Error("DEMO read lost");
      return original(url, options);
    }),
  );
  const { result } = renderHook(() => useCollaborativeBoard(project, true));
  await waitFor(() => expect(result.current.syncStatus).toBe("协作画布已同步"));
  offline = true;
  act(() => {
    result.current.change((board) => ({
      ...board,
      nodes: [{ id: "note-a", kind: "note", text: "DEMO retained" }],
    }));
    window.dispatchEvent(
      new CustomEvent("afds-project-event", {
        detail: { projectId: project.project_id },
      }),
    );
  });
  await waitFor(() => expect(result.current.syncStatus).toBe("协作草稿待同步"));
  expect(result.current.syncError).toContain("草稿已保留");
  offline = false;
  await act(() => result.current.retrySync());
  expect(backend.state.document.nodes).toHaveLength(1);
});

it("keeps the editing identity across repeated drag updates", async () => {
  server();
  const { result } = renderHook(() => useCollaborativeBoard(project, true));
  await waitFor(() => expect(result.current.syncStatus).toBe("协作画布已同步"));
  act(() => {
    result.current.startGesture("note-a");
    result.current.startGesture();
  });
  await waitFor(
    () => {
      const calls = vi
        .mocked(fetch)
        .mock.calls.filter(([path]) => String(path).endsWith("/presence"));
      expect(JSON.parse(String(calls.at(-1)?.[1]?.body)).entity_id).toBe(
        "note-a",
      );
    },
    { timeout: 3000 },
  );
});
