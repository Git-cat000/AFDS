import { execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { expect, test } from "@playwright/test";

test("imports a production skill, approves guidance and runs reviewed scripts on desktop and mobile", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const title = `DEMO skill production ${Date.now()}`;
  const created = await page.request.post("/api/v1/projects", {
    data: { title, brief: "DEMO portable production guidance" },
  });
  expect(created.status()).toBe(201);
  const apr = await created.json();
  const api = `/api/v1/projects/${apr.project_id}`;
  const folder = testInfo.outputPath("demo-production-guide");
  await mkdir(join(folder, "scripts"), { recursive: true });
  await mkdir(join(folder, "references"), { recursive: true });
  await writeFile(
    join(folder, "SKILL.md"),
    `---
name: demo-production-guide
description: DEMO imported story and shot guidance.
metadata:
  version: "1.0"
license: MIT
---
# DEMO production guidance
Preserve eyelines and describe each visible action clearly.
Use references/framing.md for the action axis.
`,
  );
  await writeFile(
    join(folder, "references", "framing.md"),
    "DEMO keep a consistent action axis.",
  );
  await writeFile(
    join(folder, "scripts", "inspect.py"),
    `import json
import pathlib
import sys
context = json.load(sys.stdin)
result = {'stage': context['stage'], 'arguments': sys.argv[1:]}
pathlib.Path('outputs/notes.json').write_text(json.dumps(result), encoding='utf-8')
print('DEMO imported script completed')
`,
  );
  const zip = testInfo.outputPath("production-skill.zip");
  const python = resolve(
    "..",
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  );
  execFileSync(python, [
    "-c",
    `import pathlib,sys,zipfile
root=pathlib.Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(root.rglob('*')):
        if file.is_file():
            archive.write(file, root.name + '/' + file.relative_to(root).as_posix())
`,
    folder,
    zip,
  ]);
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(title) }).click();
  await page.getByRole("button", { name: /剧本编排技能/ }).click();
  const dialog = page.getByRole("dialog", { name: "制作技能与脚本" });
  await dialog.getByText("导入其它项目的技能", { exact: true }).click();
  await dialog
    .getByRole("textbox", { name: "来源说明" })
    .fill("DEMO external project");
  await dialog.getByRole("checkbox", { name: "镜头设计", exact: true }).check();
  await dialog
    .getByLabel("导入 SKILL.md 或 ZIP", { exact: true })
    .setInputFiles(zip);
  const enabled = dialog.getByRole("checkbox", {
    name: "启用 demo-production-guide",
  });
  await expect(enabled).toBeVisible();
  await enabled.check();
  await dialog
    .getByRole("button", { name: "demo-production-guide", exact: false })
    .click();
  await dialog.getByText("资源与来源", { exact: true }).click();
  await dialog
    .getByRole("checkbox", { name: "加入指导 references/framing.md" })
    .check();
  await dialog
    .getByRole("button", { name: "预览技能配置", exact: true })
    .click();
  expect((await (await page.request.get(`${api}/apr`)).json()).revision).toBe(
    apr.revision,
  );
  await dialog
    .getByRole("button", { name: "批准技能配置", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`${api}/apr`)).json())
          .production_guidance.story?.[0]?.resources[0]?.text,
    )
    .toContain("consistent action axis");
  await dialog.getByText("导入其它项目的技能", { exact: true }).click();
  await dialog.getByRole("button", { name: "运行本阶段制作检查" }).click();
  await expect(
    dialog.locator(".skill-run-result").filter({ hasText: "制作检查" }),
  ).toContainText("已完成");
  await dialog.getByRole("button", { name: "脚本与运行", exact: true }).click();
  await dialog
    .getByLabel("脚本参数 JSON")
    .fill('["DEMO desktop", "  preserve spaces  "]');
  await dialog.getByLabel("最长运行秒数").fill("10");
  await dialog
    .getByRole("button", { name: "预览脚本运行", exact: true })
    .click();
  await expect(dialog.getByLabel("待运行脚本源码")).toContainText(
    "DEMO imported script completed",
  );
  const approve = dialog.getByRole("button", { name: "批准并运行本次脚本" });
  await expect(approve).toBeDisabled();
  await dialog
    .getByRole("checkbox", {
      name: "已审阅源码和参数，允许以本机用户权限运行本次脚本",
    })
    .scrollIntoViewIfNeeded();
  await dialog.screenshot({ path: "../data/skills-approval-desktop.png" });
  await dialog
    .getByRole("checkbox", {
      name: "已审阅源码和参数，允许以本机用户权限运行本次脚本",
    })
    .check();
  await approve.click();
  await expect(
    dialog.getByText("DEMO imported script completed", { exact: true }),
  ).toBeVisible();
  const download = dialog.getByRole("link", { name: "下载 notes.json" });
  const saved = page.waitForEvent("download");
  await download.click();
  const destination = testInfo.outputPath("notes.json");
  await (await saved).saveAs(destination);
  expect(JSON.parse(await readFile(destination, "utf-8"))).toEqual({
    stage: "story",
    arguments: ["DEMO desktop", "  preserve spaces  "],
  });
  await dialog
    .getByRole("heading", { name: "制作技能与脚本", exact: true })
    .scrollIntoViewIfNeeded();
  await dialog.screenshot({ path: "../data/skills-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.getByLabel("脚本参数 JSON").fill('["DEMO mobile"]');
  await dialog
    .getByRole("button", { name: "预览脚本运行", exact: true })
    .click();
  await dialog
    .getByRole("checkbox", {
      name: "已审阅源码和参数，允许以本机用户权限运行本次脚本",
    })
    .scrollIntoViewIfNeeded();
  await dialog.screenshot({ path: "../data/skills-approval-mobile.png" });
  await dialog
    .getByRole("checkbox", {
      name: "已审阅源码和参数，允许以本机用户权限运行本次脚本",
    })
    .check();
  await approve.click();
  await expect
    .poll(async () => {
      const runs = await (
        await page.request.get(`${api}/skills/runs?stage=story`)
      ).json();
      return runs.items.filter(
        (job: { operation: string; state: string }) =>
          job.operation === "run_skill_script" && job.state === "completed",
      ).length;
    })
    .toBe(2);
  await dialog
    .getByRole("heading", { name: "制作技能与脚本", exact: true })
    .scrollIntoViewIfNeeded();
  await dialog.screenshot({ path: "../data/skills-mobile.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  const latest = await (await page.request.get(`${api}/apr`)).json();
  expect(latest.revision).toBe(apr.revision + 1);
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "技能与脚本", exact: true }).click();
  await expect(
    dialog.getByRole("checkbox", { name: "启用 demo-production-guide" }),
  ).toBeChecked();
  expect(errors).toEqual([]);
});
