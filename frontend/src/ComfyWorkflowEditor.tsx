import { useState } from "react";
import type { WorkflowTemplate } from "./types";

export function ComfyWorkflowEditor({
  value,
  mediaType,
  onChange,
}: {
  value: WorkflowTemplate | null;
  mediaType: "image" | "video";
  onChange: (value: WorkflowTemplate) => void;
}) {
  const [error, setError] = useState("");
  const choices = Object.entries(value?.graph || {}).flatMap(([id, node]) =>
    Object.entries(node.inputs || {})
      .filter(
        ([, input]) => typeof input === "string" || typeof input === "number",
      )
      .map(([input]) => ({
        value: JSON.stringify([id, input]),
        label: `${id} · ${node._meta?.title || node.class_type} / ${input}`,
      })),
  );

  async function upload(file?: File) {
    if (!file) return;
    setError("");
    try {
      if (file.size > 2_000_000) throw new Error("工作流文件不能超过 2 MB");
      const parsed = JSON.parse(await file.text());
      const graph = parsed.prompt || parsed;
      const nodes = Object.entries(graph) as [
        string,
        WorkflowTemplate["graph"][string],
      ][];
      if (
        !nodes.length ||
        nodes.some(
          ([, node]) =>
            !node || typeof node.class_type !== "string" || !node.inputs,
        )
      ) {
        throw new Error(
          "请从 ComfyUI 导出 API 格式工作流，普通编辑器 JSON 无法使用。",
        );
      }
      onChange({
        graph,
        bindings: {},
        output_node: nodes.at(-1)![0],
        output_field: mediaType === "image" ? "images" : "gifs",
        media_type: mediaType,
        seed: 0,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "工作流读取失败");
    }
  }
  return (
    <section className="workflow-editor">
      <p className="protocol-note">
        导入 ComfyUI 的 API
        格式工作流，再选择提示词输入和保存输出节点。模型及自定义节点需在 ComfyUI
        端准备好。
      </p>
      <label>
        导入 API 工作流
        <input
          aria-label="导入 API 工作流"
          type="file"
          accept=".json,application/json"
          onChange={(e) => void upload(e.target.files?.[0])}
        />
      </label>
      {error && (
        <p role="alert" className="settings-error">
          {error}
        </p>
      )}
      {value && (
        <>
          <p className="muted">
            已导入 {Object.keys(value.graph).length} 个节点 ·{" "}
            {value.media_type === "video" ? "视频" : "图像"}输出
          </p>
          <div className="editor-grid">
            {(
              ["prompt", "seed", "width", "height", "frames", "fps"] as const
            ).map((name) => (
              <label key={name}>
                {
                  {
                    prompt: "提示词映射（必选）",
                    seed: "种子映射",
                    width: "宽度映射",
                    height: "高度映射",
                    frames: "帧数映射",
                    fps: "帧率映射",
                  }[name]
                }
                <select
                  aria-label={`${name} 映射`}
                  value={
                    value.bindings[name]
                      ? JSON.stringify([
                          value.bindings[name]!.node_id,
                          value.bindings[name]!.input_name,
                        ])
                      : ""
                  }
                  onChange={(e) => {
                    const bindings = { ...value.bindings };
                    if (e.target.value) {
                      const [node_id, input_name] = JSON.parse(e.target.value);
                      bindings[name] = { node_id, input_name };
                    } else delete bindings[name];
                    onChange({ ...value, bindings });
                  }}
                >
                  <option value="">
                    {name === "prompt" ? "选择提示词输入" : "保留工作流原值"}
                  </option>
                  {choices.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <label>
              保存输出节点
              <select
                aria-label="保存输出节点"
                value={value.output_node}
                onChange={(e) =>
                  onChange({ ...value, output_node: e.target.value })
                }
              >
                {Object.entries(value.graph).map(([id, node]) => (
                  <option key={id} value={id}>
                    {id} · {node._meta?.title || node.class_type}
                  </option>
                ))}
              </select>
            </label>
            <label>
              历史输出字段
              <select
                value={value.output_field}
                onChange={(e) =>
                  onChange({
                    ...value,
                    output_field: e.target
                      .value as WorkflowTemplate["output_field"],
                  })
                }
              >
                <option value="images">images · 图像</option>
                <option value="gifs">gifs · 视频节点常用</option>
                <option value="videos">videos · 视频</option>
              </select>
            </label>
            <label>
              固定种子
              <input
                type="number"
                min="0"
                max="9007199254740991"
                value={value.seed}
                onChange={(e) =>
                  onChange({ ...value, seed: Number(e.target.value) })
                }
              />
            </label>
          </div>
          <small>
            映射只修改指定的输入；未映射的参数沿用工作流。保存后测试连接会核对服务端节点，实际媒体类型在生成后验证。
          </small>
        </>
      )}
    </section>
  );
}
