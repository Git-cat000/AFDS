import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { cpus, totalmem } from "node:os";
import { expect, test, type Page } from "@playwright/test";
import { demoClickTrack } from "./audio-fixtures";

test.skip(
  process.env.AFDS_TEST_DEPLOYMENT_BROWSER !== "1",
  "Run the isolated team deployment profile",
);

async function login(page: Page, role: string) {
  await page.goto("/");
  await page.getByRole("textbox", { name: "用户名" }).fill(`demo-${role}`);
  await page
    .getByLabel("密码", { exact: true })
    .fill(`DEMO-${role}-password-2026`);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "创作工作区" })).toBeVisible();
}

test("recovers a team film and checks five editing clients with two independent local workers", async ({
  page,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page, "owner");
  const session = await (await page.request.get("/api/v1/auth/session")).json();
  const headers = { "X-CSRF-Token": session.csrf_token };
  const outsider = await browser.newContext({
    baseURL: new URL(page.url()).origin,
  });
  expect((await outsider.request.get("/api/v1/projects")).status()).toBe(401);
  await outsider.close();
  const scene = randomUUID(),
    shots = [randomUUID(), randomUUID()];
  const title = `DEMO deployment ${Date.now()}`;
  const imported = await page.request.post("/api/v1/projects/import", {
    headers,
    data: {
      title,
      format: { width: 320, height: 180 },
      scenes: [{ id: scene, order: 0, title: "DEMO", shot_ids: shots }],
      shots: shots.map((id, order) => ({
        id,
        scene_id: scene,
        order,
        title: `DEMO ${order}`,
        duration_frames: 48,
      })),
      timeline: {
        tracks: [
          {
            id: "v1",
            type: "video",
            items: shots.map((shot_id, i) => ({
              shot_id,
              start_frame: i * 48,
              duration_frames: 48,
            })),
          },
        ],
      },
    },
  });
  expect(imported.status()).toBe(201);
  let apr = await imported.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  const latest = async () => (await page.request.get(root + "/apr")).json();
  expect(
    (
      await page.request.post(root + "/members", {
        headers,
        data: { username: "demo-editor", role: "editor" },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(root + "/generate", {
        data: { base_revision: apr.revision, shot_ids: shots },
      })
    ).status(),
  ).toBe(403);
  const noteIds = Array.from({ length: 5 }, (_, i) => `note-load-${i}`);
  expect(
    (
      await page.request.put(root + "/workspace", {
        headers,
        data: {
          base_workspace_revision: 0,
          document: {
            version: 2,
            nodes: noteIds.map((id) => ({
              id,
              kind: "note",
              text: "DEMO initial",
            })),
            edges: [],
            positions: Object.fromEntries(
              noteIds.map((id, i) => [id, { x: i * 240, y: 0 }]),
            ),
            hiddenShots: [],
          },
        },
      })
    ).status(),
  ).toBe(200);
  await page.reload();
  await page.getByRole("button").filter({ hasText: title }).click();
  const clients = await Promise.all(
    noteIds.map(async () => {
      const context = await browser.newContext({
        baseURL: new URL(page.url()).origin,
      });
      const editor = await context.newPage();
      editor.on("pageerror", (error) => errors.push(error.message));
      await login(editor, "editor");
      await editor.getByRole("button").filter({ hasText: title }).click();
      return { context, page: editor };
    }),
  );
  let syncMs = 0;
  try {
    const started = Date.now();
    await Promise.all(
      clients.map(async (client, i) => {
        await client.page
          .getByRole("textbox", { name: `文字内容 ${noteIds[i]}`, exact: true })
          .fill(`DEMO edited ${i}`, { force: true });
      }),
    );
    await expect
      .poll(async () => {
        const workspace = await (
          await page.request.get(root + "/workspace")
        ).json();
        return workspace.document.nodes.filter((node: { text: string }) =>
          node.text.startsWith("DEMO edited"),
        ).length;
      })
      .toBe(5);
    await Promise.all(
      clients.map(async (client) => {
        for (let i = 0; i < noteIds.length; i++)
          await expect(
            client.page.getByRole("textbox", {
              name: `文字内容 ${noteIds[i]}`,
              exact: true,
            }),
          ).toHaveValue(`DEMO edited ${i}`);
      }),
    );
    syncMs = Date.now() - started;
    const editorPage = clients[0].page;
    const readFirstNote = async () => {
      const workspace = await (
        await page.request.get(root + "/workspace")
      ).json();
      return workspace.document.nodes.find(
        (node: { id: string }) => node.id === noteIds[0],
      ).text;
    };
    for (const text of ["DEMO own edit B", "DEMO own edit C"]) {
      await editorPage
        .getByRole("textbox", { name: `文字内容 ${noteIds[0]}`, exact: true })
        .fill(text, { force: true });
      await expect.poll(readFirstNote).toBe(text);
      await expect(
        editorPage.getByText("协作画布已同步", { exact: true }),
      ).toBeVisible();
    }
    for (const [button, values] of [
      ["撤销画布操作", ["DEMO own edit B", "DEMO edited 0"]],
      ["重做画布操作", ["DEMO own edit B", "DEMO own edit C"]],
      ["撤销画布操作", ["DEMO own edit B", "DEMO edited 0"]],
    ] as const) {
      for (const text of values) {
        await editorPage
          .getByRole("button", { name: button, exact: true })
          .click();
        await expect.poll(readFirstNote).toBe(text);
        await expect(
          editorPage.getByRole("textbox", {
            name: `文字内容 ${noteIds[0]}`,
            exact: true,
          }),
        ).toHaveValue(text);
      }
    }
    const roster = await (await page.request.get(root + "/members")).json();
    const editor = roster.find(
      (member: { username: string }) => member.username === "demo-editor",
    );
    expect(
      (
        await page.request.delete(root + "/members/" + editor.id, { headers })
      ).status(),
    ).toBe(200);
    expect((await clients[0].page.request.get(root + "/apr")).status()).toBe(
      404,
    );
  } finally {
    await Promise.all(clients.map((client) => client.context.close()));
  }
  const nodes = await (await page.request.get("/api/v1/system/workers")).json();
  expect(
    nodes.filter((node: { id: string }) =>
      ["demo-media-1", "demo-media-2"].includes(node.id),
    ),
  ).toHaveLength(2);
  const generated = await page.request.post(root + "/generate", {
    headers,
    data: {
      base_revision: apr.revision,
      shot_ids: apr.shots.map((shot: { id: string }) => shot.id),
      provider_id: "mock-video-local",
      request_id: randomUUID(),
    },
  });
  expect(generated.status(), await generated.text()).toBe(202);
  const jobIds = (await generated.json()).map((job: { id: string }) => job.id);
  for (const id of jobIds)
    await expect
      .poll(
        async () =>
          (await (await page.request.get(root + "/jobs/" + id)).json()).state,
      )
      .toBe("completed");
  const finished = await (
    await page.request.get(root + "/jobs/" + jobIds[0])
  ).json();
  const firstNode = finished.worker_node_id;
  expect(
    (
      await page.request.post(`/api/v1/system/workers/${firstNode}/drain`, {
        headers,
        data: { draining: true },
      })
    ).status(),
  ).toBe(200);
  try {
    apr = await latest();
    await page.getByRole("button", { name: "音画时间轴", exact: true }).click();
    await page.getByRole("button", { name: "添加视频轨", exact: true }).click();
    await page
      .getByRole("combobox", { name: "叠加素材", exact: true })
      .selectOption(
        apr.assets.find(
          (asset: { media_type: string }) => asset.media_type === "video",
        ).id,
      );
    await page
      .getByRole("button", { name: "添加叠加片段", exact: true })
      .click();
    for (const label of ["水平位置", "垂直位置", "宽度", "高度"])
      await page.getByLabel(label, { exact: true }).fill("50");
    await page.getByRole("button", { name: "保存视频轨", exact: true }).click();
    await expect
      .poll(async () => (await latest()).timeline.video_layers[0]?.items.length)
      .toBe(1);
    await page.getByLabel("选择音频文件", { exact: true }).setInputFiles({
      name: "DEMO-clicks.wav",
      mimeType: "audio/wav",
      buffer: demoClickTrack(),
    });
    await page
      .getByLabel("我确认拥有该音频的使用权或已获得授权", { exact: true })
      .check();
    await page.getByRole("button", { name: "导入音频", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await latest()).assets.filter(
            (asset: { media_type: string }) => asset.media_type === "audio",
          ).length,
      )
      .toBe(1);
    apr = await latest();
    await expect(
      page.getByText(new RegExp(`修订 ${apr.revision}$`)).first(),
    ).toBeVisible();
    await page.getByRole("button", { name: "新增音轨", exact: true }).click();
    await expect
      .poll(async () => (await latest()).timeline.tracks.length)
      .toBe(2);
    await page.getByLabel("音频持续帧", { exact: true }).fill("96");
    await page
      .getByRole("button", { name: "添加音频片段", exact: true })
      .click();
    await expect
      .poll(async () => (await latest()).timeline.tracks[1]?.items.length)
      .toBe(1);
    await page
      .getByLabel("字幕内容", { exact: true })
      .fill("DEMO deployment subtitle");
    await page.getByRole("button", { name: "添加字幕", exact: true }).click();
    await expect
      .poll(async () => (await latest()).timeline.captions.length)
      .toBe(1);
    await page.getByRole("button", { name: "渲染成片", exact: true }).click();
    await expect.poll(async () => (await latest()).render_status).toBe("ready");
    apr = await latest();
    const download = await page.request.get(
      `${root}/assets/${apr.render_asset_id}/file`,
    );
    expect(download.status()).toBe(200);
    await writeFile("../data/deployment-output.mp4", await download.body());
    const allJobs = await (await page.request.get(root + "/jobs")).json();
    const usedNodes = [
      ...new Set(
        allJobs
          .map((job: { worker_node_id: string }) => job.worker_node_id)
          .filter(Boolean),
      ),
    ];
    expect(usedNodes.sort()).toEqual(["demo-media-1", "demo-media-2"]);
    expect(
      allJobs.every((job: { state: string }) => job.state === "completed"),
    ).toBe(true);
    await page.getByRole("button", { name: "导出工程包", exact: true }).click();
    let exportId = "";
    await expect
      .poll(async () => {
        const jobs = await (await page.request.get(root + "/jobs")).json();
        const result = jobs.find(
          (job: { operation: string }) => job.operation === "export_project",
        );
        exportId = result?.id || "";
        return result?.state;
      })
      .toBe("completed");
    const archive = await page.request.get(`${root}/exports/${exportId}/file`);
    expect(archive.status()).toBe(200);
    await page
      .getByRole("button", { name: "返回项目列表", exact: true })
      .click();
    await page.getByLabel("工程包 ZIP").setInputFiles({
      name: "DEMO-recovery.zip",
      mimeType: "application/zip",
      buffer: await archive.body(),
    });
    const restoredTitle = title + " restored";
    await page.getByLabel("恢复副本名称").fill(restoredTitle);
    await page
      .getByRole("button", { name: "恢复为新项目", exact: true })
      .click();
    await expect(
      page.getByRole("textbox", {
        name: `文字内容 ${noteIds[0]}`,
        exact: true,
      }),
    ).toHaveValue("DEMO edited 0");
    const projects = await (await page.request.get("/api/v1/projects")).json();
    const restored = await (
      await page.request.get(
        `/api/v1/projects/${projects.find((value: { title: string }) => value.title === restoredTitle).project_id}/apr`,
      )
    ).json();
    expect(restored.timeline.video_layers).toHaveLength(1);
    expect(restored.timeline.tracks).toHaveLength(2);
    expect(
      restored.assets.map((asset: { sha256: string }) => asset.sha256).sort(),
    ).toEqual(
      apr.assets.map((asset: { sha256: string }) => asset.sha256).sort(),
    );
    expect(errors).toEqual([]);
    await writeFile(
      "../data/deployment-local-evidence.json",
      JSON.stringify({
        topology:
          "one Windows API, two Windows Workers, SQLite, five Chromium editor contexts",
        docker_acceptance: "deferred by user",
        cpu_count: cpus().length,
        memory_bytes: totalmem(),
        width: 320,
        height: 180,
        fps: 24,
        frames: 96,
        editing_clients: 5,
        sync_ms: syncMs,
        used_nodes: usedNodes,
        jobs: allJobs.map(
          (job: { id: string; state: string; elapsed_ms: number }) => ({
            id: job.id,
            state: job.state,
            elapsed_ms: job.elapsed_ms,
          }),
        ),
        errors,
      }),
      "utf-8",
    );
  } finally {
    await page.request.post(`/api/v1/system/workers/${firstNode}/drain`, {
      headers,
      data: { draining: false },
    });
  }
});
