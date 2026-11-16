import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TimelineEditor } from "../src/TimelineEditor";

const project = {
  project_id: "film",
  revision: 7,
  format: { fps_num: 24, fps_den: 1, width: 320, height: 180 },
  shots: [
    { id: "s1", title: "DEMO first", duration_frames: 48 },
    { id: "s2", title: "DEMO second", duration_frames: 48 },
  ],
  assets: [
    { id: "image", media_type: "image", provenance: { name: "DEMO image" } },
    { id: "audio", media_type: "audio", duration_seconds: 2 },
  ],
  timeline: {
    tracks: [
      {
        id: "v1",
        type: "video",
        items: [
          { id: "clip1", shot_id: "s1", start_frame: 0, duration_frames: 48 },
          { id: "clip2", shot_id: "s2", start_frame: 48, duration_frames: 48 },
        ],
      },
      {
        id: "music",
        type: "audio",
        muted: false,
        gain_db: 0,
        items: [
          {
            asset_id: "audio",
            start_frame: 0,
            source_start_frame: 0,
            duration_frames: 24,
            gain_db: 0,
          },
        ],
      },
    ],
    captions: [
      {
        id: "caption",
        start_frame: 0,
        duration_frames: 24,
        text: "DEMO caption",
      },
    ],
    markers: [],
    video_layers: [],
    transitions: [],
  },
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("accepts owned canvas media dropped onto a video layer", async () => {
  const requests = setup();
  fireEvent.click(screen.getByRole("button", { name: "添加视频轨" }));
  fireEvent.drop(screen.getByRole("group", { name: "叠加轨 1" }), {
    dataTransfer: {
      getData: () => JSON.stringify({ projectId: "other", assetId: "image" }),
    },
  });
  expect(screen.queryByText(/叠加片段 1/)).toBeNull();
  fireEvent.drop(screen.getByRole("group", { name: "叠加轨 1" }), {
    dataTransfer: {
      getData: () => JSON.stringify({ projectId: "film", assetId: "image" }),
    },
  });
  expect(screen.getByText("叠加片段 1 · 48 帧", { exact: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "保存视频轨" }));
  await waitFor(() =>
    expect(requests.some((r) => r.url.endsWith("/composition-v2"))).toBe(true),
  );
  const sent = JSON.parse(
    requests.find((r) => r.url.endsWith("/composition-v2"))!.body,
  );
  expect(sent.video_layers[0].items[0].asset_id).toBe("image");
});

function setup() {
  const requests: Array<{ url: string; body: string; file?: File }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      const body = typeof options?.body === "string" ? options.body : "{}";
      requests.push({
        url,
        body,
        file: options?.body instanceof File ? options.body : undefined,
      });
      let result: unknown;
      if (url.includes("/video/import?")) {
        result = {
          id: "video-import-job",
          state: "queued",
          output_asset_ids: [],
        };
      } else if (url.endsWith("/composition/preview")) {
        result = {
          preview_token: "a".repeat(64),
          total_frames: 84,
          previous_total_frames: 96,
          overflows: [],
          can_apply: true,
          transitions_changed: true,
        };
      } else if (url.endsWith("/composition-v2")) {
        const value = JSON.parse(body);
        result = {
          ...project,
          revision: 8,
          timeline: {
            ...project.timeline,
            video_layers: value.video_layers,
            transitions: value.transitions,
          },
        };
      } else if (url.endsWith("/timeline/preview")) {
        result = {
          id: "preview-job",
          state: "queued",
          source_revision: 7,
          output_asset_ids: [],
        };
      } else if (url.endsWith("/jobs/preview-job")) {
        result = {
          id: "preview-job",
          state: "completed",
          source_revision: 7,
          output_asset_ids: ["preview-video"],
        };
      } else throw new Error(`Unexpected request ${url}`);
      return new Response(JSON.stringify(result), { status: 200 });
    }),
  );
  render(
    <TimelineEditor
      project={project}
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  return requests;
}

it("edits a picture layer and preserves current audio and captions", async () => {
  const requests = setup();
  expect(screen.getByRole("heading", { name: "视频合成" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "添加视频轨" }));
  fireEvent.change(screen.getByLabelText("叠加素材"), {
    target: { value: "image" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加叠加片段" }));
  fireEvent.change(screen.getByLabelText("水平位置"), {
    target: { value: "50" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存视频轨" }));
  await waitFor(() =>
    expect(
      requests.some((request) => request.url.endsWith("/composition-v2")),
    ).toBe(true),
  );
  const sent = JSON.parse(
    requests.find((request) => request.url.endsWith("/composition-v2"))!.body,
  );
  expect(sent.video_layers[0].items[0].x).toBe(0.5);
  expect(sent.video_layers[0].items[0].asset_id).toBe("image");
  expect(sent.audio_tracks).toEqual([project.timeline.tracks[1]]);
  expect(sent.captions).toEqual(project.timeline.captions);
});

it("previews and explicitly approves a changed transition", async () => {
  const requests = setup();
  fireEvent.change(screen.getByLabelText("转场 1"), {
    target: { value: "dissolve" },
  });
  expect(
    (screen.getByRole("button", { name: "保存视频轨" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "预览转场变更" }));
  await screen.findByRole("button", { name: "批准并保存剪辑" });
  fireEvent.click(screen.getByRole("button", { name: "批准并保存剪辑" }));
  await waitFor(() =>
    expect(
      requests.some((request) => request.url.endsWith("/composition-v2")),
    ).toBe(true),
  );
  const sent = JSON.parse(
    requests.find((request) => request.url.endsWith("/composition-v2"))!.body,
  );
  expect(sent.approved).toBe(true);
  expect(sent.preview_token).toBe("a".repeat(64));
  expect(sent.transitions[0].kind).toBe("dissolve");
});

it("queues an actual preview and displays its owned video", async () => {
  const requests = setup();
  fireEvent.change(screen.getByLabelText("预览开始帧"), {
    target: { value: "12" },
  });
  fireEvent.change(screen.getByLabelText("预览时长帧"), {
    target: { value: "24" },
  });
  fireEvent.click(screen.getByRole("button", { name: "生成预览片段" }));
  await waitFor(() =>
    expect(screen.getByLabelText("剪辑预览视频").getAttribute("src")).toBe(
      "/api/v1/projects/film/assets/preview-video/file",
    ),
  );
  const sent = JSON.parse(
    requests.find((request) => request.url.endsWith("/timeline/preview"))!.body,
  );
  expect(sent.start_frame).toBe(12);
  expect(sent.duration_frames).toBe(24);
});

it("uploads an explicitly authorized local video with audio extraction", async () => {
  const requests = setup();
  const file = new File(["DEMO"], "DEMO-source.mp4", { type: "video/mp4" });
  fireEvent.change(screen.getByLabelText("本机视频"), {
    target: { files: [file] },
  });
  expect(
    (screen.getByRole("button", { name: "导入本机视频" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByLabelText("确认有权使用本视频"));
  fireEvent.click(screen.getByRole("button", { name: "导入本机视频" }));
  await waitFor(() =>
    expect(
      requests.some((request) => request.url.includes("/video/import?")),
    ).toBe(true),
  );
  const sent = requests.find((request) =>
    request.url.includes("/video/import?"),
  )!;
  expect(sent.url).toContain("rights_confirmed=true");
  expect(sent.url).toContain("extract_audio=true");
  expect(sent.file?.name).toBe("DEMO-source.mp4");
});
