import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OperationsDashboard } from "../src/OperationsDashboard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows missing runtime services and bounded graph relationships without inventing health", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.includes("/system/status")
          ? {
              status: "degraded",
              database: { status: "available", schema_version: "0005" },
              worker: {
                status: "offline",
                online_count: 0,
                last_seen_age_seconds: null,
                stale_after_seconds: 15,
              },
              tools: {
                ffmpeg: "available",
                ffprobe: "available",
                otio: "unavailable",
              },
              storage: {
                status: "unavailable",
                free_bytes: null,
                total_bytes: null,
              },
            }
          : {
              project_id: "p1",
              jobs: {
                total: 80,
                state_counts: { completed: 78, failed: 2 },
                active_count: 0,
                elapsed_ms: { sample_count: 80, p50: 1000, p95: 2000 },
                sample_limit: 200,
              },
              media: { asset_count: 3, unique_files: 2, bytes: 2000 },
              budget: {
                spent: "0.000000",
                reserved: "0.000000",
                currency: "USD",
                unknown_count: 0,
              },
              graph: {
                nodes: [
                  {
                    id: "a",
                    type: "job",
                    label: "generate_video",
                    operation: "generate_video",
                    state: "completed",
                  },
                  {
                    id: "b",
                    type: "job",
                    label: "render_timeline",
                    operation: "render_timeline",
                    state: "failed",
                  },
                ],
                edges: [{ source: "a", target: "b", kind: "media" }],
                job_limit: 50,
                media_limit: 100,
                truncated: true,
              },
            },
    })),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OperationsDashboard projectId="p1" />
    </QueryClientProvider>,
  );
  await screen.findByText("未检测到在线 Worker");
  expect(screen.getByText(/仅展示最近/)).toBeTruthy();
  expect(screen.getByRole("img", { name: "任务素材流向图" })).toBeTruthy();
  expect(screen.getByText(/素材流向不表示排队阻塞/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "刷新运行状态" })).toBeTruthy();
});
