import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("configures a candidate, previews policy reasons and runs a pinned local contract", async ({
  page,
}) => {
  let submissions = 0;
  const server = createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      if (request.url === "/v1/chat/completions") submissions++;
      response.setHeader("Content-Type", "application/json");
      response.end(
        JSON.stringify(
          request.url === "/v1/models"
            ? { data: [{ id: "DEMO-contract-model" }] }
            : {
                choices: [
                  {
                    message: {
                      content: JSON.stringify({
                        scene: {
                          title: "DEMO contract scene",
                          description: "Offline acceptance",
                        },
                        shots: [
                          {
                            title: "DEMO routed shot",
                            action: "DEMO contract action",
                            duration_frames: 48,
                          },
                        ],
                      }),
                    },
                  },
                ],
              },
        ),
      );
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    if (!address || typeof address === "string")
      throw new Error("Missing contract listener");
    const identity = `demo-route-${randomUUID().slice(0, 8)}`;
    const label = `DEMO routed candidate ${identity}`;
    await page.goto("/");
    await page.getByRole("button", { name: "模型与工具", exact: true }).click();
    await page.getByText("管理候选连接", { exact: true }).click();
    const candidate = page.locator(".candidate-settings");
    await candidate.getByLabel("连接 ID", { exact: true }).fill(identity);
    await candidate.getByLabel("连接名称", { exact: true }).fill(label);
    await candidate
      .getByLabel("服务 URL", { exact: true })
      .fill(`http://127.0.0.1:${address.port}/v1`);
    await candidate
      .getByLabel("模型名称", { exact: true })
      .fill("DEMO-contract-model");
    await candidate.getByLabel("启用此连接", { exact: true }).check();
    await candidate.getByText("高级选项", { exact: true }).click();
    await candidate
      .getByLabel("允许连接本机 / 内网服务及 HTTP 地址", { exact: true })
      .check();
    await candidate.getByLabel("单次请求预估费用", { exact: true }).fill("0");
    await candidate
      .getByRole("button", { name: "保存设置", exact: true })
      .click();
    await expect(
      candidate.getByRole("combobox", { name: "编辑候选连接" }),
    ).toHaveValue(identity);
    await candidate
      .getByRole("button", { name: "测试连接 / 获取模型" })
      .click();
    await expect(candidate.getByText("已发现 1 个模型")).toBeVisible();
    await candidate.screenshot({
      path: "../data/routing-candidate-desktop.png",
    });
    const created = await page.request.post("/api/v1/projects", {
      data: { title: label, brief: "DEMO route acceptance" },
    });
    expect(created.status()).toBe(201);
    const apr = await created.json();
    const root = `/api/v1/projects/${apr.project_id}`;
    await page.reload();
    await page
      .getByRole("button", { name: "返回项目列表", exact: true })
      .click();
    await page.getByRole("button").filter({ hasText: label }).click();
    await page.getByRole("button", { name: /任务队列/ }).click();
    await page.getByText("模型路由策略", { exact: true }).click();
    const policy = page.locator(".project-routing");
    await policy
      .getByRole("combobox", { name: "选择策略" })
      .selectOption("lowest_estimated_cost");
    await policy.getByLabel(`允许 ${label}`, { exact: true }).check();
    await policy
      .getByRole("button", { name: "保存路由策略", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(root + "/apr")).json())
            .routing_policies?.storyboard?.allowed_ids,
      )
      .toEqual([identity]);
    await policy.getByRole("button", { name: "预览已保存策略" }).click();
    await expect(
      policy.getByText(new RegExp(`选择 ${identity}`)),
    ).toBeVisible();
    await policy.getByText("查看候选与筛选理由", { exact: true }).click();
    await expect(policy.getByText(/超出本次候选范围/).first()).toBeVisible();
    await policy.screenshot({ path: "../data/routing-policy-desktop.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await policy.screenshot({ path: "../data/routing-policy-mobile.png" });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.setViewportSize({ width: 1440, height: 1000 });
    // Use the normal storyboard button after saving the policy in the actual UI.
    await page.getByRole("button", { name: "分镜工作室", exact: true }).click();
    await page.getByLabel("导演 LLM", { exact: true }).selectOption("auto");
    await page
      .getByText(/允许发送.*服务商/)
      .first()
      .click();
    await page.getByRole("button", { name: "生成分镜建议" }).click();
    await expect(
      page.getByRole("button", { name: "批准并加入分镜" }),
    ).toBeVisible();
    const jobs = await (await page.request.get(root + "/jobs/page")).json();
    const summary = jobs.items.find(
      (value: { operation: string }) => value.operation === "plan_storyboard",
    );
    const job = await (await page.request.get(root + "/jobs/" + summary.id)).json();
    expect(job.request.provider_config.connection_id).toBe(identity);
    expect(job.request.provider_config.revision).toBe(1);
    expect(submissions).toBe(1);
    expect(JSON.stringify(job.request.provider_config.route)).not.toContain(
      "base_url",
    );
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
