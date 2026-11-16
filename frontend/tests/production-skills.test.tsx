import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionSkills } from "../src/ProductionSkills";
import type { Project } from "../src/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const identifier = "a".repeat(64);
const skill = {
  id: identifier,
  name: "external-camera",
  description: "DEMO camera guide",
  version: "1",
  source: "DEMO external",
  builtin: false,
  license: "MIT",
  compatibility: "Python",
  stages: ["shots"],
  instructions: "DEMO preserve eyelines",
  resources: [
    { path: "SKILL.md", size: 100, sha256: identifier },
    { path: "references/framing.md", size: 50, sha256: identifier },
  ],
  scripts: [{ path: "scripts/inspect.py", size: 50, sha256: identifier }],
};
function setup() {
  const project = {
    project_id: "project",
    revision: 7,
    production_guidance: {},
    shots: [],
  } as unknown as Project;
  const fetcher = vi.fn(async (url: string) => {
    let data: unknown = {};
    if (url.includes("/skills?"))
      data = {
        stages: { shots: "镜头设计" },
        items: [skill],
        total: 1,
        offset: 0,
      };
    else if (url.endsWith(`/skills/${identifier}`)) data = skill;
    else if (url.includes("/skills/runs?"))
      data = { items: [], total: 0, offset: 0 };
    else if (url.endsWith("/guidance/preview"))
      data = { changes: [], affected_shot_ids: [] };
    else if (url.endsWith("/scripts/preview"))
      data = {
        id: "script-plan",
        base_revision: 7,
        fingerprint: identifier,
        source_code: "print('DEMO approved')",
        runtime: { name: "Python", available: true },
        request: {
          package_id: identifier,
          script_path: "scripts/inspect.py",
          arguments: [],
          stage: "shots",
          timeout_seconds: 60,
          context: { stage: "shots" },
        },
      };
    else if (url.endsWith("/execute"))
      data = { id: "script-job", state: "queued" };
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (value: Project) => (
    <QueryClientProvider client={client}>
      <ProductionSkills
        project={value}
        initialStage="shots"
        busy={false}
        run={async (action) => {
          await action();
        }}
      />
    </QueryClientProvider>
  );
  const view = render(tree(project));
  return {
    fetcher,
    project,
    refresh: (value: Project) => view.rerender(tree(value)),
  };
}
function bodies(fetcher: ReturnType<typeof setup>["fetcher"], ending: string) {
  return fetcher.mock.calls
    .filter(([url]) => url.endsWith(ending))
    .map((call) =>
      JSON.parse(String((call as unknown as [string, RequestInit])[1].body)),
    );
}

it("previews stage guidance before approving its captured source revision", async () => {
  const { fetcher } = setup();
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "启用 external-camera" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "预览技能配置" }));
  await screen.findByRole("button", { name: "批准技能配置" });
  expect(bodies(fetcher, "/guidance/commit")).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "批准技能配置" }));
  await waitFor(() =>
    expect(bodies(fetcher, "/guidance/commit")).toHaveLength(1),
  );
  expect(bodies(fetcher, "/guidance/commit")[0]).toMatchObject({
    base_revision: 7,
    approved: true,
    selections: { shots: [{ package_id: identifier, resources: [] }] },
  });
});

it("blocks a dirty guidance draft after the project revision changes", async () => {
  const { project, refresh } = setup();
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "启用 external-camera" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "预览技能配置" }));
  await screen.findByRole("button", { name: "批准技能配置" });
  refresh({ ...project, revision: 8 });
  expect(
    (screen.getByRole("button", { name: "批准技能配置" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "载入最新技能配置" }));
  expect(
    (
      screen.getByRole("checkbox", {
        name: "启用 external-camera",
      }) as HTMLInputElement
    ).checked,
  ).toBe(false);
});

it("requires an exact script preview and local-permission acknowledgement before execution", async () => {
  const { fetcher } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "脚本与运行" }));
  fireEvent.click(screen.getByRole("button", { name: "预览脚本运行" }));
  const approve = await screen.findByRole("button", {
    name: "批准并运行本次脚本",
  });
  expect((approve as HTMLButtonElement).disabled).toBe(true);
  expect(bodies(fetcher, "/execute")).toEqual([]);
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "已审阅源码和参数，允许以本机用户权限运行本次脚本",
    }),
  );
  fireEvent.click(approve);
  await waitFor(() => expect(bodies(fetcher, "/execute")).toHaveLength(1));
  expect(bodies(fetcher, "/execute")[0]).toEqual({
    base_revision: 7,
    approved: true,
    fingerprint: identifier,
  });
});

it("discards a script preview when its argument draft changes", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "脚本与运行" }));
  fireEvent.click(screen.getByRole("button", { name: "预览脚本运行" }));
  await screen.findByRole("button", { name: "批准并运行本次脚本" });
  fireEvent.change(screen.getByLabelText("脚本参数 JSON"), {
    target: { value: '["changed"]' },
  });
  expect(
    screen.queryByRole("button", { name: "批准并运行本次脚本" }),
  ).toBeNull();
});
