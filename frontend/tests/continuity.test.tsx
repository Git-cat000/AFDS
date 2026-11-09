import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ContinuityPanel } from "../src/ContinuityPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const project = {
  project_id: "project-one",
  revision: 7,
  characters: [
    {
      id: "character-one",
      name: "Ada",
      invariant_traits: ["red coat"],
      reference_asset_ids: [],
    },
  ],
  locations: [],
  assets: [],
  world_states: [],
  continuity_links: [],
  scenes: [{ id: "scene-one", title: "Station", location_id: null }],
  shots: [
    {
      id: "shot-one",
      scene_id: "scene-one",
      title: "Arrival",
      character_ids: ["character-one"],
      input_state_ref: null,
      output_state_ref: null,
      action: "Wait",
      emotion: "calm",
      duration_frames: 120,
      camera: { shot_size: "wide", movement: "static" },
      locked: true,
      status: "approved",
    },
  ],
};

it("previews Bible impact and sends approval only after the user reviews affected shots", async () => {
  const requests: { url: string; body: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
    requests.push({
      url: String(url),
      body: init?.body ? JSON.parse(String(init.body)) : {},
    });
    return new Response(
      JSON.stringify(
        String(url).endsWith("/preview")
          ? {
              id: "plan-one",
              type: "continuity",
              base_revision: 7,
              affected_shot_ids: ["shot-one"],
              protected_shot_ids: ["shot-one"],
              changes: [
                {
                  field: "character",
                  before: { invariant_traits: ["red coat"] },
                  after: { invariant_traits: ["blue coat"] },
                },
              ],
            }
          : project,
      ),
      { status: 200 },
    );
  });
  render(
    <ContinuityPanel
      project={project}
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText("选择设定"), {
    target: { value: "character-one" },
  });
  fireEvent.change(screen.getByLabelText("不变特征（每行一项）"), {
    target: { value: "blue coat" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览设定变更" }));
  await screen.findByText(/保留已选素材/);
  expect(requests).toHaveLength(1);
  expect(requests[0].body).toMatchObject({
    base_revision: 7,
    entity_type: "character",
    entity_id: "character-one",
    value: { invariant_traits: ["blue coat"] },
  });
  fireEvent.click(screen.getByRole("button", { name: "批准并应用" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual({
    url: "/api/v1/projects/project-one/continuity/plans/plan-one/execute",
    body: { base_revision: 7, approved: true },
  });
});

it("offers deterministic fields and requires consent before an LLM instruction", () => {
  render(
    <ContinuityPanel
      project={project}
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  expect(screen.getByRole("option", { name: "确定性字段编辑" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("编辑方式"), {
    target: { value: "llm" },
  });
  fireEvent.change(screen.getByLabelText("修改指令"), {
    target: { value: "Make this calmer" },
  });
  expect(
    (screen.getByRole("button", { name: "预览镜头修改" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByLabelText(/允许发送所选镜头/));
  expect(
    (screen.getByRole("button", { name: "预览镜头修改" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});

it("restores persisted plans and blocks approval after the project revision changes", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify([
        {
          id: "saved-plan",
          type: "semantic_edit",
          state: "proposed",
          base_revision: 6,
          affected_shot_ids: ["shot-one"],
          protected_shot_ids: [],
          changes: [
            {
              entity_id: "shot-one",
              field: "emotion",
              before: "calm",
              after: "quiet",
            },
          ],
        },
      ]),
      { status: 200 },
    ),
  );
  render(
    <ContinuityPanel
      project={project}
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "恢复待批准方案" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "镜头修改 · 版本 6" }),
  );
  expect(screen.getByRole("alert").textContent).toContain("重新预览");
  expect(
    (screen.getByRole("button", { name: "批准并应用" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("submits only explicit deterministic fields and selected shots for preview", async () => {
  let body: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    body = JSON.parse(String(init?.body));
    return new Response(
      JSON.stringify({
        id: "semantic-plan",
        type: "semantic_edit",
        base_revision: 7,
        affected_shot_ids: ["shot-one"],
        protected_shot_ids: ["shot-one"],
        changes: [
          {
            entity_id: "shot-one",
            field: "emotion",
            before: "calm",
            after: "quiet",
          },
        ],
      }),
      { status: 200 },
    );
  });
  render(
    <ContinuityPanel
      project={project}
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText("新的字段值"), {
    target: { value: "quiet" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览镜头修改" }));
  await screen.findByRole("article", { name: "变更影响预览" });
  expect(body).toEqual({
    base_revision: 7,
    shot_ids: ["shot-one"],
    mode: "structured",
    fields: { emotion: "quiet" },
  });
});
