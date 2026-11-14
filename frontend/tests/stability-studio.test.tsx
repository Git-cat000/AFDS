import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { useStudio } from "../src/store";

afterEach(() => {
  cleanup();
  useStudio.setState({ projectId: null });
  vi.unstubAllGlobals();
});

function setup() {
  const apr = {
    project_id: "demo",
    revision: 1,
    title: "DEMO paged studio",
    brief: "",
    format: { width: 320, height: 180, fps_num: 24, fps_den: 1 },
    scenes: [],
    shots: [],
    assets: [],
    asset_versions: [],
    style_bible: {},
    timeline: { tracks: [{ id: "v1", type: "video", items: [] }] },
  };
  const job = {
    id: "job-1",
    project_id: "demo",
    state: "completed",
    operation: "import_image",
    provider_id: "local-image-import",
    progress: 100,
    attempt: 1,
    elapsed_ms: 10,
    input_summary: { request_hash: "demo" },
  };
  const fetcher = vi.fn(async (url: string) => ({
    ok: true,
    json: async () => {
      if (url.endsWith("/projects")) return [apr];
      if (url.endsWith("/apr")) return structuredClone(apr);
      if (url.includes("/jobs/page"))
        return {
          items: [{ ...job, id: url.includes("cursor=") ? "job-2" : "job-1" }],
          total: 27,
          active_count: 2,
          active_operations: {},
          state_counts: { queued: 2, completed: 25 },
          next_cursor: url.includes("cursor=") ? null : "cursor-2",
          has_more: !url.includes("cursor="),
        };
      if (url.endsWith("/jobs/job-1"))
        return { ...job, request: { prompt: "DEMO detailed input" } };
      if (url.endsWith("/settings/models")) return { defaults: {} };
      if (url.endsWith("/activity"))
        return { items: [], cursor: 0, has_more: false };
      if (url.endsWith("/budget/summary"))
        return {
          revision: 1,
          budget_limit: "0",
          spent: "0",
          reserved: "0",
          currency: "USD",
          entry_count: 0,
          unknown_count: 0,
          allow_remote: false,
          allow_unknown_cost: false,
        };
      return [];
    },
  }));
  vi.stubGlobal("fetch", fetcher);
  useStudio.setState({ projectId: "demo" });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <App />
    </QueryClientProvider>,
  );
  return fetcher;
}

it("keeps global active counts when the visible page contains completed history", async () => {
  setup();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /任务队列/ }).textContent).toBe(
      "任务队列2",
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: /任务队列/ }));
  await screen.findByText("共 27 项任务");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByText("第 2 页");
  expect(screen.getByRole("button", { name: /任务队列/ }).textContent).toBe(
    "任务队列2",
  );
});

it("loads the full request only when a user opens a job detail", async () => {
  const fetcher = setup();
  await screen.findByRole("button", { name: "版本记录" });
  fireEvent.click(screen.getByRole("button", { name: /任务队列/ }));
  const details = await screen.findByText("请求与溯源");
  expect(fetcher.mock.calls.some(([url]) => url.endsWith("/jobs/job-1"))).toBe(
    false,
  );
  fireEvent.click(details);
  await screen.findByText(/DEMO detailed input/);
  expect(fetcher.mock.calls.some(([url]) => url.endsWith("/jobs/job-1"))).toBe(
    true,
  );
});
