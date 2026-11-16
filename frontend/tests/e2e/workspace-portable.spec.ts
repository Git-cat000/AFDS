import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("shares a canvas across independent browsers without changing the film revision", async ({
  page,
  browser,
}) => {
  const scene = randomUUID();
  const shot = randomUUID();
  const title = `DEMO portable workspace ${Date.now()}`;
  const created = await page.request.post("/api/v1/projects/import", {
    data: {
      title,
      brief: "DEMO / MOCK workspace",
      format: { width: 320, height: 180 },
      scenes: [{ id: scene, order: 0, title: "DEMO scene", shot_ids: [shot] }],
      shots: [
        {
          id: shot,
          scene_id: scene,
          order: 0,
          title: "DEMO shot",
          duration_frames: 48,
        },
      ],
      timeline: {
        tracks: [
          {
            id: "v1",
            type: "video",
            items: [{ shot_id: shot, start_frame: 0, duration_frames: 48 }],
          },
        ],
      },
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
  await expect
    .poll(async () => {
      const saved = await (await page.request.get(`${root}/workspace`)).json();
      return saved.document.nodes[0]?.text;
    })
    .toBe("DEMO 跨浏览器制作便签");
  expect((await (await page.request.get(`${root}/apr`)).json()).revision).toBe(
    apr.revision,
  );
  const second = await browser.newContext();
  try {
    const other = await second.newPage();
    await other.goto(new URL("/", page.url()).href);
    await other.getByRole("button").filter({ hasText: title }).click();
    await expect(
      other.getByRole("textbox", { name: /^文字内容 / }),
    ).toHaveValue("DEMO 跨浏览器制作便签");
    const local = await other.evaluate(
      (id) => JSON.parse(localStorage.getItem(`afds-canvas-${id}`) || "{}"),
      apr.project_id,
    );
    expect(local.nodes[0].text).toBe("DEMO 跨浏览器制作便签");
  } finally {
    await second.close();
  }
  const generated = await page.request.post(`${root}/generate`, {
    data: {
      base_revision: apr.revision,
      shot_ids: [apr.shots[0].id],
      provider_id: "mock-video-local",
    },
  });
  expect(generated.status()).toBe(202);
  const videoJob = (await generated.json())[0];
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`${root}/jobs/${videoJob.id}`)).json())
          .state,
    )
    .toBe("completed");
  await page.getByRole("button", { name: "导出工程包", exact: true }).click();
  let exportId = "";
  await expect
    .poll(async () => {
      const jobs = await (await page.request.get(`${root}/jobs`)).json();
      const exported = jobs.find(
        (job: { operation: string }) => job.operation === "export_project",
      );
      exportId = exported?.id || "";
      return exported?.state;
    })
    .toBe("completed");
  const backup = await page.request.get(`${root}/exports/${exportId}/file`);
  expect(backup.status()).toBe(200);
  const restoredTitle = `${title} restored`;
  await page.getByRole("button", { name: "返回项目列表", exact: true }).click();
  await page
    .getByLabel("工程包 ZIP")
    .setInputFiles({
      name: "workspace.zip",
      mimeType: "application/zip",
      buffer: await backup.body(),
    });
  await page.getByLabel("恢复副本名称").fill(restoredTitle);
  await page.getByRole("button", { name: "恢复为新项目", exact: true }).click();
  await expect(page.getByRole("textbox", { name: /^文字内容 / })).toHaveValue(
    "DEMO 跨浏览器制作便签",
  );
  await page.screenshot({
    path: "../data/product-workspace-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "../data/product-workspace-mobile.png",
    fullPage: true,
  });
});
