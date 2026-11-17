import { expect, test, type Page } from "@playwright/test";

test.skip(
  process.env.AFDS_TEST_COLLAB_BROWSER !== "1",
  "Run the isolated two-API team profile",
);

async function signIn(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("textbox", { name: "用户名" }).fill(`demo-${name}`);
  await page
    .getByLabel("密码", { exact: true })
    .fill(`DEMO-${name}-password-2026`);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "创作工作区" })).toBeVisible();
}

function revocationStream(page: Page, root: string, label: string) {
  return page
    .evaluate(
      ({ root, label }) =>
        new Promise<boolean>((resolve) => {
          const source = new EventSource(`${root}/events/stream`);
          source.onopen = () => {
            document.body.dataset.demoStream = label;
          };
          source.addEventListener("access_revoked", () => {
            source.close();
            resolve(true);
          });
        }),
      { root, label },
    )
    .catch(() => false);
}

test("synchronizes two API nodes, merges disjoint drafts, fences conflicts, reconnects and revokes streams", async ({
  page,
  browser,
}) => {
  expect(
    (await page.request.get("http://127.0.0.1:8012/api/v1/health")).status(),
  ).toBe(200);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await signIn(page, "owner");
  const session = await (await page.request.get("/api/v1/auth/session")).json();
  const headers = { "X-CSRF-Token": session.csrf_token };
  const title = `DEMO collaborative film ${Date.now()}`;
  const project = await (
    await page.request.post("/api/v1/projects", { headers, data: { title } })
  ).json();
  const root = `/api/v1/projects/${project.project_id}`;
  const invite = await page.request.post(`${root}/members`, {
    headers,
    data: { username: "demo-editor", role: "editor" },
  });
  expect(invite.status()).toBe(200);
  await page.reload();
  await page.getByRole("button", { name: new RegExp(title) }).click();
  await expect(page.getByText("协作画布已同步", { exact: true })).toBeVisible();
  for (const text of ["DEMO A", "DEMO B"]) {
    await page.getByRole("button", { name: "添加文字", exact: true }).click();
    await page
      .getByRole("textbox", { name: /^文字内容 / })
      .last()
      .fill(text);
    await expect(
      page.getByText("协作画布已同步", { exact: true }),
    ).toBeVisible();
    await expect
      .poll(async () =>
        (
          await (await page.request.get(`${root}/workspace`)).json()
        ).document.nodes.some((node: { text: string }) => node.text === text),
      )
      .toBe(true);
  }
  const saved = await (
    await page.request.get(`${root}/workspace/collaboration`)
  ).json();
  const noteA = saved.document.nodes.find(
    (node: { text: string }) => node.text === "DEMO A",
  ).id;
  const noteB = saved.document.nodes.find(
    (node: { text: string }) => node.text === "DEMO B",
  ).id;
  const second = await browser.newContext({ baseURL: "http://127.0.0.1:5175" });
  try {
    const other = await second.newPage();
    other.on("pageerror", (error) => errors.push(error.message));
    await signIn(other, "editor");
    await other.getByRole("button", { name: new RegExp(title) }).click();
    const ownerA = page.getByRole("textbox", {
      name: `文字内容 ${noteA}`,
      exact: true,
    });
    const editorA = other.getByRole("textbox", {
      name: `文字内容 ${noteA}`,
      exact: true,
    });
    const ownerB = page.getByRole("textbox", {
      name: `文字内容 ${noteB}`,
      exact: true,
    });
    const editorB = other.getByRole("textbox", {
      name: `文字内容 ${noteB}`,
      exact: true,
    });
    await expect(editorA).toHaveValue("DEMO A");
    const start = Date.now();
    await ownerA.fill("DEMO owner live update");
    await expect(editorA).toHaveValue("DEMO owner live update", {
      timeout: 2000,
    });
    const latency = Date.now() - start;
    console.log(`DEMO two-API synchronization latency ${latency} ms`);
    expect(latency).toBeLessThan(2000);
    await Promise.all([
      ownerA.fill("DEMO owner disjoint"),
      editorB.fill("DEMO editor disjoint"),
    ]);
    await expect(editorA).toHaveValue("DEMO owner disjoint");
    await expect(ownerB).toHaveValue("DEMO editor disjoint");
    await expect(
      page.getByText("协作画布已同步", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "撤销画布操作", exact: true })
      .click();
    await expect(editorA).toHaveValue("DEMO owner live update");
    await expect(editorB).toHaveValue("DEMO editor disjoint");
    await page
      .getByRole("button", { name: "重做画布操作", exact: true })
      .click();
    await expect(editorA).toHaveValue("DEMO owner disjoint");

    let release: (() => void) | undefined;
    let intercepted: (() => void) | undefined;
    const waiting = new Promise<void>((resolve) => {
      intercepted = resolve;
    });
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    await other.route(`**${root}/workspace/operations`, async (route) => {
      intercepted!();
      await blocked;
      await route.continue();
    });
    await editorA.fill("DEMO conflicting editor draft");
    await waiting;
    await ownerA.fill("DEMO latest owner text");
    await expect
      .poll(
        async () =>
          (
            await (await page.request.get(`${root}/workspace`)).json()
          ).document.nodes.find((node: { id: string }) => node.id === noteA)
            .text,
      )
      .toBe("DEMO latest owner text");
    release!();
    await expect(
      other.getByText("协作对象已发生变化", { exact: false }),
    ).toBeVisible();
    await expect(editorA).toHaveValue("DEMO conflicting editor draft");
    await other.unroute(`**${root}/workspace/operations`);
    await other
      .getByRole("button", { name: "载入服务器版本", exact: true })
      .click();
    await expect(editorA).toHaveValue("DEMO latest owner text");

    await second.setOffline(true);
    await editorB.fill("DEMO offline preserved draft");
    await expect(other.getByText(/草稿.*已保留/)).toBeVisible();
    await ownerA.fill("DEMO remote during disconnect");
    await expect(
      page.getByText("协作画布已同步", { exact: true }),
    ).toBeVisible();
    await second.setOffline(false);
    await other
      .getByRole("button", { name: "重新保存当前草稿", exact: true })
      .click();
    await expect(ownerB).toHaveValue("DEMO offline preserved draft");
    await expect(editorA).toHaveValue("DEMO remote during disconnect");
    const handle = page
      .locator(`[data-id="${noteA}"] .node-drag-handle`)
      .first();
    await handle.scrollIntoViewIfNeeded();
    const box = (await handle.boundingBox())!;
    await page.mouse.move(box.x + 20, box.y + 15);
    await page.mouse.down();
    await page.mouse.move(box.x + 65, box.y + 35, { steps: 8 });
    try {
      await expect
        .poll(
          async () =>
            (await (await other.request.get(`${root}/presence`)).json()).some(
              (person: { entity_id: string }) => person.entity_id === noteA,
            ),
          { timeout: 4000 },
        )
        .toBe(true);
      await expect(other.getByLabel("在线成员")).toContainText("正在编辑");
    } finally {
      await page.mouse.up();
    }
    await other.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        other.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      )
      .toBe(true);
    const actions = (await other.locator(".topbar-actions").boundingBox())!;
    const heading = (await other
      .getByRole("heading", { name: title, exact: true })
      .boundingBox())!;
    expect(actions.y + actions.height).toBeLessThanOrEqual(heading.y);
    await other.screenshot({
      path: "../data/collaboration-mobile.png",
      fullPage: true,
    });
    await page.screenshot({
      path: "../data/collaboration-desktop.png",
      fullPage: true,
    });

    const expired = revocationStream(other, root, "session");
    await expect(other.locator("body")).toHaveAttribute(
      "data-demo-stream",
      "session",
    );
    await other.getByRole("button", { name: "退出登录", exact: true }).click();
    expect(await expired).toBe(true);
    await signIn(other, "editor");
    await other.getByRole("button", { name: new RegExp(title) }).click();
    const revoked = revocationStream(other, root, "membership");
    await expect(other.locator("body")).toHaveAttribute(
      "data-demo-stream",
      "membership",
    );
    const members = await (await page.request.get(`${root}/members`)).json();
    const editor = members.find(
      (member: { username: string }) => member.username === "demo-editor",
    );
    expect(
      (
        await page.request.delete(`${root}/members/${editor.id}`, {
          headers,
        })
      ).status(),
    ).toBe(200);
    expect(await revoked).toBe(true);
    expect((await other.request.get(root)).status()).toBe(404);
    expect(
      (await (await page.request.get(`${root}/apr`)).json()).revision,
    ).toBe(project.revision);
    expect(errors).toEqual([]);
  } finally {
    await second.close();
  }
});
