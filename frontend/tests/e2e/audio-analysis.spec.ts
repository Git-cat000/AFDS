import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { expect, test } from "@playwright/test";

test("approves local beats and contract transcription while preserving manual subtitles", async ({
  page,
}) => {
  const requests: Buffer[] = [];
  const contract = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      requests.push(Buffer.concat(chunks));
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          language: "en",
          segments: [
            { start: 0.25, end: 1, text: "DEMO contract first" },
            { start: 1.25, end: 2, text: "DEMO contract second" },
          ],
        }),
      );
    });
  });
  await new Promise<void>((resolve) =>
    contract.listen(0, "127.0.0.1", resolve),
  );
  try {
    const address = contract.address();
    if (!address || typeof address === "string")
      throw new Error("Missing contract address");
    const profiles = await (
      await page.request.get("/api/v1/settings/models")
    ).json();
    const previous = profiles.providers.find(
      (item: { kind: string }) => item.kind === "transcription",
    );
    const saved = await page.request.put(
      "/api/v1/settings/providers/transcription",
      {
        data: {
          revision: previous?.revision || 0,
          base_url: `http://127.0.0.1:${address.port}/v1`,
          model: "DEMO-transcription",
          api_key: "test-only-credential",
          enabled: true,
          allow_private_network: true,
          transcription_timestamps: true,
        },
      },
    );
    expect(saved.status()).toBe(200);
    const scene = randomUUID(),
      shot = randomUUID();
    const title = `DEMO audio analysis ${Date.now()}`;
    const imported = await page.request.post("/api/v1/projects/import", {
      data: {
        title,
        format: { width: 320, height: 180 },
        scenes: [
          { id: scene, order: 0, title: "DEMO scene", shot_ids: [shot] },
        ],
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
    const wav = Buffer.alloc(44 + 16000 * 4 * 2);
    wav.write("RIFF", 0);
    wav.writeUInt32LE(wav.length - 8, 4);
    wav.write("WAVEfmt ", 8);
    wav.writeUInt32LE(16, 16);
    wav.writeUInt16LE(1, 20);
    wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(16000, 24);
    wav.writeUInt32LE(32000, 28);
    wav.writeUInt16LE(2, 32);
    wav.writeUInt16LE(16, 34);
    wav.write("data", 36);
    wav.writeUInt32LE(wav.length - 44, 40);
    for (let start = 0; start < 64000; start += 8000) {
      for (let sample = start; sample < start + 100; sample++)
        wav.writeInt16LE(24000, 44 + sample * 2);
    }
    await page.goto("/");
    await page.getByRole("button").filter({ hasText: title }).click();
    await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
    await page
      .getByLabel("选择音频文件", { exact: true })
      .setInputFiles({
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
    await page
      .getByRole("button", { name: "预览选中候选", exact: true })
      .click();
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
    await page
      .getByRole("button", { name: "选择全部候选", exact: true })
      .click();
    await page
      .getByRole("button", { name: "预览选中候选", exact: true })
      .click();
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
  } finally {
    contract.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      contract.close((error) => (error ? reject(error) : resolve())),
    );
  }
});
