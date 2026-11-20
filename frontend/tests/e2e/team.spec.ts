import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

test.skip(
  process.env.AFDS_TEST_TEAM_BROWSER !== "1",
  "Run the isolated team browser profile",
);

async function signIn(page: Page, name: string) {
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "用户名" })
    .waitFor({ timeout: 5000 });
  await page.getByRole("textbox", { name: "用户名" }).fill(`demo-${name}`);
  await page
    .getByLabel("密码", { exact: true })
    .fill(`DEMO-${name}-password-2026`);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "创作工作区" })).toBeVisible();
}

test("creates a team film, manages roles, fences revoked editors and downloads owned media", async ({
  page,
  browser,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await signIn(page, "owner");
  const title = `DEMO team film ${Date.now()}`;
  await page.getByLabel("项目名称").fill(title);
  await page.getByLabel("创作简述").fill("DEMO offline team production");
  await page.getByRole("button", { name: "创建项目", exact: true }).click();
  await page.getByRole("button", { name: "生成分镜建议" }).click();
  await page.getByRole("button", { name: "批准并加入分镜" }).click();
  await expect(page.getByTestId("shot-card")).toHaveCount(4);
  await page.getByRole("button", { name: "项目成员", exact: true }).click();
  const members = page.getByRole("dialog", { name: "项目成员" });
  for (const role of ["editor", "reviewer", "viewer"]) {
    await members
      .getByRole("textbox", { name: "成员用户名" })
      .fill(`demo-${role}`);
    await members
      .getByRole("combobox", { name: "新增成员角色" })
      .selectOption(role);
    await members
      .getByRole("button", { name: "添加成员", exact: true })
      .click();
    await expect(
      members.getByText(`demo-${role}`, { exact: true }).first(),
    ).toBeVisible();
  }
  await members
    .getByRole("heading", { name: "项目成员", exact: true })
    .scrollIntoViewIfNeeded();
  await members.screenshot({ path: "../data/team-members-desktop.png" });
  await members.getByRole("button", { name: "关闭", exact: true }).click();

  const folder = testInfo.outputPath("demo-team-guide");
  await mkdir(join(folder, "scripts"), { recursive: true });
  await writeFile(
    join(folder, "SKILL.md"),
    `---
name: demo-team-guide
description: DEMO isolated team production check.
---
# DEMO team guidance
Read the production context before checking the stage.
`,
  );
  await writeFile(
    join(folder, "scripts", "inspect.py"),
    `import json
import pathlib
import sys
context = json.load(sys.stdin)
pathlib.Path('outputs/report.json').write_text(json.dumps({'stage': context['stage']}))
print('DEMO team container completed')
`,
  );
  const zip = testInfo.outputPath("team-guide.zip");
  execFileSync(
    resolve(
      "..",
      process.platform === "win32"
        ? ".venv/Scripts/python.exe"
        : ".venv/bin/python",
    ),
    [
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
    ],
    { windowsHide: true },
  );
  await page.getByRole("button", { name: "剧本构思", exact: true }).click();
  await page.getByRole("button", { name: /剧本编排技能/ }).click();
  const skills = page.getByRole("dialog", { name: "制作技能与脚本" });
  await skills.getByText("导入其它项目的技能", { exact: true }).click();
  await skills
    .getByLabel("导入 SKILL.md 或 ZIP", { exact: true })
    .setInputFiles(zip);
  await skills
    .getByRole("button", { name: "demo-team-guide", exact: false })
    .click();
  await skills.getByRole("button", { name: "脚本与运行", exact: true }).click();
  await expect(skills.getByLabel("执行环境")).toHaveValue("container");
  await skills
    .getByRole("button", { name: "预览脚本运行", exact: true })
    .click();
  await skills
    .getByRole("checkbox", {
      name: "已审阅源码和策略，允许在隔离容器中运行本次脚本",
    })
    .check();
  await skills
    .getByRole("button", { name: "批准并运行本次脚本", exact: true })
    .click();
  await expect(
    skills.getByText("DEMO team container completed", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await skills.getByRole("button", { name: "关闭", exact: true }).click();

  const editorContext = await browser.newContext({
    baseURL: "http://127.0.0.1:5174",
  });
  const viewerContext = await browser.newContext({
    baseURL: "http://127.0.0.1:5174",
    viewport: { width: 390, height: 844 },
  });
  try {
    const editor = await editorContext.newPage();
    const viewer = await viewerContext.newPage();
    await signIn(editor, "editor");
    await editor.getByRole("button", { name: new RegExp(title) }).click();
    await expect(editor.locator(".project-role")).toHaveText("编辑者");
    await expect(
      editor.getByRole("button", { name: "模型与工具", exact: true }),
    ).toHaveCount(0);
    await editor.getByRole("button", { name: "网格", exact: true }).click();
    await editor
      .getByTestId("shot-card")
      .first()
      .getByRole("button", { name: "编辑镜头" })
      .click();
    await editor.getByLabel("动作描述").fill("DEMO edited by a project editor");
    await editor.getByRole("button", { name: "预览修改影响" }).click();
    await editor.getByRole("button", { name: "确认提交修改" }).click();

    await signIn(viewer, "viewer");
    await viewer.getByRole("button", { name: new RegExp(title) }).click();
    await expect(viewer.locator(".project-role")).toHaveText("查看者");
    await viewer.getByRole("button", { name: "项目成员", exact: true }).click();
    const readOnlyMembers = viewer.getByRole("dialog", { name: "项目成员" });
    await expect(
      readOnlyMembers.getByRole("combobox", { name: "demo-editor的角色" }),
    ).toBeDisabled();
    await readOnlyMembers.screenshot({
      path: "../data/team-members-mobile.png",
    });
    await readOnlyMembers
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    await viewer
      .getByRole("button", { name: "生成全部镜头", exact: true })
      .click();
    await expect(viewer.getByRole("alert")).toContainText(
      "当前项目角色没有此操作权限",
    );

    await page.getByRole("button", { name: "项目成员", exact: true }).click();
    await members
      .getByRole("button", { name: "移除 demo-editor", exact: true })
      .click();
    await expect(members.getByText("demo-editor", { exact: true })).toHaveCount(
      0,
    );
    const projects = await (await page.request.get("/api/v1/projects")).json();
    const projectId = projects.find(
      (project: { title: string }) => project.title === title,
    ).project_id;
    await expect
      .poll(async () =>
        (
          await editor.request.get(`/api/v1/projects/${projectId}/apr`)
        ).status(),
      )
      .toBe(404);
    await members.getByRole("button", { name: "关闭", exact: true }).click();

    await page
      .getByRole("button", { name: "生成全部镜头", exact: true })
      .click();
    await expect(
      page.getByTestId("shot-card").filter({ hasText: "可用" }),
    ).toHaveCount(4);
    await page.getByRole("button", { name: "渲染成片", exact: true }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载 MP4", exact: true }).click();
    const film = await download;
    expect(await film.failure()).toBeNull();
    await film.saveAs("../data/team-output.mp4");
    const apr = await (
      await viewer.request.get(`/api/v1/projects/${projectId}/apr`)
    ).json();
    const media = await viewer.request.get(
      `/api/v1/projects/${projectId}/assets/${apr.render_asset_id}/file`,
    );
    expect(media.status()).toBe(200);
    expect(media.headers()["content-type"]).toContain("video/mp4");
    expect(
      await viewer.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.getByRole("button", { name: "运行概览", exact: true }).click();
    const nodes = page.getByRole("region", { name: "执行节点", exact: true });
    await expect(
      nodes.getByRole("heading", { name: "AFDS worker", exact: true }),
    ).toBeVisible();
    await nodes
      .getByRole("button", { name: "排空 AFDS worker", exact: true })
      .click();
    await expect(nodes.getByText("排空中", { exact: true })).toBeVisible();
    await nodes
      .getByRole("button", { name: "恢复接单 AFDS worker", exact: true })
      .click();
    await expect(nodes.getByText("在线", { exact: true })).toBeVisible();
    await nodes.screenshot({ path: "../data/worker-nodes-desktop.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    await nodes.screenshot({ path: "../data/worker-nodes-mobile.png" });
    await page.getByRole("button", { name: "退出登录", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeVisible();
    expect(
      (await page.request.get(`/api/v1/projects/${projectId}/apr`)).status(),
    ).toBe(401);
    expect(errors).toEqual([]);
  } finally {
    await editorContext.close();
    await viewerContext.close();
  }
});
