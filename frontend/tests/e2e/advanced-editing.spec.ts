import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("imports video, edits picture layers and transitions, and plays actual preview and film", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const scene = randomUUID();
  const shots = [randomUUID(), randomUUID()];
  const title = `DEMO advanced editing ${Date.now()}`;
  const created = await page.request.post("/api/v1/projects/import", {
    data: {
      title,
      brief: "DEMO / MOCK two-clip composition",
      format: { width: 320, height: 180 },
      scenes: [{ id: scene, order: 0, title: "DEMO scene", shot_ids: shots }],
      shots: shots.map((id, index) => ({
        id,
        scene_id: scene,
        order: index,
        title: `DEMO shot ${index + 1}`,
        duration_frames: 48,
      })),
      timeline: {
        tracks: [
          {
            id: "v1",
            type: "video",
            items: shots.map((shot_id, index) => ({
              shot_id,
              start_frame: index * 48,
              duration_frames: 48,
            })),
          },
        ],
      },
    },
  });
  expect(created.status()).toBe(201);
  let apr = await created.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  const latest = async () => (await page.request.get(`${root}/apr`)).json();
  const waitJob = async (id: string) =>
    expect
      .poll(
        async () =>
          (await (await page.request.get(`${root}/jobs/${id}`)).json()).state,
      )
      .toBe("completed");
  const generated = await page.request.post(`${root}/generate`, {
    data: {
      base_revision: apr.revision,
      shot_ids: apr.shots.map((shot: { id: string }) => shot.id),
      provider_id: "mock-video-local",
    },
  });
  expect(generated.status()).toBe(202);
  for (const job of await generated.json()) await waitJob(job.id);
  apr = await latest();
  const source = await page.request.get(
    `${root}/assets/${apr.asset_versions[0].asset_id}/file`,
  );
  expect(source.status()).toBe(200);
  await page.goto("/");
  await page.getByRole("button").filter({ hasText: title }).click();
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await page.getByLabel("本机视频", { exact: true }).setInputFiles({
    name: "DEMO-layer.mp4",
    mimeType: "video/mp4",
    buffer: await source.body(),
  });
  await page.getByLabel("确认有权使用本视频", { exact: true }).check();
  await page.getByRole("button", { name: "导入本机视频", exact: true }).click();
  let importId = "";
  await expect
    .poll(async () => {
      const jobs = await (await page.request.get(`${root}/jobs`)).json();
      const imported = jobs.find(
        (job: { operation: string }) => job.operation === "import_video",
      );
      importId = imported?.id || "";
      return imported?.state;
    })
    .toBe("completed");
  apr = await latest();
  const imported = apr.assets.find(
    (asset: { media_type: string; provenance: { name?: string } }) =>
      asset.media_type === "video" &&
      asset.provenance.name === "DEMO-layer.mp4",
  );
  expect(imported).toBeTruthy();
  expect(importId).toBeTruthy();
  await expect(
    page.getByText(new RegExp(`修订 ${apr.revision}$`)).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加视频轨", exact: true }).click();
  await page
    .getByRole("combobox", { name: "叠加素材", exact: true })
    .selectOption(imported.id);
  await page.getByRole("button", { name: "添加叠加片段", exact: true }).click();
  for (const label of ["水平位置", "垂直位置", "宽度", "高度"])
    await page.getByLabel(label, { exact: true }).fill("50");
  await page.getByRole("button", { name: "保存视频轨", exact: true }).click();
  await expect
    .poll(
      async () => (await latest()).timeline.video_layers[0]?.items[0]?.asset_id,
    )
    .toBe(imported.id);
  await page
    .getByRole("combobox", { name: "转场 1", exact: true })
    .selectOption("dissolve");
  await page.getByRole("button", { name: "预览转场变更", exact: true }).click();
  await expect(
    page.getByText("成片 84 帧，原长度 96 帧。", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "批准并保存剪辑", exact: true })
    .click();
  await expect
    .poll(async () => (await latest()).timeline.tracks[0].items[1].start_frame)
    .toBe(36);
  await page.getByLabel("预览开始帧", { exact: true }).fill("12");
  await page.getByLabel("预览时长帧", { exact: true }).fill("24");
  await page.getByRole("button", { name: "生成预览片段", exact: true }).click();
  const preview = page.getByLabel("剪辑预览视频", { exact: true });
  await expect(preview).toBeVisible();
  await expect
    .poll(() =>
      preview.evaluate((element) => (element as HTMLVideoElement).readyState),
    )
    .toBeGreaterThanOrEqual(2);
  await expect
    .poll(() =>
      preview.evaluate((element) => (element as HTMLVideoElement).duration),
    )
    .toBeCloseTo(1, 1);
  await preview.evaluate((element) => (element as HTMLVideoElement).play());
  await page.getByRole("button", { name: "渲染成片", exact: true }).click();
  let renderId = "";
  await expect
    .poll(async () => {
      const jobs = await (await page.request.get(`${root}/jobs`)).json();
      const rendered = jobs.find(
        (job: { operation: string }) => job.operation === "render_timeline",
      );
      renderId = rendered?.id || "";
      return rendered?.state;
    })
    .toBe("completed");
  apr = await latest();
  const film = apr.assets.find(
    (asset: { id: string }) => asset.id === apr.render_asset_id,
  );
  expect(film.duration_frames).toBe(84);
  expect(renderId).toBeTruthy();
  await expect(
    page.getByText("00:03.5", { exact: true }).first(),
  ).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 MP4", exact: true }).click();
  await (await downloaded).saveAs("../data/advanced-editing-output.mp4");
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await page
    .getByRole("region", { name: "视频合成编辑器", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../data/advanced-editing-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("region", { name: "视频合成编辑器", exact: true })
    .scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "../data/advanced-editing-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
