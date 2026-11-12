import { expect, test } from "@playwright/test";

test("creates a film, edits a shot, generates footage and downloads the render", async ({
  page,
}) => {
  const errors: string[] = [];
  let revisionRequests = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.url().endsWith("/revisions")) revisionRequests++;
  });
  await page.goto("/");
  await page.getByLabel("项目名称").fill(`雨夜告别 ${Date.now()}`);
  await page.getByLabel("创作简述").fill("在雨夜的车站，一位旅人向旧日告别。");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.getByRole("button", { name: "生成分镜建议" }).click();
  await expect(
    page.getByRole("button", { name: "批准并加入分镜" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "批准并加入分镜" }).click();
  await expect(page.getByTestId("shot-card")).toHaveCount(4);
  await page
    .getByTestId("shot-card")
    .nth(2)
    .getByRole("button", { name: "编辑镜头" })
    .click();
  await page.getByLabel("动作描述").fill("主角抬头，望向最后一班列车。");
  await page.getByRole("button", { name: "预览修改影响" }).click();
  await page.getByRole("button", { name: "确认提交修改" }).click();
  await page.getByRole("button", { name: "生成全部镜头" }).click();
  await expect(
    page.getByTestId("shot-card").filter({ hasText: "可用" }),
  ).toHaveCount(4);
  await page.getByRole("button", { name: "渲染成片", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 MP4" }).click();
  const download = await downloadEvent;
  expect(await download.failure()).toBeNull();
  expect(revisionRequests).toBe(0);
  await page.getByRole("button", { name: "版本记录", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "项目版本记录" }),
  ).toBeVisible();
  await expect(page.locator(".history-row").first()).toBeVisible();
  expect(revisionRequests).toBeGreaterThan(0);
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("link", { name: "下载 MP4" })).toBeVisible();
  await page.screenshot({ path: "../data/studio-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../data/studio-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
