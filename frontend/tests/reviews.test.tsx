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
