import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

function demoWave() {
  const samples = 48000,
    rate = 24000,
    buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF");
  buffer.writeUInt32LE(buffer.length - 8, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24);
  buffer.writeUInt32LE(rate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++)
    buffer.writeInt16LE(
      Math.round(Math.sin((2 * Math.PI * 220 * i) / rate) * 3000),
      44 + i * 2,
    );
  return buffer;
}

test("restores a portable film with audio and captions and displays live operations", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const scene = randomUUID(),
    shot = randomUUID(),
    title = `DEMO Phase7 source ${Date.now()}`;
  const sourceResponse = await page.request.post("/api/v1/projects/import", {
    data: {
      title,
      brief: "DEMO / MOCK portable recovery",
      format: { width: 320, height: 180, fps_num: 24, fps_den: 1 },
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
  expect(sourceResponse.status()).toBe(201);
  const source = await sourceResponse.json(),
    pid = source.project_id;
  const generated = await page.request.post(
    `/api/v1/projects/${pid}/generate`,
    {
      data: {
        base_revision: source.revision,
        shot_ids: [source.shots[0].id],
        provider_id: "mock-video-local",
      },
    },
  );
  expect(generated.status()).toBe(202);
  await expect
    .poll(
      async () =>
        (
          await (await page.request.get(`/api/v1/projects/${pid}/jobs`)).json()
        ).find(
          (job: { operation: string }) => job.operation === "generate_video",
        )?.state,
    )
    .toBe("completed");
  let apr = await (
    await page.request.get(`/api/v1/projects/${pid}/apr`)
  ).json();
  const audioResponse = await page.request.post(
    `/api/v1/projects/${pid}/timeline/audio/import?base_revision=${apr.revision}&name=DEMO-tone.wav&rights_confirmed=true`,
    {
      data: demoWave(),
      headers: { "Content-Type": "application/octet-stream" },
    },
  );
  expect(audioResponse.status()).toBe(202);
  await expect
    .poll(
      async () =>
        (
          await (await page.request.get(`/api/v1/projects/${pid}/jobs`)).json()
        ).find((job: { operation: string }) => job.operation === "import_audio")
          ?.state,
    )
    .toBe("completed");
  apr = await (await page.request.get(`/api/v1/projects/${pid}/apr`)).json();
  const audio = apr.assets.find(
    (asset: { media_type: string }) => asset.media_type === "audio",
  );
  expect(
    (
      await page.request.put(`/api/v1/projects/${pid}/timeline/composition`, {
        data: {
          base_revision: apr.revision,
          audio_tracks: [
            {
              id: "music",
              type: "audio",
              name: "DEMO soundtrack",
              gain_db: -3,
              items: [
                {
                  asset_id: audio.id,
                  start_frame: 0,
                  source_start_frame: 0,
                  duration_frames: 48,
                  gain_db: -6,
                },
              ],
            },
          ],
          captions: [
            {
              id: randomUUID(),
              start_frame: 0,
              duration_frames: 24,
              text: "DEMO / MOCK restored",
            },
          ],
          markers: [
            { id: randomUUID(), frame: 24, label: "DEMO beat", kind: "beat" },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(title) }).click();
  await page.getByRole("button", { name: "渲染成片", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible();
  await page.getByRole("button", { name: "导出工程包", exact: true }).click();
  const exportLink = page.getByRole("link", { name: /下载 afds-.*\.zip/ });
  await expect(exportLink).toBeVisible();
  const archiveEvent = page.waitForEvent("download");
  await exportLink.click();
  const archive = await archiveEvent,
    archivePath = testInfo.outputPath("portable-project.zip");
  await archive.saveAs(archivePath);
  await archive.saveAs("../data/phase7-source-project.zip");
  await page.getByRole("button", { name: "返回项目列表" }).click();
  await page.getByLabel("工程包 ZIP").setInputFiles(archivePath);
  const restoredTitle = `DEMO Phase7 restored ${Date.now()}`;
  await page.getByLabel("恢复副本名称").fill(restoredTitle);
  await page.screenshot({
    path: "../data/phase7-restore-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../data/phase7-restore-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "恢复为新项目" }).click();
  await expect(
    page.getByRole("heading", { name: "恢复工程副本" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible();
  const projects = await (await page.request.get("/api/v1/projects")).json();
  const restored = projects.find(
    (project: { title: string }) => project.title === restoredTitle,
  );
  expect(restored.project_id).not.toBe(pid);
  const copy = await (
    await page.request.get(`/api/v1/projects/${restored.project_id}/apr`)
  ).json();
  expect(copy.shots[0].id).not.toBe(apr.shots[0].id);
  expect(copy.budget.allow_remote).toBe(false);
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await expect(page.getByRole("button", { name: "删除片段 1" })).toBeVisible();
  await expect(
    page.getByText("DEMO / MOCK restored", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "目标音轨" }).locator("option:checked"),
  ).toHaveText("DEMO soundtrack");
  await page.getByRole("button", { name: "渲染成片", exact: true }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(
              `/api/v1/projects/${restored.project_id}/jobs`,
            )
          ).json()
        ).find(
          (job: { operation: string }) => job.operation === "render_timeline",
        )?.state,
    )
    .toBe("completed");
  const filmEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 MP4" }).click();
  await (await filmEvent).saveAs("../data/phase7-restored-output.mp4");
  await page.getByRole("button", { name: "运行概览", exact: true }).click();
  await expect(page.getByText(/1 个 Worker 在线/)).toBeVisible();
  await expect(page.getByRole("img", { name: "任务素材流向图" })).toBeVisible();
  await page.screenshot({
    path: "../data/phase7-operations-desktop.png",
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
    path: "../data/phase7-operations-mobile.png",
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("button", { name: "运行概览", exact: true }).click();
  await expect(page.getByRole("heading", { name: "运行概览" })).toBeVisible();
  expect(errors).toEqual([]);
});
