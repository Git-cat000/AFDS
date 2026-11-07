import { expect, test } from "@playwright/test";

test("configures model connections, discovers models and keeps secrets masked after reload", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "模型与工具", exact: true }).click();
  await expect(page.getByRole("heading", { name: "模型连接" })).toBeVisible();
  await page.getByLabel("服务 URL").fill("https://provider.example/v1");
  await page.getByLabel("API Key", { exact: true }).fill("ui-test-only-key");
  await page.route("**/api/v1/settings/providers/llm/test", (route) =>
    route.fulfill({
      json: {
        status: "connected",
        models: ["ui-test-model", "ui-model-two"],
        message: "连接与鉴权通过。生成能力需运行任务验证。",
        generation_tested: false,
      },
    }),
  );
  await page.getByRole("button", { name: "测试连接 / 获取模型" }).click();
  await expect(page.getByText("已发现 2 个模型")).toBeVisible();
  await page.getByLabel("模型名称", { exact: true }).fill("ui-test-model");
  await page.getByLabel("启用此连接").check();
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("设置已保存", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "模型与工具", exact: true }).click();
  await expect(page.getByLabel("服务 URL")).toHaveValue(
    "https://provider.example/v1",
  );
  await expect(page.getByLabel("API Key", { exact: true })).toHaveValue("");
  await expect(
    page.getByText("密钥已保存在本机", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("默认 LLM").selectOption("openai-llm");
  await expect(page.getByText("默认模型已更新", { exact: true })).toBeVisible();
  await page.getByLabel("默认 LLM").selectOption("mock-director");
  await page.screenshot({
    path: "../data/providers-desktop.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "视频生成", exact: true }).click();
  await expect(page.getByText(/OpenAI 官方视频服务已停止/)).toBeVisible();
  await page.getByRole("tab", { name: "图像生成", exact: true }).click();
  await expect(page.getByLabel("服务 URL")).toBeVisible();
  await page.getByRole("tab", { name: "语音合成", exact: true }).click();
  await page.getByText("高级选项", { exact: true }).click();
  await expect(page.getByLabel("音色名称")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../data/providers-mobile.png",
    fullPage: true,
  });
});
