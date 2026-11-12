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
    shots: [],
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
        if (path.endsWith("/jobs")) return [{ ...job }];
        if (path.endsWith("/projects")) return [apr];
        if (path.endsWith("/revisions"))
          return [
            {
              revision: apr.revision,
              reason: "DEMO saved",
              author: "user",
              created_at: 1,
            },
          ];
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
    fetcher.mock.calls.filter(([path]) => path.endsWith(suffix)).length;
  return { apr, job, client, requests, submissions };
}

it("fetches revisions only while history is open and refreshes after project changes", async () => {
  const { apr, client, requests } = studio();
  await screen.findByRole("button", { name: "版本记录" });
  expect(requests("/revisions")).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "版本记录" }));
  await screen.findByText("r1");
  expect(requests("/revisions")).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  await act(async () => {
    apr.revision = 2;
    client.setQueryData(["project", apr.project_id], { ...apr });
  });
  expect(requests("/revisions")).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "版本记录" }));
  await screen.findByText("r2");
  expect(requests("/revisions")).toBe(2);
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
    client.setQueryData(["jobs", apr.project_id], [{ ...job }]);
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
  expect(requests("/jobs")).toBe(1);
  expect(requests("/apr")).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(requests("/jobs")).toBe(1);
  expect(requests("/apr")).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(requests("/jobs")).toBe(2);
  expect(requests("/apr")).toBe(2);
  await act(async () => {
    job.state = "cancel_requested";
    client.setQueryData(["jobs", apr.project_id], [{ ...job }]);
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(requests("/jobs")).toBe(3);
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
