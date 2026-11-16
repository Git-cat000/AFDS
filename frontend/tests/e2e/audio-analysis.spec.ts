import { randomUUID } from "node:crypto";
import { demoClickTrack, test } from "./audio-fixtures";
import { expect } from "@playwright/test";

test("approves local beats and contract transcription while preserving manual subtitles", async ({
  page,
  transcriptionContract,
}) => {
  const requests = transcriptionContract.requests;
  const scene = randomUUID(),
    shot = randomUUID();
  const title = `DEMO audio analysis ${Date.now()}`;
  const imported = await page.request.post("/api/v1/projects/import", {
    data: {
      title,
      format: { width: 320, height: 180 },
      scenes: [{ id: scene, order: 0, title: "DEMO scene", shot_ids: [shot] }],
      shots: [
        {
          id: shot,
          scene_id: scene,
          order: 0,
          title: "DEMO shot",
          duration_frames: 120,
        },
      ],
      timeline: {
        tracks: [
          {
            id: "v1",
            type: "video",
            items: [{ shot_id: shot, start_frame: 0, duration_frames: 120 }],
          },
        ],
      },
    },
  });
  expect(imported.status()).toBe(201);
  let apr = await imported.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  const latest = async () => (await page.request.get(`${root}/apr`)).json();
  const wav = demoClickTrack();
  await page.goto("/");
  await page.getByRole("button").filter({ hasText: title }).click();
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await page.getByLabel("选择音频文件", { exact: true }).setInputFiles({
    name: "DEMO-clicks.wav",
    mimeType: "audio/wav",
    buffer: wav,
  });
  await page
    .getByLabel("我确认拥有该音频的使用权或已获得授权", { exact: true })
    .check();
  await page.getByRole("button", { name: "导入音频", exact: true }).click();
  await expect.poll(async () => (await latest()).assets.length).toBe(1);
  apr = await latest();
  await expect(
    page.getByText(new RegExp(`修订 ${apr.revision}$`)).first(),
  ).toBeVisible();
  await page
    .getByLabel("字幕内容", { exact: true })
    .fill("DEMO manual subtitle");
  await page.getByRole("button", { name: "添加字幕", exact: true }).click();
  await expect
    .poll(async () => (await latest()).timeline.captions.length)
    .toBe(1);
  await page
    .getByRole("button", { name: "节拍与语音分析", exact: true })
    .click();
  await page.getByRole("button", { name: "检测节拍", exact: true }).click();
  await expect(
    page.getByText("8 项候选 · 估计 120 BPM · 周期一致度 100%", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("候选 2", { exact: true }).check();
  await page.getByLabel("候选 3", { exact: true }).check();
  await page.getByRole("button", { name: "预览选中候选", exact: true }).click();
  await page
    .getByRole("button", { name: "批准写入时间轴", exact: true })
    .click();
  await expect
    .poll(async () =>
      (await latest()).timeline.markers.map(
        (item: { frame: number }) => item.frame,
      ),
    )
    .toEqual([12, 24]);
  await page.getByLabel("转写语言", { exact: true }).fill("en");
  await page
    .getByLabel("批准发送所选音频并承担本次转写费用", { exact: true })
    .check();
  await page.getByRole("button", { name: "转写语音", exact: true }).click();
  await expect(
    page.getByText(/250–1000 ms · DEMO contract first/),
  ).toBeVisible();
  await page.getByRole("button", { name: "选择全部候选", exact: true }).click();
  await page.getByRole("button", { name: "预览选中候选", exact: true }).click();
  await page
    .getByRole("button", { name: "批准写入时间轴", exact: true })
    .click();
  await expect
    .poll(async () => (await latest()).timeline.captions.length)
    .toBe(3);
  apr = await latest();
  expect(apr.timeline.captions[0].text).toBe("DEMO manual subtitle");
  expect(apr.timeline.captions[1].start_frame).toBe(6);
  expect(requests).toHaveLength(1);
  expect(requests[0].includes(Buffer.from("RIFF"))).toBe(true);
  const jobs = await (
    await page.request.get(`${root}/timeline/analysis?limit=20`)
  ).json();
  const transcript = jobs.find(
    (item: { operation: string }) => item.operation === "transcribe_audio",
  );
  await page.reload();
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await page
    .getByRole("button", { name: "节拍与语音分析", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "最近分析", exact: true })
    .selectOption(transcript.id);
  await expect(
    page.getByText(/250–1000 ms · DEMO contract first/),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("region", { name: "节拍与语音分析", exact: true })
    .scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBe(true);
  await page.screenshot({
    path: "../data/audio-analysis-mobile.png",
    fullPage: true,
  });
  expect(requests).toHaveLength(1);
});
