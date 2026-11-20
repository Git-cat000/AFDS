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

it("retains the approved route preview with the same uncertain planning request", async () => {
  const bodies: Record<string, unknown>[] = [];
  let previews = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/routing/preview")) {
        previews += 1;
        return new Response(JSON.stringify({ id: "demo-route" }));
      }
      bodies.push(JSON.parse(String(options?.body)));
      if (bodies.length === 1) throw new TypeError("network interrupted");
      return new Response(JSON.stringify({ id: "demo-job", state: "queued" }));
    }),
  );
  const body = {
    base_revision: 4,
    message: "DEMO",
    provider_id: "demo-llm",
    allow_remote: true,
  };
  await expect(queuePlanning("demo", "storyboard", body)).rejects.toThrow(
    "network",
  );
  await queuePlanning("demo", "storyboard", body);
  expect(previews).toBe(1);
  expect(bodies[0].route_preview_id).toBe("demo-route");
  expect(bodies[1]).toEqual(bodies[0]);
});

it("keeps the original revision while retrying an uncertain planning submission", async () => {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, options?: RequestInit) => {
      bodies.push(JSON.parse(String(options?.body)));
      if (bodies.length === 1) throw new TypeError("network interrupted");
      return new Response(JSON.stringify({ id: "DEMO-job", state: "queued" }));
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
  await queuePlanning("demo", "storyboard", { ...body, base_revision: 5 });
  expect(bodies[1]).toEqual(bodies[0]);
});
