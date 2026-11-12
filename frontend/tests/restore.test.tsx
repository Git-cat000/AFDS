import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BackupRestorePanel } from "../src/BackupRestorePanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uploads the raw archive and retains request identity after an uncertain response", async () => {
  const onQueued = vi.fn();
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Network interrupted"))
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: "job-1",
        project_id: "copy-1",
        state: "queued",
      }),
    });
  vi.stubGlobal("fetch", fetcher);
  render(<BackupRestorePanel onQueued={onQueued} />);
  const file = new File(["DEMO ZIP"], "film.zip", { type: "application/zip" });
  fireEvent.change(screen.getByLabelText("工程包 ZIP"), {
    target: { files: [file] },
  });
  fireEvent.change(screen.getByLabelText("恢复副本名称"), {
    target: { value: "恢复副本" },
  });
  fireEvent.click(screen.getByRole("button", { name: "恢复为新项目" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "恢复为新项目" }));
  await waitFor(() =>
    expect(onQueued).toHaveBeenCalledWith(
      expect.objectContaining({ project_id: "copy-1" }),
    ),
  );
  expect(fetcher.mock.calls[0][0]).toBe(fetcher.mock.calls[1][0]);
  expect(fetcher.mock.calls[1][1].body).toBe(file);
  expect(fetcher.mock.calls[1][1].headers).toEqual({
    "Content-Type": "application/zip",
  });
  expect(screen.getByText(/不恢复.*密钥/)).toBeTruthy();
});

it("blocks oversized archives before uploading", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<BackupRestorePanel onQueued={vi.fn()} />);
  const file = new File(["ZIP"], "huge.zip");
  Object.defineProperty(file, "size", { value: 2 * 1024 ** 3 + 1 });
  fireEvent.change(screen.getByLabelText("工程包 ZIP"), {
    target: { files: [file] },
  });
  expect(
    screen
      .getByRole("button", { name: "恢复为新项目" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(fetcher).not.toHaveBeenCalled();
});
