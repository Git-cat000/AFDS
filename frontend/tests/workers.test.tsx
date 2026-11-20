import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkerNodes } from "../src/WorkerNodes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionContext } from "../src/session";
import { OperationsDashboard } from "../src/OperationsDashboard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows node capacity and storage failures and drains only the selected node", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(JSON.stringify({ draining: true })));
  vi.stubGlobal("fetch", fetcher);
  const changed = vi.fn();
  render(
    <WorkerNodes
      nodes={[
        {
          id: "demo-linux",
          label: "DEMO Linux renderer",
          status: "online",
          draining: false,
          slots: 2,
          last_seen_at: Date.now() / 1000,
          app_version: "0.7.0",
          capabilities: ["python", "media"],
          operations: ["render_timeline"],
          storage: { status: "available" },
          current_tasks: [
            { id: "demo-job", project_id: "demo-project", state: "running" },
          ],
        },
        {
          id: "demo-missing",
          label: "DEMO missing mount",
          status: "unavailable",
          draining: false,
          slots: 1,
          last_seen_at: Date.now() / 1000,
          app_version: "0.7.0",
          capabilities: ["python"],
          operations: ["check_production"],
          storage: { status: "unavailable", reason: "Shared marker differs" },
          current_tasks: [],
        },
      ]}
      onChanged={changed}
    />,
  );
  expect(screen.getByText("DEMO Linux renderer")).toBeTruthy();
  expect(screen.getByText("1 / 2 槽位")).toBeTruthy();
  expect(screen.getByText("Shared marker differs")).toBeTruthy();
  expect(screen.getByText("demo-job")).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "排空 DEMO Linux renderer" }),
  );
  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/v1/system/workers/demo-linux/drain",
  );
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
    draining: true,
  });
});

it("hides cached global node data after administrator access is revoked", () => {
  const client = new QueryClient();
  client.setQueryData(["runtime-status"], {
    status: "ok",
    database: { status: "available", schema_version: "0010" },
    worker: {
      status: "online",
      online_count: 1,
      last_seen_age_seconds: 0,
      stale_after_seconds: 15,
    },
    tools: {},
    storage: { status: "available", free_bytes: 1, total_bytes: 2 },
    nodes: [
      {
        id: "demo-admin-only",
        label: "DEMO admin node",
        status: "online",
        draining: false,
        slots: 1,
        last_seen_at: Date.now() / 1000,
        app_version: "0.7.0",
        capabilities: ["python"],
        operations: ["check_production"],
        storage: { status: "available" },
        current_tasks: [],
      },
    ],
  });
  render(
    <QueryClientProvider client={client}>
      <SessionContext.Provider
        value={{
          session: {
            mode: "team",
            authenticated: true,
            user: {
              id: "demo-user",
              username: "demo-user",
              display_name: "DEMO editor",
              is_admin: false,
            },
          },
          logout: async () => {},
          pending: false,
          error: "",
        }}
      >
        <OperationsDashboard />
      </SessionContext.Provider>
    </QueryClientProvider>,
  );
  expect(screen.queryByText("DEMO admin node")).toBeNull();
});
