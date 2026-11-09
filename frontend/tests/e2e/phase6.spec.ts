import { expect, test } from "@playwright/test";

function wave(seconds = 3) {
  const rate = 24000,
    count = seconds * rate,
    data = Buffer.alloc(44 + count * 2);
  data.write("RIFF", 0);
  data.writeUInt32LE(data.length - 8, 4);
  data.write("WAVEfmt ", 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24);
  data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write("data", 36);
  data.writeUInt32LE(count * 2, 40);
  for (let i = 0; i < count; i++)
    data.writeInt16LE(
      Math.round(Math.sin((2 * Math.PI * 330 * i) / rate) * 4000),
      44 + i * 2,
    );
  return data;
}

test("finishes a continuity-aware film with audio, captions, review evidence and portable export", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("项目名称").fill("Phase 6 雨夜告别");
  await page.getByLabel("创作简述").fill("DEMO/MOCK 四镜头的雨夜告别。");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.getByRole("button", { name: "生成分镜建议" }).click();
  await page.getByRole("button", { name: "批准并加入分镜" }).click();
  await expect(page.getByTestId("shot-card")).toHaveCount(4);
  await page.getByRole("button", { name: "角色与连续性", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("旅人");
  await page.getByLabel("不变特征（每行一项）").fill("深绿色外套\n手持旧皮箱");
  await page.getByRole("button", { name: "预览设定变更", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "变更影响预览" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "批准并应用", exact: true }).click();
  await page
    .getByRole("group", { name: "出场角色" })
    .getByLabel("旅人", { exact: true })
    .check();
  await page.getByRole("button", { name: "预览镜头引用", exact: true }).click();
  await page.getByRole("button", { name: "批准并应用", exact: true }).click();
  await page.screenshot({
    path: "../data/phase6-continuity-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "分镜工作室", exact: true }).click();
  await page.getByRole("button", { name: "生成全部镜头" }).click();
  await expect(
    page.getByTestId("shot-card").filter({ hasText: "可用" }),
  ).toHaveCount(4);
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await page.getByLabel("选择音频文件").setInputFiles({
    name: "DEMO-tone.wav",
    mimeType: "audio/wav",
    buffer: wave(),
  });
  await page.getByLabel("我确认拥有该音频的使用权或已获得授权").check();
  await page.getByRole("button", { name: "导入音频", exact: true }).click();
  await expect(
    page
      .getByRole("combobox", { name: "音频素材", exact: true })
      .locator("option"),
  ).toHaveCount(2);
  await page.getByRole("button", { name: "新增音轨" }).click();
  await page.getByLabel("音频开始帧", { exact: true }).fill("24");
  await page.getByLabel("音频持续帧", { exact: true }).fill("48");
  await page.getByLabel("片段增益 dB").fill("-6");
  await page.getByRole("button", { name: "添加音频片段", exact: true }).click();
  await expect(page.getByRole("button", { name: "删除片段 1" })).toBeVisible();
  await page.getByLabel("字幕内容").fill("DEMO/MOCK · 雨夜的告别");
  await page.getByLabel("字幕持续帧").fill("72");
  await page.getByRole("button", { name: "添加字幕", exact: true }).click();
  await page.getByRole("button", { name: "渲染成片", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible();
  const filmEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 MP4" }).click();
  await (await filmEvent).saveAs("../data/phase6-output.mp4");
  await page.screenshot({
    path: "../data/phase6-timeline-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../data/phase6-timeline-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "审片与证据", exact: true }).click();
  await page.getByRole("button", { name: "提交审片", exact: true }).click();
  await expect(page.getByText(/SEMANTIC_CONTINUITY/)).toBeVisible();
  await expect(page.getByAltText("审片关键帧 1")).toBeVisible();
  await page.screenshot({
    path: "../data/phase6-review-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "导出工程包", exact: true }).click();
  const exportLink = page.getByRole("link", { name: /下载 afds-.*\.zip/ });
  await expect(exportLink).toBeVisible();
  const packageEvent = page.waitForEvent("download");
  await exportLink.click();
  await (await packageEvent).saveAs("../data/phase6-project.zip");
  await page.reload();
  await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
  await expect(
    page.getByText("DEMO/MOCK · 雨夜的告别", { exact: true }).first(),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
