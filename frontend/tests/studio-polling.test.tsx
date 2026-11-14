import {
  act,
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
import type { Shot } from "../src/types";

afterEach(() => {
  cleanup();
  useStudio.setState({ projectId: null });
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function studio(state = "completed") {
  const apr = {
    project_id: "demo-project",
    revision: 1,
    title: "DEMO read performance",
    brief: "",
    format: { width: 320, height: 180, fps_num: 24, fps_den: 1 },
    scenes: [],
    shots: [] as Shot[],
    assets: [],
    asset_versions: [],
    characters: [],
    locations: [],
    timeline: { tracks: [{ id: "v1", type: "video", items: [] }] },
    style_bible: {},
  };
  const job = {
    id: "demo-job",
    project_id: apr.project_id,
    operation: "generate_video",
    state,
    provider_id: "mock-video-local",
    request: {},
    input_summary: { request_hash: "demo" },
    attempt: 1,
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const submissions: {
    path: string;
    body: { base_revision: number; format?: string };
  }[] = [];
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.body)
      submissions.push({ path, body: JSON.parse(String(options.body)) });
    return {
      ok: true,
      json: async () => {
        if (path.endsWith("/apr")) return structuredClone(apr);
        if (path.includes("/feedback?"))
          return { items: [], total: 0, next_cursor: null };
        if (path.includes("/jobs/page?"))
          return {
            items: [{ ...job }],
            total: 1,
            active_count: [
              "queued",
              "claimed",
              "running",
              "verifying",
              "retry_wait",
              "cancel_requested",
            ].includes(job.state)
              ? 1
              : 0,
            active_operations: {},
            next_cursor: null,
          };
        if (path.includes("/activity"))
          return { items: [], cursor: 0, has_more: false };
        if (path.endsWith("/projects")) return [apr];
        if (path.includes("/revisions/page?"))
          return {
            items: [
              {
                revision: apr.revision,
                reason: "DEMO saved",
                author: "user",
                created_at: 1,
              },
            ],
            total: 1,
            next_cursor: null,
          };
        if (path.endsWith("/settings/models")) return { defaults: {} };
        if (path.endsWith("/budget"))
          return {
            revision: apr.revision,
            budget_limit: "0.000000",
            spent: "0.000000",
            reserved: "0.000000",
            currency: "USD",
            allow_remote: true,
            allow_unknown_cost: true,
            unknown_count: 0,
            entries: [],
          };
        return [];
      },
    };
  });
  vi.stubGlobal("fetch", fetcher);
  useStudio.setState({ projectId: apr.project_id });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  const requests = (suffix: string) =>
    fetcher.mock.calls.filter(([path]) => path.includes(suffix)).length;
  return { apr, job, client, requests, submissions };
}

it("fetches revisions only while history is open and refreshes after project changes", async () => {
  const { apr, client, requests } = studio();
  await screen.findByRole("button", { name: "版本记录" });
  expect(requests("/revisions/page?")).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "版本记录" }));
  await screen.findByText("r1");
  expect(requests("/revisions/page?")).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  await act(async () => {
    apr.revision = 2;
    client.setQueryData(["project", apr.project_id], { ...apr });
  });
  expect(requests("/revisions/page?")).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "版本记录" }));
  await screen.findByText("r2");
  expect(requests("/revisions/page?")).toBe(2);
});

it("counts cancellation requests as active until cancellation finishes", async () => {
  const { apr, job, client } = studio("cancel_requested");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /任务队列/ }).textContent).toBe(
      "任务队列1",
    ),
  );
  await act(async () => {
    job.state = "cancelled";
    client.setQueryData(["jobs", apr.project_id, "summary"], {
      items: [{ ...job }],
      total: 1,
      active_count: job.state === "cancel_requested" ? 1 : 0,
      active_operations: {},
    });
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /任务队列/ }).textContent).toBe(
      "任务队列0",
    ),
  );
});

it("slows idle polling and resumes fast polling when work becomes active", async () => {
  vi.useFakeTimers();
  const { apr, job, client, requests } = studio();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(requests("/jobs/page?")).toBe(1);
  expect(requests("/apr")).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(requests("/jobs/page?")).toBe(1);
  expect(requests("/apr")).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(requests("/jobs/page?")).toBe(2);
  expect(requests("/apr")).toBe(2);
  await act(async () => {
    job.state = "cancel_requested";
    client.setQueryData(["jobs", apr.project_id, "summary"], {
      items: [{ ...job }],
      total: 1,
      active_count: job.state === "cancel_requested" ? 1 : 0,
      active_operations: {},
    });
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(requests("/jobs/page?")).toBe(3);
  expect(requests("/apr")).toBe(3);
});

it.each(["zip", "otio"])(
  "refreshes the project revision before queuing a %s snapshot",
  async (format) => {
    const { apr, submissions } = studio();
    await screen.findByRole("button", { name: "版本记录" });
    apr.revision = 2;
    fireEvent.click(
      screen.getByRole("button", {
        name: format === "zip" ? "导出工程包" : "导出 OTIO",
      }),
    );
    await waitFor(() =>
      expect(submissions).toContainEqual({
        path: `/api/v1/projects/${apr.project_id}/exports`,
        body: { base_revision: 2, format },
      }),
    );
  },
);

it("refreshes the source revision before generating footage after a recent edit", async () => {
  const { apr, client, submissions } = studio();
  await screen.findByRole("button", { name: "版本记录" });
  apr.shots = [
    {
      id: "shot",
      scene_id: "scene",
      title: "DEMO recent edit",
      action: "Walk",
      emotion: "calm",
      order: 0,
      duration_frames: 48,
      camera: { shot_size: "wide", movement: "static" },
      locked: false,
      status: "draft",
      active_asset_version_id: null,
    },
  ];
  apr.revision = 2;
  await act(async () => {
    client.setQueryData(["project", apr.project_id], { ...apr, revision: 1 });
  });
  fireEvent.click(await screen.findByRole("button", { name: "生成全部镜头" }));
  await waitFor(() =>
    expect(submissions).toContainEqual({
      path: `/api/v1/projects/${apr.project_id}/generate`,
      body: {
        base_revision: 2,
        shot_ids: ["shot"],
        provider_id: "mock-video-local",
        allow_remote: false,
      },
    }),
  );
});
