import { expect, test } from "@playwright/test";

test("persists project budget/privacy and imports a mapped ComfyUI workflow", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("项目名称").fill("Phase 5 验收项目");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.getByRole("button", { name: /任务队列/ }).click();
  await expect(page.getByRole("heading", { name: "预算与隐私" })).toBeVisible();
  await page.getByLabel("项目预算上限").fill("12.50");
  await page.getByLabel("允许未知费用请求", { exact: false }).uncheck();
  await page.getByRole("button", { name: "保存预算与隐私" }).click();
  await expect(page.getByLabel("项目预算上限")).toHaveValue("12.500000");
  await page.reload();
  await page.getByRole("button", { name: /任务队列/ }).click();
  await expect(page.getByLabel("项目预算上限")).toHaveValue("12.500000");
  await expect(
    page.getByLabel("允许未知费用请求", { exact: false }),
  ).not.toBeChecked();
  await page.screenshot({ path: "../data/budget-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../data/budget-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "模型与工具", exact: true }).click();
  await page.getByRole("tab", { name: "图像生成", exact: true }).click();
  await page.getByLabel("接口协议").selectOption("comfyui");
  await page.getByLabel("服务 URL").fill("http://127.0.0.1:8188");
  await page
    .getByLabel("模型名称", { exact: true })
    .fill("local-reference-workflow");
  await page
    .getByLabel("导入 API 工作流")
    .setInputFiles({
      name: "reference.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          "1": { class_type: "CLIPTextEncode", inputs: { text: "a station" } },
          "2": { class_type: "SaveImage", inputs: { images: ["1", 0] } },
        }),
      ),
    });
  await page
    .getByLabel("prompt 映射", { exact: true })
    .selectOption(JSON.stringify(["1", "text"]));
  await page.getByText("高级选项", { exact: true }).click();
  await page.getByLabel("允许连接本机 / 内网服务及 HTTP 地址").check();
  await page.getByLabel("单次请求预估费用").fill("0");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByText("设置已保存", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "模型与工具", exact: true }).click();
  await page.getByRole("tab", { name: "图像生成", exact: true }).click();
  await expect(page.getByLabel("接口协议")).toHaveValue("comfyui");
  await expect(page.getByLabel("prompt 映射", { exact: true })).toHaveValue(
    JSON.stringify(["1", "text"]),
  );
  await page.screenshot({ path: "../data/comfy-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "../data/comfy-desktop.png", fullPage: true });
});
