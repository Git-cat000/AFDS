import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test.skip(
  process.env.AFDS_TEST_DOCKER !== "1",
  "Enable AFDS_TEST_DOCKER=1 for Docker-dependent browser acceptance",
);

test("approves isolated scripts on desktop and mobile, downloads output and cancels a running container", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const title = `DEMO container scripts ${Date.now()}`;
  const created = await page.request.post("/api/v1/projects", {
    data: { title, brief: "DEMO sandbox approval acceptance" },
  });
  expect(created.status()).toBe(201);
  const apr = await created.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  const script = `import json, pathlib, sys, time
context = json.load(sys.stdin)
if sys.argv[1] == 'wait':
    print('DEMO waiting in container', flush=True)
    time.sleep(60)
pathlib.Path('outputs/result.json').write_text(json.dumps({
    'stage': context['stage'], 'argument': sys.argv[1]}), encoding='utf-8')
print('DEMO isolated result')
`;
  const imported = await page.request.post("/api/v1/skills/import", {
    data: {
      stages: ["story"],
      files: [
        {
          path: "SKILL.md",
          content_base64: Buffer.from(
            "---\nname: demo-container-script\ndescription: DEMO isolated script verification.\n---\n# DEMO sandbox\nReview each output.\n",
          ).toString("base64"),
        },
        {
          path: "scripts/run.py",
          content_base64: Buffer.from(script).toString("base64"),
        },
      ],
    },
  });
  expect(imported.status()).toBe(201);
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(title) }).click();
  await page.getByRole("button", { name: /剧本编排技能/ }).click();
  const dialog = page.getByRole("dialog", { name: "制作技能与脚本" });
  await dialog
    .getByRole("button", { name: "demo-container-script", exact: false })
    .click();
  await dialog.getByRole("button", { name: "脚本与运行", exact: true }).click();
  await dialog
    .getByRole("combobox", { name: "执行环境" })
    .selectOption("container");

  async function approve(argument: string) {
    await dialog.getByLabel("脚本参数 JSON").fill(JSON.stringify([argument]));
    await dialog.getByLabel("最长运行秒数").fill("60");
    await dialog
      .getByRole("button", { name: "预览脚本运行", exact: true })
      .click();
    await expect(dialog.getByLabel("待运行脚本源码")).toContainText(
      "DEMO isolated result",
    );
    await expect(dialog.getByText(/内存 512 MiB/)).toBeVisible();
    const run = dialog.getByRole("button", { name: "批准并运行本次脚本" });
    await expect(run).toBeDisabled();
    const consent = dialog.getByRole("checkbox", {
      name: "已审阅源码和策略，允许在隔离容器中运行本次脚本",
    });
    await consent.scrollIntoViewIfNeeded();
    if (argument !== "wait") {
      await dialog.screenshot({
        path: `../data/container-approval-${argument}.png`,
      });
    }
    await consent.check();
    await run.click();
    await expect(dialog.getByRole("status")).toContainText("已提交运行");
  }

  await approve("desktop");
  const result = dialog
    .locator(".skill-run-result")
    .filter({ hasText: "下载 result.json" });
  await expect(result).toContainText("已完成");
  await expect(result).toContainText("隔离容器 · 网络关闭");
  await result.getByText("实际执行策略与解释器版本", { exact: true }).click();
  await expect(result.locator("pre").first()).toContainText(
    '"resources_verified": true',
  );
  const downloadEvent = page.waitForEvent("download");
  await result.getByRole("link", { name: "下载 result.json" }).click();
  const downloadPath = testInfo.outputPath("DEMO-container-result.json");
  await (await downloadEvent).saveAs(downloadPath);
  expect(JSON.parse(await readFile(downloadPath, "utf-8"))).toEqual({
    stage: "story",
    argument: "desktop",
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await approve("mobile");
  await expect
    .poll(async () => {
      const runs = await (await page.request.get(`${root}/skills/runs`)).json();
      return runs.items.filter(
        (job: { state: string }) => job.state === "completed",
      ).length;
    })
    .toBe(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();

  await approve("wait");
  const running = dialog
    .locator(".skill-run-result")
    .filter({ hasText: "执行中" });
  await expect(running).toHaveCount(1);
  await running.getByRole("button", { name: "取消此次运行" }).click();
  await expect(
    dialog.locator(".skill-run-result").filter({ hasText: "已取消" }),
  ).toHaveCount(1);
  await expect
    .poll(async () => {
      const runs = await (await page.request.get(`${root}/skills/runs`)).json();
      return runs.items.filter(
        (job: { sandbox_cleanup_pending?: boolean }) =>
          job.sandbox_cleanup_pending,
      ).length;
    })
    .toBe(0);
  expect(errors).toEqual([]);
});
