import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

function audioFixture() {
  const rate = 24000,
    samples = 24000,
    data = Buffer.alloc(44 + samples * 2);
  data.write("RIFF");
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
  data.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index++)
    data.writeInt16LE(
      Math.round(Math.sin((index * Math.PI) / 30) * 2000),
      44 + index * 2,
    );
  return data;
}

test("freely places mixed media, drags groups, connects inputs and persists the board", async ({
  page,
}) => {
  const errors: string[] = [],
    modelCalls: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const scene = randomUUID(),
    shot = randomUUID(),
    title = `DEMO media board ${Date.now()}`;
  const response = await page.request.post("/api/v1/projects/import", {
    data: {
      title,
      brief: "DEMO / MOCK material canvas",
      format: { width: 320, height: 180, fps_num: 24, fps_den: 1 },
      scenes: [{ id: scene, order: 0, title: "DEMO scene", shot_ids: [shot] }],
      shots: [
        {
          id: shot,
          scene_id: scene,
          order: 0,
          title: "DEMO target shot",
          duration_frames: 48,
        },
      ],
      timeline: {
        tracks: [
          {
            id: "v1",
            type: "video",
            items: [{ shot_id: shot, start_frame: 0, duration_frames: 48 }],
          },
        ],
      },
    },
  });
  expect(response.status()).toBe(201);
  let apr = await response.json();
  const root = `/api/v1/projects/${apr.project_id}`;
  const latest = async () => (await page.request.get(`${root}/apr`)).json();
  const waitJob = async (id: string) =>
    expect
      .poll(
        async () =>
          (await (await page.request.get(`${root}/jobs/${id}`)).json()).state,
      )
      .toBe("completed");
  const generated = await page.request.post(`${root}/generate`, {
    data: {
      base_revision: apr.revision,
      shot_ids: [apr.shots[0].id],
      provider_id: "mock-video-local",
    },
  });
  await waitJob((await generated.json())[0].id);
  apr = await latest();
  const poster = await page.request.get(
    `${root}/assets/${apr.asset_versions[0].poster_asset_id}/file`,
  );
  const imported = await page.request.post(
    `${root}/images/import?base_revision=${apr.revision}&name=DEMO-reference.jpg&rights_confirmed=true`,
    { headers: { "Content-Type": "image/jpeg" }, data: await poster.body() },
  );
  expect(imported.status()).toBe(202);
  await waitJob((await imported.json()).id);
  apr = await latest();
  const audio = await page.request.post(
    `${root}/timeline/audio/import?base_revision=${apr.revision}&name=DEMO-audio.wav&rights_confirmed=true`,
    { headers: { "Content-Type": "audio/wav" }, data: audioFixture() },
  );
  expect(audio.status()).toBe(202);
  await waitJob((await audio.json()).id);
  apr = await latest();
  const image = apr.assets.find(
    (asset: { provenance: { name?: string } }) =>
      asset.provenance.name === "DEMO-reference.jpg",
  );
  const video = apr.assets.find(
    (asset: { media_type: string }) => asset.media_type === "video",
  );
  const sound = apr.assets.find(
    (asset: { media_type: string }) => asset.media_type === "audio",
  );
  const sourceRevision = apr.revision;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/(generate|media|reviews|planning\/[^/]+)$/.test(
        new URL(request.url()).pathname,
      )
    )
      modelCalls.push(request.url());
  });
  await page.goto("/");
  await page.getByRole("button").filter({ hasText: title }).click();
  await page.getByRole("button", { name: "素材抽屉", exact: true }).click();
  const canvas = page.getByLabel("镜头创作画布", { exact: true });
  const readBoard = async () =>
    page.evaluate(
      (pid) => JSON.parse(localStorage.getItem(`afds-canvas-${pid}`) || "{}"),
      apr.project_id,
    );
  const addMedia = async (
    asset: { id: string; provenance: { name?: string } },
    x: number,
    y: number,
  ) => {
    const button = page.getByRole("button", {
      name: `放入画布 ${asset.provenance.name || asset.id}`,
      exact: true,
    });
    const card = button.locator("..");
    await card.scrollIntoViewIfNeeded();
    const start = (await card.boundingBox())!;
    const area = (await canvas.boundingBox())!;
    const targetY = Math.min(area.y + y, page.viewportSize()!.height - 30);
    // Move through dragstart and dragover before releasing the native payload.
    await page.mouse.move(start.x + 30, start.y + 12);
    await page.mouse.down();
    await page.mouse.move(start.x + 45, start.y + 27, { steps: 3 });
    await page.mouse.move(area.x + x, targetY, { steps: 10 });
    await page.mouse.move(area.x + x + 1, targetY + 1);
    await page.mouse.up();
    await expect
      .poll(
        async () =>
          (await readBoard()).nodes?.some(
            (item: { assetId?: string }) => item.assetId === asset.id,
          ),
        { timeout: 10000 },
      )
      .toBeTruthy();
    return (await readBoard()).nodes.find(
      (item: { assetId?: string }) => item.assetId === asset.id,
    ).id as string;
  };
  const imageNode = await addMedia(image, 390, 190);
  const videoNode = await addMedia(video, 75, 350);
  const audioNode = await addMedia(sound, 390, 460);
  await page.getByRole("button", { name: "添加文字", exact: true }).click();
  const note = page.getByPlaceholder("写下想法、提示词或制作说明");
  await note.fill("DEMO 镜头情绪与视觉参考");
  const noteNode = (await readBoard()).nodes.find(
    (item: { kind: string }) => item.kind === "note",
  ).id as string;
  const flowNode = (id: string) =>
    page.locator(`.react-flow__node[data-id="${id}"]`);
  await page.getByRole("button", { name: "自动排列", exact: true }).click();
  await page.getByRole("button", { name: "适应全部节点" }).click();
  const beforeKeyboard = await readBoard();
  await flowNode(videoNode).focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("ArrowDown");
  await expect
    .poll(async () => (await readBoard()).positions[videoNode].y, {
      timeout: 5000,
    })
    .not.toBe(beforeKeyboard.positions[videoNode].y);
  await flowNode(videoNode).locator(".node-drag-handle").click();
  await flowNode(audioNode)
    .locator(".node-drag-handle")
    .click({ modifiers: ["Shift"] });
  await expect(flowNode(videoNode)).toHaveClass(/selected/);
  await expect(flowNode(audioNode)).toHaveClass(/selected/);
  const beforeGroup = await readBoard();
  const grip = (await flowNode(videoNode)
    .locator(".node-drag-handle")
    .boundingBox())!;
  await page.mouse.move(grip.x + 30, grip.y + 12);
  await page.mouse.down();
  await page.mouse.move(grip.x + 78, grip.y + 46, { steps: 7 });
  await page.mouse.up();
  await expect
    .poll(async () => (await readBoard()).positions[videoNode].x)
    .not.toBe(beforeGroup.positions[videoNode].x);
  const afterGroup = await readBoard();
  expect(
    afterGroup.positions[videoNode].x - beforeGroup.positions[videoNode].x,
  ).toBeCloseTo(
    afterGroup.positions[audioNode].x - beforeGroup.positions[audioNode].x,
    1,
  );
  await flowNode(noteNode).locator(".node-drag-handle").focus();
  for (let index = 0; index < 35; index++)
    await page.keyboard.press("ArrowLeft");
  expect((await readBoard()).positions[noteNode].x).toBeLessThan(0);
  await page.getByRole("button", { name: "适应全部节点" }).click();
  const out = flowNode(imageNode).locator(".react-flow__handle.source"),
    input = flowNode(apr.shots[0].id).locator(".react-flow__handle.target");
  await out.dragTo(input);
  await expect.poll(async () => (await readBoard()).edges?.length).toBe(1);
  let edgeId = (await readBoard()).edges[0].id;
  const line = () => page.locator(`.react-flow__edge[data-id="${edgeId}"]`);
  await line().focus();
  await page.keyboard.press("Enter");
  await page.getByLabel("连线名称", { exact: true }).fill("视觉参考");
  expect((await latest()).revision).toBe(sourceRevision);
  await line().focus();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "连线设置" })).toHaveCount(0);
  await line().focus();
  await page.keyboard.press("Enter");
  await flowNode(noteNode).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("region", { name: "连线设置" })).toHaveCount(0);
  await page.keyboard.press("Delete");
  await expect(flowNode(noteNode)).toHaveCount(0);
  expect((await readBoard()).edges).toHaveLength(1);
  await page.getByRole("button", { name: "撤销画布操作" }).click();
  await expect(flowNode(noteNode)).toHaveCount(1);
  await line().focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "删除连线", exact: true }).click();
  expect((await readBoard()).edges).toHaveLength(0);
  await page.getByRole("button", { name: "撤销画布操作" }).click();
  await expect.poll(async () => (await readBoard()).edges?.length).toBe(1);
  edgeId = (await readBoard()).edges[0].id;
  await line().focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "用作镜头输入", exact: true }).click();
  const assignment = page.getByRole("dialog", { name: "将图像用于镜头" });
  await assignment.getByLabel("图像用途").selectOption("start_image");
  await assignment.getByRole("button", { name: "预览图像关联" }).click();
  expect((await latest()).revision).toBe(sourceRevision);
  await assignment.getByRole("button", { name: "批准关联到镜头" }).click();
  await expect
    .poll(async () => (await latest()).shots[0].reference_images.start_image)
    .toBe(image.id);
  await expect(page.locator(".effective-input-edge")).toHaveCount(1);
  const actual = page.locator(".effective-input-edge");
  await actual.focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("button", { name: "预览移除镜头输入", exact: true })
    .click();
  const removal = page.getByRole("dialog", { name: "移除镜头输入" });
  await removal.getByRole("button", { name: "预览移除图像关联" }).click();
  await removal.getByRole("button", { name: "批准移除镜头输入" }).click();
  await expect
    .poll(
      async () =>
        Object.keys((await latest()).shots[0].reference_images).length,
    )
    .toBe(0);
  await flowNode(noteNode).locator(".node-drag-handle").click();
  await page.getByRole("button", { name: "移出画布", exact: true }).click();
  await expect(flowNode(noteNode)).toHaveCount(0);
  await page.getByRole("button", { name: "撤销画布操作" }).click();
  await expect(flowNode(noteNode)).toHaveCount(1);
  await page.getByRole("button", { name: "重做画布操作" }).click();
  await expect(flowNode(noteNode)).toHaveCount(0);
  await page.getByRole("button", { name: "撤销画布操作" }).click();
  await page.reload();
  await expect(page.getByPlaceholder("写下想法、提示词或制作说明")).toHaveValue(
    "DEMO 镜头情绪与视觉参考",
  );
  await expect(page.getByTestId("material-node")).toHaveCount(4);
  await expect.poll(async () => (await readBoard()).edges.length).toBe(1);
  await page.getByRole("button", { name: "适应全部节点" }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../data/material-canvas-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "适应全部节点" }).click();
  await expect(page.getByTestId("material-node")).toHaveCount(4);
  await page.screenshot({
    path: "../data/material-canvas-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(modelCalls).toEqual([]);
  expect(errors).toEqual([]);
});
