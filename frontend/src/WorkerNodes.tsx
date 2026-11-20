import { useState } from "react";
import { api } from "./api";
import { statusLabel } from "./format";

export type WorkerNode = {
  id: string;
  label: string;
  status: string;
  draining: boolean;
  slots: number;
  last_seen_at: number;
  last_seen_age_seconds?: number;
  app_version: string;
  runtime?: { python: string; platform: string };
  capabilities: string[];
  operations: string[];
  storage: { status: string; reason?: string };
  current_tasks: { id: string; project_id: string; state: string }[];
};

const nodeStates: Record<string, string> = {
  online: "在线",
  offline: "离线",
  draining: "排空中",
  unavailable: "不可用",
};
const capabilityNames: Record<string, string> = {
  python: "Python",
  media: "FFmpeg / ffprobe",
  node: "Node.js",
  powershell: "PowerShell",
  bash: "Bash",
};

export function WorkerNodes({
  nodes,
  onChanged,
}: {
  nodes: WorkerNode[];
  onChanged: () => void;
}) {
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  async function drain(node: WorkerNode) {
    setPending(node.id);
    setError("");
    try {
      await api(`/system/workers/${encodeURIComponent(node.id)}/drain`, {
        draining: !node.draining,
      });
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "未能更新节点状态");
    } finally {
      setPending("");
    }
  }
  return (
    <section className="panel worker-nodes" aria-label="执行节点">
      <h2>执行节点</h2>
      {error && <p role="alert">{error}</p>}
      {!nodes.length && <p className="muted">尚未记录执行节点。</p>}
      <div className="worker-node-grid">
        {nodes.map((node) => (
          <article key={node.id}>
            <header>
              <h3>{node.label}</h3>
              <span>{nodeStates[node.status] || node.status}</span>
            </header>
            <p>
              {node.current_tasks.length} / {node.slots} 槽位
            </p>
            <p className="muted">
              v{node.app_version} · 最近心跳{" "}
              {Math.floor(node.last_seen_age_seconds || 0)} 秒前
            </p>
            {node.runtime && (
              <p className="muted">
                {node.runtime.platform} · Python {node.runtime.python}
              </p>
            )}
            <p>
              {node.capabilities
                .map((value) =>
                  value.startsWith("sandbox:")
                    ? "隔离容器"
                    : capabilityNames[value] || value,
                )
                .join("、")}
            </p>
            <details>
              <summary>允许任务</summary>
              <p className="worker-operations">{node.operations.join("、")}</p>
            </details>
            <p>
              {node.storage.status === "available"
                ? "共享存储验证通过"
                : node.storage.reason || "共享存储不可用"}
            </p>
            {node.current_tasks.length > 0 && (
              <ul>
                {node.current_tasks.map((job) => (
                  <li key={job.id}>
                    <code>{job.id}</code> ·{" "}
                    {statusLabel[job.state] || job.state}
                  </li>
                ))}
              </ul>
            )}
            <button
              disabled={!!pending}
              aria-label={`${node.draining ? "恢复接单" : "排空"} ${node.label}`}
              onClick={() => void drain(node)}
            >
              {pending === node.id
                ? "正在更新…"
                : node.draining
                  ? "恢复接单"
                  : "排空节点"}
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
