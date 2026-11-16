import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("shares a canvas across independent browsers without changing the film revision", async ({ page, browser }) => {
  const scene = randomUUID();
  const shot = randomUUID();
  const title = `DEMO portable workspace ${Date.now()}`;
  const created = await page.request.post("/api/v1/projects/import", {
    data: {
      title, brief: "DEMO / MOCK workspace", format: { width: 320, height: 180 },
      scenes: [{ id: scene, order: 0, title: "DEMO scene", shot_ids: [shot] }],
      shots: [{ id: shot, scene_id: scene, order: 0, title: "DEMO shot", duration_frames: 48 }],
      timeline: { tracks: [{ id: "v1", type: "video", items: [{ shot_id: shot, start_frame: 0, duration_frames: 48 }] }] },
    },
  });
  expect(created.status()).toBe(201);
  const apr = await created.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  await page.goto("/");
  await page.getByRole("button").filter({ hasText: title }).click();
  await expect(page.getByText("画布已保存", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加文字", exact: true }).click();
  const note = page.getByRole("textbox", { name: /^文字内容 / });
  await note.fill("DEMO 跨浏览器制作便签");
  await expect.poll(async () => {
    const saved = await (await page.request.get(`${root}/workspace`)).json();
    return saved.document.nodes[0]?.text;
  }).toBe("DEMO 跨浏览器制作便签");
  expect((await (await page.request.get(`${root}/apr`)).json()).revision).toBe(apr.revision);
  const second = await browser.newContext();
  try {
    const other = await second.newPage();
    await other.goto(new URL("/", page.url()).href);
    await other.getByRole("button").filter({ hasText: title }).click();
    await expect(other.getByRole("textbox", { name: /^文字内容 / })).toHaveValue("DEMO 跨浏览器制作便签");
    const local = await other.evaluate((id) => JSON.parse(localStorage.getItem(`afds-canvas-${id}`) || "{}"), apr.project_id);
    expect(local.nodes[0].text).toBe("DEMO 跨浏览器制作便签");
  } finally {
    await second.close();
  }
});
