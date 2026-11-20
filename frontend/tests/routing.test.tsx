import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CandidateSettings } from "../src/CandidateSettings";
import { ProjectRouting } from "../src/ProjectRouting";
import type { Connection } from "../src/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const profile: Connection = {
  kind: "llm",
  provider_id: "openai-llm",
  display_name: "DEMO",
  revision: 0,
  base_url: "",
  model: "",
  enabled: false,
  has_api_key: false,
  allow_private_network: false,
  timeout_seconds: 60,
  json_mode: true,
  video_seconds: 8,
  video_size: "1280x720",
  video_poll_seconds: 3,
  video_timeout_seconds: 900,
  image_size: "1024x1024",
  speech_voice: "alloy",
};

function wrap(node: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {node}
    </QueryClientProvider>,
  );
}

it("saves another model candidate from connection fields and an explicit identity", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_url, options) =>
      new Response(
        JSON.stringify(
          options?.method === "PUT"
            ? {
                ...profile,
                ...JSON.parse(String(options.body)).connection,
                id: "demo-secondary",
                revision: 1,
              }
            : [],
        ),
        { status: 200 },
      ),
  );
  wrap(<CandidateSettings kind="llm" profile={profile} />);
  fireEvent.click(screen.getByText("管理候选连接", { exact: true }));
  await screen.findByLabelText("连接 ID");
  fireEvent.change(screen.getByLabelText("连接 ID"), {
    target: { value: "demo-secondary" },
  });
  fireEvent.change(screen.getByLabelText("连接名称"), {
    target: { value: "DEMO secondary" },
  });
  fireEvent.change(screen.getByLabelText("服务 URL"), {
    target: { value: "https://example.com/v1" },
  });
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "DEMO-contract-key" },
  });
  fireEvent.change(screen.getByLabelText("模型名称"), {
    target: { value: "DEMO model" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      true,
    ),
  );
  const call = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(String(call[0])).toContain("/settings/candidates/demo-secondary");
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({
    kind: "llm",
    connection: {
      label: "DEMO secondary",
      base_url: "https://example.com/v1",
      model: "DEMO model",
      api_key: "DEMO-contract-key",
    },
  });
});

it("shows routing reasons and saves a bounded project policy", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_url, options) =>
      new Response(
        JSON.stringify(
          options?.method === "PUT"
            ? { revision: 2 }
            : {
                revision: 1,
                policies: {},
                candidates: [
                  {
                    id: "demo-image",
                    label: "DEMO image",
                    kind: "image",
                    capabilities: ["text_to_image"],
                    config_revision: 1,
                    model: "DEMO model",
                    currency: "USD",
                    estimated_cost: "0.1",
                    enabled: true,
                    health: "untested",
                  },
                ],
              },
        ),
        { status: 200 },
      ),
  );
  wrap(
    <ProjectRouting
      projectId="demo-project"
      run={async (action) => {
        await action();
      }}
      busy={false}
      canManage
    />,
  );
  fireEvent.click(screen.getByText("模型路由策略", { exact: true }));
  await screen.findByLabelText("路由用途");
  fireEvent.change(screen.getByLabelText("路由用途"), {
    target: { value: "text_to_image" },
  });
  fireEvent.click(await screen.findByLabelText("允许 DEMO image"));
  fireEvent.change(screen.getByLabelText("选择策略"), {
    target: { value: "lowest_estimated_cost" },
  });
  fireEvent.change(screen.getByLabelText("本次估算费用上限"), {
    target: { value: "0.5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存路由策略" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      true,
    ),
  );
  const call = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(
    JSON.parse(String(call[1]?.body)).policies.text_to_image,
  ).toMatchObject({
    allowed_ids: ["demo-image"],
    strategy: "lowest_estimated_cost",
    max_estimated_cost: "0.5",
  });
});
