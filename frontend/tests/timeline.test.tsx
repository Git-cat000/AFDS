import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { TimelineEditor } from "../src/TimelineEditor";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const project = {
  project_id: "p1",
  revision: 7,
  format: { fps_num: 24, fps_den: 1, width: 320, height: 180 },
  shots: [
    {
      id: "s1",
      title: "Arrival",
      duration_frames: 48,
      locked: true,
      status: "approved",
    },
    {
      id: "s2",
      title: "Goodbye",
      duration_frames: 48,
      locked: false,
      status: "ready",
    },
  ],
  assets: [
    {
      id: "audio1",
      media_type: "audio",
      duration_seconds: 4,
      provenance: { provider_id: "local-audio-import" },
    },
  ],
  timeline: {
    tracks: [
      {
        id: "v1",
        type: "video",
        items: [
          { shot_id: "s1", start_frame: 0, duration_frames: 48 },
          { shot_id: "s2", start_frame: 48, duration_frames: 48 },
        ],
      },
      {
        id: "music",
        type: "audio",
        name: "Music",
        muted: false,
        gain_db: 0,
        items: [],
      },
    ],
    captions: [],
    markers: [],
  },
};

function setup() {
  const calls: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
    calls.push({
      url: String(url),
      body:
        init?.body instanceof File
          ? init.body
          : JSON.parse(String(init?.body || "{}")),
    });
    return new Response(
      JSON.stringify(
        String(url).endsWith("/preview")
          ? {
              base_revision: 7,
              preview_token: "a".repeat(64),
              changes: [
                { shot_id: "s1", before: 48, after: 50 },
                { shot_id: "s2", before: 48, after: 46 },
              ],
              protected_shot_ids: ["s1"],
              affected_shot_ids: ["s1", "s2"],
            }
          : project,
      ),
      { status: 200 },
    );
  });
  render(
    <TimelineEditor
      project={project}
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  return calls;
}

it("places a trimmed audio asset with gain without changing video items", async () => {
  const calls = setup();
  fireEvent.change(screen.getByLabelText("音频开始帧"), {
    target: { value: "12" },
  });
  fireEvent.change(screen.getByLabelText("源音频起始帧"), {
    target: { value: "24" },
  });
  fireEvent.change(screen.getByLabelText("音频持续帧"), {
    target: { value: "36" },
  });
  fireEvent.change(screen.getByLabelText("片段增益 dB"), {
    target: { value: "-6" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加音频片段" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].body).toMatchObject({
    base_revision: 7,
    audio_tracks: [
      {
        id: "music",
        items: [
          {
            asset_id: "audio1",
            start_frame: 12,
            source_start_frame: 24,
            duration_frames: 36,
            gain_db: -6,
          },
        ],
      },
    ],
  });
  expect(calls[0].body).not.toHaveProperty("shot_ids");
});

it("requires rights confirmation before uploading raw audio", async () => {
  const calls = setup();
  const button = screen.getByRole("button", {
    name: "导入音频",
  }) as HTMLButtonElement;
  fireEvent.change(screen.getByLabelText("选择音频文件"), {
    target: { files: [new File(["wav"], "music.wav", { type: "audio/wav" })] },
  });
  expect(button.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/我确认拥有/));
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].url).toContain("rights_confirmed=true");
  expect(calls[0].body).toBeInstanceOf(File);
});

it("shows affected timing before explicit approval", async () => {
  const calls = setup();
  fireEvent.click(screen.getByLabelText(/Arrival 之后/));
  fireEvent.change(screen.getByLabelText("BPM（可选）"), {
    target: { value: "110" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览锚点对齐" }));
  await screen.findByText(/48 → 50/);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toMatchObject({
    base_revision: 7,
    boundary_shot_ids: ["s1"],
    bpm: 110,
    allow_protected: false,
  });
  fireEvent.click(screen.getByRole("button", { name: "批准时长调整" }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].body).toMatchObject({
    approved: true,
    preview_token: "a".repeat(64),
  });
});
