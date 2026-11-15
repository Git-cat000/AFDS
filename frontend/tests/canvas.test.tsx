import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CreationWorkspace } from "../src/CreationWorkspace";
import type { Project } from "../src/types";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});
function setup() {
  const shot = {
    id: "shot",
    scene_id: "scene",
    order: 0,
    title: "DEMO shot",
    action: "Walk",
    emotion: "calm",
    duration_frames: 48,
    camera: { shot_size: "wide", movement: "still" },
    locked: false,
    status: "ready",
    active_asset_version_id: "v1",
  };
  const project = {
    project_id: "demo",
    revision: 4,
    title: "DEMO",
    brief: "",
    format: { width: 320, height: 180, fps_num: 24, fps_den: 1 },
    style_bible: {},
    shots: [shot],
    assets: [
      {
        id: "image",
        sha256: "imagehash",
        media_type: "image",
        provenance: { name: "DEMO reference" },
      },
      {
        id: "a1",
        sha256: "hash1",
        media_type: "video",
        provenance: { is_mock: true },
      },
    ],
    asset_versions: [1, 2].map((i) => ({
      id: `v${i}`,
      shot_id: "shot",
      asset_id: `a${i}`,
      poster_asset_id: `p${i}`,
      status: "ready",
      generated_by_job_id: "job",
    })),
    timeline: { tracks: [{ id: "v", type: "video", items: [] }] },
  } as unknown as Project;
  const fetcher = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.endsWith("/reviews")
            ? []
            : url.includes("/feedback?")
              ? { items: [], next_cursor: null, total: 0 }
              : url.endsWith("/changesets/preview")
                ? {
                    affected_shot_ids: ["shot"],
                    protected_shot_ids: [],
                    render_invalidated: true,
                    changes: [],
                  }
                : {},
        ),
        { status: 200 },
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (value: Project) => (
    <QueryClientProvider client={client}>
      <CreationWorkspace
        project={value}
        run={async (action) => {
          await action();
        }}
        busy={false}
        edit={vi.fn()}
        generate={vi.fn()}
        canGenerate
      />
    </QueryClientProvider>
  );
  const renderView = () => render(tree(project));
  const view = renderView();
  return {
    fetcher,
    renderView,
    project,
    refresh: (value: Project) => view.rerender(tree(value)),
  };
}

it("deselects a keyboard-selected edge when another node is selected or Escape is pressed", async () => {
  localStorage.setItem(
    "afds-canvas-demo",
    JSON.stringify({
      nodes: [
        { id: "asset-image", kind: "asset", assetId: "image" },
        { id: "note-unrelated", kind: "note", text: "unrelated" },
      ],
      edges: [
        { id: "line", source: "asset-image", target: "shot", label: "关联" },
      ],
    }),
  );
  setup();
  const line = await screen.findByLabelText(
    "DEMO reference → DEMO shot · 关联",
    {},
    { timeout: 5000 },
  );
  fireEvent.keyDown(line, { key: "Enter" });
  fireEvent.keyUp(line, { key: "Enter" });
  expect(screen.getByRole("region", { name: "连线设置" })).toBeTruthy();
  fireEvent.keyDown(line, { key: "Escape" });
  fireEvent.keyUp(line, { key: "Escape" });
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "连线设置" })).toBeNull(),
  );
  fireEvent.keyDown(line, { key: "Enter" });
  fireEvent.keyUp(line, { key: "Enter" });
  const note = screen
    .getByPlaceholderText("写下想法、提示词或制作说明")
    .closest(".react-flow__node")!;
  fireEvent.keyDown(note, { key: "Enter" });
  fireEvent.keyUp(note, { key: "Enter" });
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "连线设置" })).toBeNull(),
  );
  fireEvent.keyDown(note, { key: "Delete" });
  fireEvent.keyUp(note, { key: "Delete" });
  await waitFor(() => {
    const board = JSON.parse(localStorage.getItem("afds-canvas-demo")!);
    expect(
      board.nodes.some((item: { id: string }) => item.id === "note-unrelated"),
    ).toBe(false);
    expect(board.edges).toHaveLength(1);
  });
});

it("persists keyboard arrangement and zoom and restores the canvas after reopening", async () => {
  const { renderView } = setup();
  fireEvent.keyDown(
    await screen.findByRole(
      "button",
      { name: "移动 DEMO shot" },
      { timeout: 5000 },
    ),
    {
      key: "ArrowRight",
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
  expect(screen.getByText("90%")).toBeTruthy();
  expect(
    JSON.parse(localStorage.getItem("afds-canvas-demo")!).positions.shot.x,
  ).toBe(20);
  cleanup();
  renderView();
  expect(screen.getByText("90%")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "自动排列" }));
  expect(screen.getByText("100%")).toBeTruthy();
});
it("previews candidate selection and explicitly approves the captured revision", async () => {
  const { fetcher } = setup();
  fireEvent.click(screen.getByRole("button", { name: "预览选用候选 B" }));
  await screen.findByRole("button", { name: "批准创作修改" });
  expect(
    fetcher.mock.calls.some(([url]) => url.endsWith("/changesets/commit")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "批准创作修改" }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([url]) => url.endsWith("/changesets/commit")),
    ).toBe(true),
  );
  const request = fetcher.mock.calls.find(([url]) =>
    url.endsWith("/changesets/commit"),
  )!;
  const body = JSON.parse(
    String((request as unknown as [string, RequestInit])[1].body),
  );
  expect(body.base_revision).toBe(4);
  expect(body.approved).toBe(true);
  const preview = fetcher.mock.calls.find(([url]) =>
    url.endsWith("/changesets/preview"),
  )!;
  expect(
    JSON.parse(String((preview as unknown as [string, RequestInit])[1].body))
      .approved,
  ).toBe(true);
  expect(body.operations[0]).toMatchObject({
    field: "active_asset_version_id",
    value: "v2",
  });
});
it("binds human feedback to candidate A and keeps reference updates previewed", async () => {
  const { fetcher } = setup();
  fireEvent.click(screen.getByText("人工评价与迭代记录"));
  fireEvent.change(screen.getByLabelText("评价与改进建议"), {
    target: { value: "DEMO improve motion" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认记录人工评价" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([url]) => url.endsWith("/feedback"))).toBe(
      true,
    ),
  );
  fireEvent.click(screen.getByText("参考图与首尾帧"));
  fireEvent.change(screen.getByLabelText("reference_image"), {
    target: { value: "image" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览参考图修改" }));
  await screen.findByRole("button", { name: "批准创作修改" });
  const call = fetcher.mock.calls.find(([url]) => url.endsWith("/feedback"))!;
  expect(
    JSON.parse(String((call as unknown as [string, RequestInit])[1].body)),
  ).toMatchObject({
    version_id: "v1",
    approved: true,
    note: "DEMO improve motion",
  });
});

it("updates untouched reference controls when the APR source changes", () => {
  const { project, refresh } = setup();
  fireEvent.click(screen.getByText("参考图与首尾帧"));
  refresh({
    ...project,
    revision: 5,
    shots: [
      { ...project.shots[0], reference_images: { start_image: "image" } },
    ],
  });
  expect(
    (screen.getByLabelText("start_image") as HTMLSelectElement).value,
  ).toBe("image");
});

it("adds a material and note, connects their ports, and restores a deleted line with undo", async () => {
  setup();
  await screen.findByRole("button", { name: "移动 DEMO shot" });
  fireEvent.click(screen.getByRole("button", { name: "素材抽屉" }));
  fireEvent.click(
    screen.getByRole("button", { name: "放入画布 DEMO reference" }),
  );
  await screen.findByRole("button", { name: "从 DEMO reference 连线" });
  fireEvent.click(screen.getByRole("button", { name: "添加文字" }));
  fireEvent.change(
    await screen.findByPlaceholderText("写下想法、提示词或制作说明"),
    { target: { value: "DEMO canvas note" } },
  );
  fireEvent.keyDown(
    screen.getByRole("button", { name: "从 DEMO reference 连线" }),
    { key: "Enter" },
  );
  fireEvent.keyDown(screen.getByRole("button", { name: "连接到 DEMO shot" }), {
    key: "Enter",
  });
  await waitFor(() =>
    expect(
      JSON.parse(localStorage.getItem("afds-canvas-demo")!).edges,
    ).toHaveLength(1),
  );
  const line = await screen.findByLabelText(
    "DEMO reference → DEMO shot · 关联",
  );
  fireEvent.click(line);
  fireEvent.click(screen.getByRole("button", { name: "删除连线" }));
  expect(
    JSON.parse(localStorage.getItem("afds-canvas-demo")!).edges,
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "撤销画布操作" }));
  expect(
    JSON.parse(localStorage.getItem("afds-canvas-demo")!).edges,
  ).toHaveLength(1);
  expect(
    JSON.parse(localStorage.getItem("afds-canvas-demo")!).nodes.find(
      (node: { kind: string }) => node.kind === "note",
    ).text,
  ).toBe("DEMO canvas note");
});
it("keeps a dirty reference draft tied to its source revision until explicitly refreshed", () => {
  const { project, refresh } = setup();
  fireEvent.click(screen.getByText("参考图与首尾帧"));
  fireEvent.change(screen.getByLabelText("end_image"), {
    target: { value: "image" },
  });
  refresh({
    ...project,
    revision: 5,
    shots: [
      { ...project.shots[0], reference_images: { start_image: "image" } },
    ],
  });
  expect(
    screen.getByText("参考图草稿基于旧版本，请载入当前设置后重新编辑。"),
  ).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: "预览参考图修改",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "载入当前参考图设置" }));
  expect(
    (screen.getByLabelText("start_image") as HTMLSelectElement).value,
  ).toBe("image");
  expect((screen.getByLabelText("end_image") as HTMLSelectElement).value).toBe(
    "",
  );
});
