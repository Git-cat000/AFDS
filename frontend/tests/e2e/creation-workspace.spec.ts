import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("arranges a canvas, compares candidates and records source-bound feedback and references", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const scene = randomUUID(),
    shot = randomUUID(),
    title = `DEMO canvas ${Date.now()}`;
  const imported = await page.request.post("/api/v1/projects/import", {
    data: {
      title,
      brief: "DEMO / MOCK creation workspace",
      format: { width: 320, height: 180, fps_num: 24, fps_den: 1 },
      scenes: [{ id: scene, order: 0, title: "DEMO", shot_ids: [shot] }],
      shots: [
        {
          id: shot,
          scene_id: scene,
          order: 0,
          title: "DEMO canvas shot",
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
  expect(imported.status()).toBe(201);
  let apr = await imported.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  const latest = async () => (await page.request.get(`${root}/apr`)).json();
  const generate = async () => {
    apr = await latest();
    const response = await page.request.post(`${root}/generate`, {
      data: {
        base_revision: apr.revision,
        shot_ids: [apr.shots[0].id],
        provider_id: "mock-video-local",
      },
    });
    expect(response.status()).toBe(202);
    const jobs = await response.json();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${root}/jobs/${jobs[0].id}`)).json())
            .state,
      )
      .toBe("completed");
    apr = await latest();
  };
  await generate();
  const changed = await page.request.post(`${root}/changesets/commit`, {
    data: {
      base_revision: apr.revision,
      approved: true,
      reason: "DEMO second candidate",
      operations: [
        {
          op: "set",
          entity_type: "shot",
          entity_id: apr.shots[0].id,
          field: "status",
          value: "approved",
        },
        {
          op: "set",
          entity_type: "shot",
          entity_id: apr.shots[0].id,
          field: "action",
          value: "DEMO Walk towards the camera",
        },
      ],
    },
  });
  expect(changed.ok()).toBeTruthy();
  await generate();
  await page.goto("/");
  await page.getByRole("button").filter({ hasText: title }).click();
  const handle = page.getByRole("button", { name: "移动 DEMO canvas shot" });
  const positionedNode = page
    .locator(".react-flow__node")
    .filter({ has: page.getByTestId("shot-card") });
  const before = await positionedNode.getAttribute("style");
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    box.x + box.width / 2 + 70,
    box.y + box.height / 2 + 35,
    { steps: 5 },
  );
  await page.mouse.up();
  await expect(positionedNode).not.toHaveAttribute("style", before!);
  await page.getByRole("button", { name: "缩小画布" }).click();
  await expect(page.getByText("90%", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("90%", { exact: true })).toBeVisible();
  const previousVersion = apr.shots[0].active_asset_version_id;
  const replacement = apr.asset_versions.find(
    (v: { id: string }) => v.id !== previousVersion,
  );
  await page.getByLabel("候选 B", { exact: true }).selectOption(replacement.id);
  const beforePreview = (await latest()).revision;
  await page.getByRole("button", { name: "预览选用候选 B" }).click();
  await expect(
    page.getByRole("button", { name: "批准创作修改" }),
  ).toBeVisible();
  expect((await latest()).revision).toBe(beforePreview);
  await page.getByRole("button", { name: "批准创作修改" }).click();
  await expect
    .poll(async () => (await latest()).shots[0].active_asset_version_id)
    .toBe(replacement.id);
  await page.getByLabel("候选 A", { exact: true }).selectOption(replacement.id);
  await page
    .getByLabel("候选 B", { exact: true })
    .selectOption(previousVersion);
  await expect(page.locator(".candidate video")).toHaveCount(2);
  await page.getByRole("button", { name: "从头同步播放" }).click();
  await page.getByRole("button", { name: "暂停比较" }).click();
  await page.getByText("人工评价与迭代记录", { exact: true }).click();
  await page.getByLabel("评价与改进建议").fill("DEMO motion needs refinement");
  await page.getByRole("button", { name: "确认记录人工评价" }).click();
  await expect(page.locator(".feedback-record")).toContainText(
    "DEMO motion needs refinement",
  );
  await expect(page.locator(".feedback-record")).toContainText(
    "对应当前选中素材",
  );
  await page.getByText("参考图与首尾帧", { exact: true }).click();
  const importPanel = page.locator(".shot-inspector .image-import");
  await importPanel.locator("summary").click();
  const poster = await page.request.get(
    `${root}/assets/${apr.asset_versions.at(-1).poster_asset_id}/file`,
  );
  await importPanel.getByLabel("参考图文件").setInputFiles({
    name: "DEMO-reference.jpg",
    mimeType: "image/jpeg",
    buffer: await poster.body(),
  });
  await importPanel.getByLabel("我有权使用并上传该图像").check();
  await importPanel.getByRole("button", { name: "上传并导入图像" }).click();
  await expect(importPanel).toContainText("参考图已加入素材库");
  apr = await latest();
  const image = apr.assets.find(
    (a: { provenance: { name?: string } }) =>
      a.provenance.name === "DEMO-reference.jpg",
  );
  const approvedShot = await page.request.post(`${root}/changesets/commit`, {
    data: {
      base_revision: apr.revision,
      approved: true,
      reason: "DEMO protected references",
      operations: [
        {
          op: "set",
          entity_type: "shot",
          entity_id: apr.shots[0].id,
          field: "status",
          value: "approved",
        },
      ],
    },
  });
  expect(approvedShot.ok()).toBeTruthy();
  await page.getByRole("button", { name: "刷新项目" }).click();
  await expect(page.getByTestId("shot-card")).toContainText("已批准");
  await page.getByRole("button", { name: "打开参考素材", exact: true }).click();
  await expect(page.getByLabel("镜头创作画布")).toBeVisible();
  await page
    .locator(`.asset-card[data-asset-id="${image.id}"]`)
    .getByRole("button", { name: "用作当前镜头参考图" })
    .click();
  const assignment = page.getByRole("dialog", { name: "将图像用于镜头" });
  await assignment.getByRole("button", { name: "预览图像关联" }).click();
  await assignment.getByRole("button", { name: "批准关联到镜头" }).click();
  await expect(page.locator(".node-references img")).toHaveCount(1);
  await expect(page.locator(".feedback-record")).toContainText("历史候选评价");
  await page.getByRole("button", { name: "审看所选镜头" }).click();
  await expect(
    page.getByRole("checkbox", { name: "1 · DEMO canvas shot" }),
  ).toBeChecked();
  await expect(page.getByText("90%", { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../data/workbench-flow-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "回到镜头候选" }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../data/creation-canvas-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "运行概览", exact: true }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载诊断报告" }).click();
  await (await downloadPromise).saveAs("../data/creation-diagnostics.json");
  await page.getByRole("button", { name: "预览存储清理" }).click();
  await expect(page.getByLabel("清理预览")).toContainText("可清理 0 个文件");
  await page.getByRole("button", { name: "分镜工作室", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "网格", exact: true }).click();
  await page.getByRole("button", { name: "编辑镜头", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.screenshot({
    path: "../data/creation-canvas-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "编辑镜头音画" }).click();
  await expect(page.getByLabel("创作工具面板")).toBeInViewport();
  await page.screenshot({
    path: "../data/workbench-flow-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
