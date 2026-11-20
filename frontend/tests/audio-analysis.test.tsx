import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AudioAnalysisPanel } from "../src/AudioAnalysisPanel";

const project = {
  project_id: "film",
  revision: 8,
  format: { fps_num: 24, fps_den: 1 },
  shots: [],
  assets: [{ id: "audio", media_type: "audio", duration_seconds: 4 }],
  timeline: {
    tracks: [
      {
        id: "v1",
        type: "video",
        items: [{ shot_id: "s1", start_frame: 0, duration_frames: 96 }],
      },
    ],
    captions: [
      { id: "manual", start_frame: 0, duration_frames: 12, text: "手写字幕" },
    ],
  },
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup() {
  const requests: Array<{ url: string; body: Record<string, unknown> }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      const body = JSON.parse(String(options?.body || "{}"));
      requests.push({ url, body });
      let value: unknown;
      if (url.endsWith("/routing"))
        value = {
          policies: {},
          candidates: [
            {
              id: "openai-transcription",
              kind: "transcription",
              enabled: true,
              timestamps: true,
              model: "DEMO-asr",
              label: "DEMO ASR",
              capabilities: ["transcription"],
            },
          ],
        };
      else if (url.endsWith("/settings/models"))
        value = {
          providers: [
            {
              kind: "transcription",
              provider_id: "openai-transcription",
              enabled: true,
              transcription_timestamps: true,
              model: "DEMO-asr",
            },
          ],
        };
      else if (url.includes("/analysis?")) value = [];
      else if (url.endsWith("/routing/preview")) value = { id: "DEMO-route" };
      else if (url.endsWith("/preview"))
        value = {
          preview_token: "hash",
          add_captions: [
            { text: "DEMO phrase", start_frame: 6, duration_frames: 18 },
          ],
          add_markers: [],
          remove_captions: [],
        };
      else if (url.endsWith("/commit")) value = project;
      else if (url.endsWith("/analysis"))
        value = { id: "job", state: "queued", source_revision: 8 };
      else if (url.endsWith("/jobs/job"))
        value = { id: "job", state: "completed", source_revision: 8 };
      else if (url.endsWith("/analysis/job"))
        value = {
          sha256: "result",
          result: {
            kind: "transcription",
            segments: [{ start_ms: 250, end_ms: 1000, text: "DEMO phrase" }],
          },
        };
      else throw new Error(url);
      return new Response(JSON.stringify(value), { status: 200 });
    }),
  );
  const run = async (action: () => Promise<unknown>) => {
    try {
      await action();
    } catch {
      return;
    }
  };
  const view = render(
    <AudioAnalysisPanel project={project} busy={false} run={run} />,
  );
  return {
    requests,
    rerender: (revision: number) =>
      view.rerender(
        <AudioAnalysisPanel
          project={{ ...project, revision }}
          busy={false}
          run={run}
        />,
      ),
  };
}

it("requires per-request transcription consent and previews selected candidates", async () => {
  const { requests } = setup();
  await screen.findByText(/DEMO-asr/);
  const start = screen.getByRole("button", {
    name: "转写语音",
  }) as HTMLButtonElement;
  expect(start.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("批准发送所选音频并承担本次转写费用"));
  fireEvent.click(start);
  await screen.findByLabelText(/候选 1/);
  fireEvent.click(screen.getByLabelText(/候选 1/));
  fireEvent.click(screen.getByRole("button", { name: "预览选中候选" }));
  await screen.findByRole("button", { name: "批准写入时间轴" });
  expect(
    screen.getByText("新增 1 条字幕、0 个节拍标记。保留现有字幕。", {
      exact: true,
    }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "批准写入时间轴" }));
  await waitFor(() =>
    expect(requests.some((r) => r.url.endsWith("/commit"))).toBe(true),
  );
  const submitted = requests.find((r) => r.url.endsWith("/analysis"))!.body;
  expect(submitted.allow_remote).toBe(true);
  const commit = requests.find((r) => r.url.endsWith("/commit"))!.body;
  expect(commit.replace_caption_ids).toEqual([]);
  expect(commit.selected_indices).toEqual([0]);
  expect(commit.approved).toBe(true);
});

it("queues local beat analysis without remote authorization", async () => {
  const { requests } = setup();
  fireEvent.click(screen.getByRole("button", { name: "检测节拍" }));
  await waitFor(() =>
    expect(requests.some((r) => r.url.endsWith("/analysis"))).toBe(true),
  );
  const body = requests.find((r) => r.url.endsWith("/analysis"))!.body;
  expect(body.kind).toBe("beats");
  expect(body.allow_remote).toBe(false);
});

it("reuses the submitted identity after a lost response and a background revision", async () => {
  const { requests, rerender } = setup();
  const original = globalThis.fetch;
  let lost = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (...args: Parameters<typeof fetch>) => {
      const response = await original(...args);
      if (String(args[0]).endsWith("/analysis") && lost) {
        lost = false;
        throw new TypeError("Lost response");
      }
      return response;
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "检测节拍" }));
  await waitFor(() =>
    expect(requests.filter((r) => r.url.endsWith("/analysis"))).toHaveLength(1),
  );
  rerender(9);
  fireEvent.click(screen.getByRole("button", { name: "检测节拍" }));
  await waitFor(() =>
    expect(requests.filter((r) => r.url.endsWith("/analysis"))).toHaveLength(2),
  );
  const sent = requests.filter((r) => r.url.endsWith("/analysis"));
  expect(sent[1].body).toEqual(sent[0].body);
});

it("reads transcription candidates through project access", async () => {
  const { requests } = setup();
  await waitFor(() =>
    expect(requests.some((value) => value.url.endsWith("/routing"))).toBe(true),
  );
  expect(requests.some((value) => value.url.endsWith("/settings/models"))).toBe(
    false,
  );
});
