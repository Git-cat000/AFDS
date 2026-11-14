import { afterEach, expect, it, vi } from "vitest";
import { queuePlanning } from "../src/history";

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});
it("keeps the planning request identity after an uncertain response", async () => {
  const bodies: { request_id: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1) throw new TypeError("network interrupted");
      return new Response(JSON.stringify({ id: "job", state: "queued" }));
    }),
  );
  const body = {
    base_revision: 4,
    message: "DEMO",
    provider_id: "mock-director",
  };
  await expect(queuePlanning("demo", "storyboard", body)).rejects.toThrow(
    "network",
  );
  await queuePlanning("demo", "storyboard", body);
  expect(bodies[0].request_id).toBe(bodies[1].request_id);
  expect(sessionStorage.getItem("afds-planning-demo-storyboard")).toBeNull();
});
