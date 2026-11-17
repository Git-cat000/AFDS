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
function setup(containerAvailable = true, running = false) {
  const project = {
    project_id: "project",
    revision: 7,
    production_guidance: {},
    shots: [],
  } as unknown as Project;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
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
      data = {
        items: running
          ? [
              {
                id: "script-job",
                state: "running",
                operation: "run_skill_script",
                source_revision: 7,
                request: {
                  execution_mode: "container",
                  script_path: "scripts/inspect.py",
                },
              },
            ]
          : [],
        total: running ? 1 : 0,
        offset: 0,
      };
    else if (url.endsWith("/guidance/preview"))
      data = { changes: [], affected_shot_ids: [] };
    else if (url.endsWith("/scripts/preview")) {
      const mode = JSON.parse(String(init?.body)).execution_mode;
      data = {
        id: "script-plan",
        base_revision: 7,
        fingerprint: identifier,
        source_code: "print('DEMO approved')",
        runtime: {
          name: "Python",
          available: mode !== "container" || containerAvailable,
          error:
            mode === "container" && !containerAvailable
              ? { code: "SANDBOX_UNAVAILABLE", message: "DEMO Docker offline" }
              : null,
        },
        request: {
          execution_mode: mode,
          execution_policy_sha256: identifier,
          execution_policy:
            mode === "container"
              ? {
                  image_id: `sha256:${identifier}`,
                  cpu_limit: 1,
                  memory_bytes: 536870912,
                  pids_limit: 64,
                  work_bytes: 67108864,
                  output_bytes: 33554432,
                  network: "none",
                }
              : null,
          package_id: identifier,
          script_path: "scripts/inspect.py",
          arguments: [],
          stage: "shots",
          timeout_seconds: 60,
          context: { stage: "shots" },
        },
      };
    } else if (url.endsWith("/execute"))
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

it("shows pinned container limits and requires container-specific consent", async () => {
  const { fetcher } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "脚本与运行" }));
  fireEvent.change(screen.getByRole("combobox", { name: "执行环境" }), {
    target: { value: "container" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览脚本运行" }));
  await screen.findByText(/512 MiB/);
  expect(bodies(fetcher, "/scripts/preview")[0].execution_mode).toBe(
    "container",
  );
  const approve = screen.getByRole("button", { name: "批准并运行本次脚本" });
  expect((approve as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "已审阅源码和策略，允许在隔离容器中运行本次脚本",
    }),
  );
  fireEvent.click(approve);
  await waitFor(() => expect(bodies(fetcher, "/execute")).toHaveLength(1));
});

it("preserves unavailable container preview with its reason and disables approval", async () => {
  setup(false);
  fireEvent.click(await screen.findByRole("button", { name: "脚本与运行" }));
  fireEvent.change(screen.getByRole("combobox", { name: "执行环境" }), {
    target: { value: "container" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览脚本运行" }));
  await screen.findByText(/DEMO Docker offline/);
  expect(screen.getByLabelText("待运行脚本源码").textContent).toContain(
    "DEMO approved",
  );
  expect(
    (
      screen.getByRole("button", {
        name: "批准并运行本次脚本",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});

it("clears prior consent and preview when execution mode changes", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "脚本与运行" }));
  fireEvent.click(screen.getByRole("button", { name: "预览脚本运行" }));
  fireEvent.click(
    await screen.findByRole("checkbox", {
      name: "已审阅源码和参数，允许以本机用户权限运行本次脚本",
    }),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "执行环境" }), {
    target: { value: "container" },
  });
  expect(
    screen.queryByRole("button", { name: "批准并运行本次脚本" }),
  ).toBeNull();
});

it("cancels a running skill through the POST cancellation endpoint", async () => {
  const { fetcher } = setup(true, true);
  fireEvent.click(await screen.findByRole("button", { name: "脚本与运行" }));
  fireEvent.click(await screen.findByRole("button", { name: "取消此次运行" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([url]) => url.endsWith("/cancel"))).toBe(
      true,
    ),
  );
  const call = fetcher.mock.calls.find(([url]) => url.endsWith("/cancel"));
  expect(call?.[1]?.method).toBe("POST");
});
