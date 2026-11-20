import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewPanel } from "../src/ReviewPanel";
import type { Project } from "../src/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

it("retains a vision review identity and preview after an unknown response", async () => {
  let previews = 0;
  const requests: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, options) => {
    if (String(url).endsWith("/routing/preview")) {
      previews++;
      return new Response(JSON.stringify({ id: "DEMO-pinned-preview" }));
    }
    if (String(url).endsWith("/reviews") && options?.method === "POST") {
      requests.push(JSON.parse(String(options.body)));
      if (requests.length === 1) throw new Error("DEMO response lost");
    }
    return new Response("[]");
  });
  const project = {
    project_id: "review-retry",
    revision: 4,
    shots: [
      {
        id: "s1",
        title: "DEMO",
        order: 0,
        active_asset_version_id: "v1",
        status: "ready",
      },
    ],
  } as unknown as Project;
  const errors: unknown[] = [];
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewPanel
        project={project}
        settings={
          {
            providers: [{ kind: "llm", enabled: true, vision_enabled: true }],
          } as never
        }
        busy={false}
        run={async (action) => {
          try {
            await action();
          } catch (error) {
            errors.push(error);
          }
        }}
      />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("审片方式"), {
    target: { value: "vision" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /允许发送所选镜头/ }));
  fireEvent.click(screen.getByRole("button", { name: "提交审片" }));
  await waitFor(() => expect(errors).toHaveLength(1));
  fireEvent.click(screen.getByRole("button", { name: "提交审片" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(previews).toBe(1);
  expect(requests[0].request_id).toBeTruthy();
  expect(requests[1]).toEqual(requests[0]);
});

it("keeps an unavailable requested shot unselected instead of reviewing another shot", () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => new Response("[]"),
  );
  const project = {
    project_id: "p1",
    revision: 4,
    shots: [
      {
        id: "ready",
        title: "Ready",
        order: 0,
        active_asset_version_id: "v1",
        status: "ready",
      },
      {
        id: "pending",
        title: "Pending",
        order: 1,
        active_asset_version_id: null,
        status: "draft",
      },
    ],
  } as unknown as Project;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewPanel
        project={project}
        initialShotId="pending"
        busy={false}
        run={async (action) => {
          await action();
        }}
      />
    </QueryClientProvider>,
  );
  expect(
    (screen.getByRole("checkbox", { name: "1 · Ready" }) as HTMLInputElement)
      .checked,
  ).toBe(false);
  expect(
    (screen.getByRole("button", { name: "提交审片" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
it("keeps vision review disabled without opt-in and submits an offline evidence review", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("[]", { status: 200 }));
  const project = {
    project_id: "p1",
    revision: 4,
    shots: [
      {
        id: "s1",
        title: "Arrival",
        active_asset_version_id: "v1",
        status: "ready",
        order: 0,
      },
    ],
    assets: [],
    asset_versions: [],
  } as unknown as Project;
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewPanel
        project={project}
        busy={false}
        run={async (action) => {
          await action();
        }}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText(/不会自动重做镜头/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "提交审片" }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
      true,
    ),
  );
  const sent = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(sent[1]?.body))).toMatchObject({
    base_revision: 4,
    shot_ids: ["s1"],
    mode: "deterministic",
    allow_remote: false,
  });
  fireEvent.change(screen.getByLabelText("审片方式"), {
    target: { value: "vision" },
  });
  expect(
    (screen.getByRole("button", { name: "提交审片" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
