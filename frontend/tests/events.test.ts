import { afterEach, expect, it, vi } from "vitest";
import { fetchEventHistory } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

it("loads event history beyond the first 200 entries and supports resuming", async () => {
  vi.stubGlobal("fetch", async (url: string) => {
    const cursor = Number(
      new URL(url, "http://localhost").searchParams.get("after"),
    );
    const last = Math.min(cursor + 200, 330);
    const events = Array.from({ length: last - cursor }, (_, i) => ({
      event_id: cursor + i + 1,
      type: "job.completed",
      timestamp: 0,
    }));
    return new Response(JSON.stringify(events), { status: 200 });
  });
  const all = await fetchEventHistory("project");
  expect(all).toHaveLength(330);
  expect(all.at(-1)?.event_id).toBe(330);
  const newer = await fetchEventHistory("project", 325);
  expect(newer.map((e) => e.event_id)).toEqual([326, 327, 328, 329, 330]);
});
