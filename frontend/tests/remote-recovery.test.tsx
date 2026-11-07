import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { RemoteJobRecovery } from "../src/RemoteJobRecovery";
import type { Job } from "../src/types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("requires a separate acknowledgement before another potentially charged generation", async () => {
  let submitted: unknown = null;
  vi.stubGlobal("fetch", async (_url: string, options: RequestInit) => {
    submitted = JSON.parse(String(options.body));
    return new Response("{}", { status: 200 });
  });
  const job = {
    id: "job",
    provider_id: "openai-video",
    state: "failed",
    external_task_id: "old-video",
  } as Job;
  render(
    <RemoteJobRecovery
      job={job}
      projectId="project"
      busy={false}
      run={async (action) => {
        await action();
      }}
    />,
  );
  fireEvent.click(screen.getByText("远端任务恢复与重新生成"));
  const button = screen.getByRole("button", {
    name: "重新提交生成",
  }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  await waitFor(() =>
    expect(submitted).toEqual({ restart_remote: true, allow_remote: true }),
  );
});
