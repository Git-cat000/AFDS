import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProviderSettings } from "../src/ProviderSettings";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("saves a timestamp-capable transcription connection from URL key and model fields", async () => {
  const profile = {
    kind: "transcription",
    provider_id: "openai-transcription",
    revision: 0,
    base_url: "https://provider.example/v1",
    model: "compatible-asr",
    enabled: false,
    timeout_seconds: 60,
    transcription_timestamps: false,
  };
  const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_url, options) =>
      new Response(
        JSON.stringify(
          options?.method === "PUT"
            ? { ...profile, ...JSON.parse(String(options.body)), revision: 1 }
            : {
                providers: [profile],
                defaults: { transcription: "openai-transcription" },
              },
        ),
        { status: 200 },
      ),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ProviderSettings />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "语音转写" }));
  await screen.findByLabelText("服务 URL");
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "test-only-credential" },
  });
  fireEvent.click(screen.getByText("高级选项", { exact: true }));
  fireEvent.click(
    screen.getByLabelText("此转写模型支持 verbose_json 分段时间戳"),
  );
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      true,
    ),
  );
  const call = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(String(call[0])).toContain("/settings/providers/transcription");
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({
    base_url: profile.base_url,
    model: profile.model,
    api_key: "test-only-credential",
    transcription_timestamps: true,
  });
});

it("offers ComfyUI workflow mappings and preserves protocol and cost settings on save", async () => {
  const profile = {
    kind: "image",
    provider_id: "openai-image",
    revision: 0,
    model: "workflow-one",
    base_url: "http://127.0.0.1:8188",
    enabled: false,
    allow_private_network: true,
    timeout_seconds: 60,
    json_mode: true,
    video_seconds: 8,
    video_size: "1280x720",
    video_poll_seconds: 3,
    video_timeout_seconds: 900,
    image_size: "1024x1024",
    speech_voice: "alloy",
    protocol: "openai-compatible",
    workflow: null,
    request_cost: null,
    cost_currency: "USD",
  };
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (_url, init) => {
      if (init?.method === "PUT")
        return new Response(
          JSON.stringify({
            ...profile,
            ...JSON.parse(String(init.body)),
            revision: 1,
          }),
          { status: 200 },
        );
      return new Response(
        JSON.stringify({
          providers: [profile],
          defaults: {
            llm: "mock-director",
            video: "mock-video-local",
            image: "openai-image",
            speech: "openai-speech",
          },
        }),
        { status: 200 },
      );
    });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ProviderSettings />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "图像生成" }));
  const protocol = await screen.findByLabelText("接口协议");
  fireEvent.change(protocol, { target: { value: "comfyui" } });
  expect(screen.getByLabelText("导入 API 工作流")).toBeTruthy();
  expect(screen.getAllByText(/ComfyUI/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByText("高级选项", { exact: true }));
  fireEvent.change(screen.getByLabelText("单次请求预估费用"), {
    target: { value: "0.15" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      true,
    ),
  );
  const call = fetcher.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({
    protocol: "comfyui",
    request_cost: "0.15",
    cost_currency: "USD",
  });
});
