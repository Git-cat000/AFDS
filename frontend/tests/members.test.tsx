import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectMembers } from "../src/ProjectMembers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(canManage: boolean) {
  const fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify([
          {
            id: "owner",
            username: "demo-owner",
            display_name: "DEMO owner",
            role: "owner",
          },
          {
            id: "editor",
            username: "demo-editor",
            display_name: "DEMO editor",
            role: "editor",
          },
        ]),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ProjectMembers projectId="project" canManage={canManage} />
    </QueryClientProvider>,
  );
  return fetcher;
}

it("lets an owner add a registered username with an explicit project role", async () => {
  const fetcher = setup(true);
  await screen.findByText("DEMO editor");
  fireEvent.change(screen.getByLabelText("成员用户名"), {
    target: { value: "demo-viewer" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "新增成员角色" }), {
    target: { value: "viewer" },
  });
  fireEvent.click(screen.getByRole("button", { name: "添加成员" }));
  await waitFor(() =>
    expect(
      (fetcher.mock.calls as unknown as [string, RequestInit][]).some(
        ([url, options]) =>
          url.endsWith("/members") &&
          options.method === "POST" &&
          JSON.parse(String(options.body)).role === "viewer",
      ),
    ).toBe(true),
  );
});

it("shows membership to readers with management controls disabled", async () => {
  setup(false);
  await screen.findByText("DEMO editor");
  expect(screen.queryByRole("button", { name: "添加成员" })).toBeNull();
  expect(
    (
      screen.getByRole("combobox", {
        name: "DEMO editor的角色",
      }) as HTMLSelectElement
    ).disabled,
  ).toBe(true);
});
