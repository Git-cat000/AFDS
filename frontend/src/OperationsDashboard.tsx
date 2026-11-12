import { useQuery } from "@tanstack/react-query";
import { Activity, Database, HardDrive, RefreshCw } from "lucide-react";
import { api } from "./api";
import { statusLabel } from "./format";
import "./operations.css";

type RuntimeStatus = {
  status: string;
  database: { status: string; schema_version: string | null };
  worker: {
    status: string;
    online_count: number | null;
    last_seen_age_seconds: number | null;
    stale_after_seconds: number;
  };
  tools: Record<string, string>;
  storage: {
    status: string;
    free_bytes: number | null;
    total_bytes: number | null;
  };
};
type GraphNode = {
  id: string;
  type: "job" | "asset";
  label: string;
  operation?: string;
  state?: string;
  error_code?: string | null;
  media_type?: string;
};
type GraphEdge = { source: string; target: string; kind: string };
type ProjectOperations = {
  jobs: {
    total: number;
    state_counts: Record<string, number>;
    active_count: number;
    elapsed_ms: {
      sample_count: number;
      p50: number | null;
      p95: number | null;
    };
    sample_limit: number;
  };
  media: { asset_count: number; unique_files: number; bytes: number };
  budget: {
    spent: string;
    reserved: string;
    currency: string;
    unknown_count: number;
  };
  graph: {
    nodes: GraphNode[];
    edges: GraphEdge[];
    job_limit: number;
    media_limit: number;
    truncated: boolean;
  };
};
const operationNames: Record<string, string> = {
  generate_video: "生成镜头",
  generate_image: "生成图像",
  generate_speech: "合成配音",
  render_timeline: "渲染成片",
  import_audio: "导入音频",
  review_shot: "审片证据",
  export_project: "导出工程",
  restore_project: "恢复工程",
  existing_media: "已有素材",
  unavailable_media: "缺失素材引用",
};
function name(node: GraphNode) {
  return operationNames[node.operation || node.label] || "任务";
}
function bytes(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "未知";
  if (value < 1024) return `${value} B`;
  const unit = value >= 1024 ** 3 ? 3 : value >= 1024 ** 2 ? 2 : 1;
  return `${(value / 1024 ** unit).toFixed(1)} ${["B", "KB", "MB", "GB"][unit]}`;
}
function available(value: string) {
  return value === "available"
    ? "可用"
    : value === "unavailable"
      ? "不可用"
      : "未知";
}
function seconds(value: number | null) {
  return value === null ? "暂无样本" : `${(value / 1000).toFixed(2)} 秒`;
}

function TaskGraph({
  nodes,
  edges,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
}) {
  const levels = new Map(nodes.map((node) => [node.id, 0]));
  const indegree = new Map(
    nodes.map((node) => [
      node.id,
      edges.filter((edge) => edge.target === node.id).length,
    ]),
  );
  const queue = nodes
    .filter((node) => indegree.get(node.id) === 0)
    .map((node) => node.id);
  for (let index = 0; index < queue.length; index++) {
    const source = queue[index];
    for (const edge of edges.filter((item) => item.source === source)) {
      levels.set(
        edge.target,
        Math.min(
          6,
          Math.max(levels.get(edge.target) || 0, (levels.get(source) || 0) + 1),
        ),
      );
      indegree.set(edge.target, (indegree.get(edge.target) || 1) - 1);
      if (indegree.get(edge.target) === 0) queue.push(edge.target);
    }
  }
  const counts = new Map<number, number>();
  const positions = new Map(
    nodes.map((node) => {
      const level = levels.get(node.id) || 0,
        row = counts.get(level) || 0;
      counts.set(level, row + 1);
      return [node.id, { x: level * 220 + 16, y: row * 92 + 20 }];
    }),
  );
  const width = Math.max(
    450,
    ...[...positions.values()].map((point) => point.x + 210),
  );
  const height = Math.max(
    140,
    ...[...positions.values()].map((point) => point.y + 90),
  );
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  return (
    <>
      <div
        className="operations-graph-scroll"
        tabIndex={0}
        aria-label="可滚动的任务图"
      >
        <svg
          width={width}
          height={height}
          role="img"
          aria-label="任务素材流向图"
        >
          <title>任务的声明依赖与已观察到的素材流向</title>
          <defs>
            <marker
              id="operations-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
            </marker>
          </defs>
          {edges.map((edge, index) => {
            const from = positions.get(edge.source),
              to = positions.get(edge.target);
            if (!from || !to) return null;
            return (
              <path
                key={index}
                className={`graph-edge graph-edge-${edge.kind}`}
                d={`M ${from.x + 180} ${from.y + 30} C ${from.x + 205} ${from.y + 30}, ${to.x - 25} ${to.y + 30}, ${to.x} ${to.y + 30}`}
                markerEnd="url(#operations-arrow)"
              />
            );
          })}
          {nodes.map((node) => {
            const point = positions.get(node.id)!;
            return (
              <g
                key={node.id}
                transform={`translate(${point.x},${point.y})`}
                className={`graph-node graph-node-${node.state || "asset"}`}
              >
                <title>
                  {name(node)} · {node.id} ·{" "}
                  {node.error_code ||
                    statusLabel[node.state || ""] ||
                    "素材输入"}
                </title>
                <rect width="180" height="64" rx="9" />
                <text x="12" y="23">
                  {name(node)}
                </text>
                <text x="12" y="46" className="graph-node-meta">
                  {node.id.replace("asset:", "").slice(0, 8)} ·{" "}
                  {statusLabel[node.state || ""] || "素材输入"}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <details className="operations-relations">
        <summary>查看关系明细（{edges.length} 条）</summary>
        {edges.length ? (
          <ul>
            {edges.map((edge, index) => (
              <li key={index}>
                {name(nodeMap.get(edge.source)!)} {edge.source.slice(-6)} →{" "}
                {name(nodeMap.get(edge.target)!)} {edge.target.slice(-6)} ·{" "}
                {edge.kind === "dependency" ? "声明依赖" : "素材流向"}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">尚无已记录的任务关系。</p>
        )}
      </details>
    </>
  );
}

export function OperationsDashboard({ projectId }: { projectId?: string }) {
  const runtime = useQuery({
    queryKey: ["runtime-status"],
    queryFn: () => api<RuntimeStatus>("/system/status"),
    refetchInterval: 5000,
  });
  const project = useQuery({
    queryKey: ["operations", projectId],
    queryFn: () => api<ProjectOperations>(`/projects/${projectId}/operations`),
    enabled: !!projectId,
    refetchInterval: 5000,
  });
  const status = runtime.data,
    metrics = project.data;
  return (
    <section className="page operations-page">
      <header className="operations-header">
        <div>
          <p className="eyebrow">LOCAL WORKSPACE</p>
          <h1>运行概览</h1>
          <p className="muted">
            每 5 秒读取本机状态，不会触发模型调用或修改任务。
          </p>
        </div>
        <button
          onClick={() => {
            void runtime.refetch();
            if (projectId) void project.refetch();
          }}
        >
          <RefreshCw size={16} />
          刷新运行状态
        </button>
      </header>
      {(runtime.isError || project.isError) && (
        <p role="alert">暂时无法读取运行状态，请刷新或检查本机 API。</p>
      )}
      {runtime.isLoading && <p className="muted">正在读取本机运行状态…</p>}
      {status && (
        <div className="operations-cards">
          <article className="panel">
            <h3>
              <Activity size={18} /> 独立 Worker
            </h3>
            <strong>
              {status.worker.status === "online"
                ? `${status.worker.online_count} 个 Worker 在线`
                : status.worker.status === "offline"
                  ? "未检测到在线 Worker"
                  : "Worker 状态未知"}
            </strong>
            <p className="muted">
              {status.worker.last_seen_age_seconds === null
                ? "尚未记录到心跳"
                : `最近心跳 ${Math.floor(status.worker.last_seen_age_seconds)} 秒前`}
              ；超过 {status.worker.stale_after_seconds} 秒视为离线。
            </p>
            {status.worker.status === "offline" && (
              <p>请在项目终端运行 Worker 启动脚本。</p>
            )}
          </article>
          <article className="panel">
            <h3>
              <Database size={18} /> 数据与工具
            </h3>
            <p>
              数据库 · {available(status.database.status)} ·{" "}
              {status.database.schema_version || "版本未知"}
            </p>
            <ul>
              {Object.entries(status.tools).map(([tool, value]) => (
                <li key={tool}>
                  {tool === "otio" ? "OpenTimelineIO" : tool} ·{" "}
                  {available(value)}
                </li>
              ))}
            </ul>
          </article>
          <article className="panel">
            <h3>
              <HardDrive size={18} /> 本机存储
            </h3>
            <strong>{bytes(status.storage.free_bytes)} 可用</strong>
            <p className="muted">
              总容量 {bytes(status.storage.total_bytes)}
              。此处读取磁盘容量，不会清理素材或缓存。
            </p>
          </article>
        </div>
      )}
      {!projectId && (
        <div className="panel operations-empty">
          <h2>选择一个项目查看制作指标</h2>
          <p className="muted">
            打开项目后进入“运行概览”，即可查看任务状态、费用汇总和素材流向。
          </p>
        </div>
      )}
      {metrics && (
        <>
          <div className="operations-cards operations-metrics">
            <article className="panel">
              <h3>任务</h3>
              <strong>
                {metrics.jobs.total} 项 · {metrics.jobs.active_count} 项处理中
              </strong>
              <div className="operations-state-list">
                {Object.entries(metrics.jobs.state_counts).map(
                  ([state, count]) => (
                    <span key={state}>
                      {statusLabel[state] || state} {count}
                    </span>
                  ),
                )}
              </div>
            </article>
            <article className="panel">
              <h3>处理耗时</h3>
              <p>中位数 {seconds(metrics.jobs.elapsed_ms.p50)}</p>
              <p>P95 {seconds(metrics.jobs.elapsed_ms.p95)}</p>
              <small className="muted">
                最近 {metrics.jobs.sample_limit} 项任务中的{" "}
                {metrics.jobs.elapsed_ms.sample_count} 个已结束样本。
              </small>
            </article>
            <article className="panel">
              <h3>项目素材与费用</h3>
              <p>
                {metrics.media.asset_count} 个素材 ·{" "}
                {metrics.media.unique_files} 个文件 ·{" "}
                {bytes(metrics.media.bytes)}
              </p>
              <p>
                已对账 {metrics.budget.spent} {metrics.budget.currency}
              </p>
              <p>
                预留/待对账估算 {metrics.budget.reserved}{" "}
                {metrics.budget.currency}
              </p>
              <small className="muted">
                未知费用 {metrics.budget.unknown_count}{" "}
                项；实际账单请在任务队列核对。
              </small>
            </article>
          </div>
          <section className="panel operations-flow">
            <h2>任务与素材流向</h2>
            <p className="muted">
              实线为素材流向，虚线为已声明的任务依赖。素材流向不表示排队阻塞。
            </p>
            {metrics.graph.truncated && (
              <p className="protocol-note">
                仅展示最近 {metrics.graph.job_limit} 项任务及最多{" "}
                {metrics.graph.media_limit}{" "}
                个已有素材输入，部分关系已省略；统计仍包含全部任务。
              </p>
            )}
            {metrics.graph.nodes.length ? (
              <TaskGraph
                nodes={metrics.graph.nodes}
                edges={metrics.graph.edges}
              />
            ) : (
              <p className="muted">
                项目尚无任务。生成、导入、审片或导出后可在这里查看。
              </p>
            )}
          </section>
        </>
      )}
    </section>
  );
}
