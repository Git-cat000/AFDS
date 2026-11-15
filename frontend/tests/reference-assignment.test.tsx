import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceAssignment } from "../src/ReferenceAssignment";
import type { Project } from "../src/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function setup(remove = false) {
  const project = {
    project_id: "project",
    revision: 7,
    shots: [
      {
        id: "shot",
        title: "DEMO target",
        reference_images: remove
          ? { start_image: "image", end_image: "other" }
          : { start_image: "start" },
      },
    ],
    assets: [
      {
        id: "image",
        media_type: "image",
        provenance: { name: "DEMO reference" },
      },
    ],
  } as unknown as Project;
  const fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          changes: [],
          affected_shot_ids: ["shot"],
          protected_shot_ids: ["shot"],
          render_invalidated: true,
        }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const complete = vi.fn();
  const tree = (value: Project) => (
    <ReferenceAssignment
      project={value}
      assetId="image"
      shotId="shot"
      run={async (action) => {
        await action();
      }}
      busy={false}
      complete={complete}
      initialRole={remove ? "start_image" : undefined}
      remove={remove}
    />
  );
  const view = render(tree(project));
  return {
    project,
    fetcher,
    complete,
    refresh: (value: Project) => view.rerender(tree(value)),
  };
}
it("reuses an image as a shot input only after preview and explicit approval while preserving other roles", async () => {
  const { fetcher, complete } = setup();
  fireEvent.change(screen.getByLabelText("图像用途"), {
    target: { value: "end_image" },
  });
  fireEvent.click(screen.getByRole("button", { name: "预览图像关联" }));
  await screen.findByRole("button", { name: "批准关联到镜头" });
  expect(fetcher.mock.calls).toHaveLength(1);
  const previewCall = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(previewCall[0]).toContain("/changesets/preview");
  expect(JSON.parse(String(previewCall[1].body)).operations[0].value).toEqual({
    start_image: "start",
    end_image: "image",
  });
  fireEvent.click(screen.getByRole("button", { name: "批准关联到镜头" }));
  await waitFor(() => expect(complete).toHaveBeenCalledOnce());
  const commit = fetcher.mock.calls[1] as unknown as [string, RequestInit];
  expect(commit[0]).toContain("/changesets/commit");
  expect(JSON.parse(String(commit[1].body))).toMatchObject({
    base_revision: 7,
    approved: true,
  });
});

it("previews removal of one effective input and blocks removing a newer replacement", async () => {
  const { project, fetcher, refresh } = setup(true);
  fireEvent.click(screen.getByRole("button", { name: "预览移除图像关联" }));
  await screen.findByRole("button", { name: "批准移除镜头输入" });
  const preview = JSON.parse(
    String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body),
  );
  expect(preview.operations[0].value).toEqual({ end_image: "other" });
  refresh({
    ...project,
    revision: 8,
    shots: [
      {
        ...project.shots[0],
        reference_images: { start_image: "other", end_image: "other" },
      },
    ],
  });
  fireEvent.click(screen.getByRole("button", { name: "载入最新镜头设置" }));
  expect(
    screen.getByText("该镜头当前已使用其它图像，请重新选择连线。"),
  ).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: "预览移除图像关联",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
it("blocks a stale assignment and explicitly reloads the latest reference map", async () => {
  const { project, refresh, fetcher } = setup();
  fireEvent.click(screen.getByRole("button", { name: "预览图像关联" }));
  await screen.findByRole("button", { name: "批准关联到镜头" });
  refresh({
    ...project,
    revision: 8,
    shots: [
      { ...project.shots[0], reference_images: { start_image: "image" } },
    ],
  });
  expect(
    (
      screen.getByRole("button", {
        name: "批准关联到镜头",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "载入最新镜头设置" }));
  fireEvent.click(screen.getByRole("button", { name: "预览图像关联" }));
  await waitFor(() => expect(fetcher.mock.calls).toHaveLength(2));
  const current = JSON.parse(
    String((fetcher.mock.calls[1] as unknown as [string, RequestInit])[1].body),
  );
  expect(current.base_revision).toBe(8);
  expect(current.operations[0].value).toMatchObject({
    start_image: "image",
    reference_image: "image",
  });
});
