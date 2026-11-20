import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { statusLabel } from "./format";
import { useCursorPage, type JobPage } from "./history";
import { ProjectBudget } from "./ProjectBudget";
import { ProjectRouting } from "./ProjectRouting";
import { RouteExplanation } from "./RouteExplanation";
import { useProjectAccess } from "./session";
import type { RouteDecision } from "./routing-types";
import { RemoteJobRecovery } from "./RemoteJobRecovery";
import type { Job, Project } from "./types";

type Run = (action: () => Promise<unknown>) => Promise<void>;
function JobDetail({ job, projectId }: { job: Job; projectId: string }) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["job-detail", projectId, job.id],
    enabled: open,
    queryFn: () => api<Job>(`/projects/${projectId}/jobs/${job.id}`),
  });
  const request = detail.data?.request as
    | { provider_config?: { route?: RouteDecision }; route?: RouteDecision }
    | undefined;
  const route = request?.provider_config?.route || request?.route;
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>请求与溯源</summary>
      {open && (
        <>
          {detail.error && <p role="alert">{detail.error.message}</p>}
          {route && <RouteExplanation decision={route} />}
          <pre>
            {detail.data
              ? JSON.stringify(detail.data.request, null, 2)
              : "正在读取…"}
          </pre>
          {detail.data?.cleanup_result && (
            <pre>{JSON.stringify(detail.data.cleanup_result, null, 2)}</pre>
          )}
          {detail.data?.skill_result && (
            <pre>{JSON.stringify(detail.data.skill_result, null, 2)}</pre>
          )}
          <small>输入哈希 {job.input_summary?.request_hash}</small>
          {detail.data?.worker_node_id && (
            <small>执行节点 {detail.data.worker_node_id}</small>
          )}
          {detail.data?.actor_id && (
            <small>发起者 {detail.data.actor_id}</small>
          )}
          {detail.data?.execution_actor_id && (
            <small>本次执行审批者 {detail.data.execution_actor_id}</small>
          )}
        </>
      )}
    </details>
  );
}
export function JobList({
  project,
  run,
  busy,
  events,
}: {
  project: Project;
  run: Run;
  busy: boolean;
  events: { event_id: number; type: string; timestamp: number }[];
}) {
  const [state, setState] = useState("all");
  const access = useProjectAccess(project.project_id);
  return (
    <section className="page compact">
      <ProjectBudget projectId={project.project_id} run={run} busy={busy} />
      <ProjectRouting
        key={project.project_id}
        projectId={project.project_id}
        run={run}
        busy={busy}
        canManage={!!access?.actions.includes("manage_budget")}
      />
      <label className="job-filter">
        任务状态
        <select value={state} onChange={(e) => setState(e.target.value)}>
          {Object.entries({
            all: "全部",
            active: "处理中",
            completed: "已完成",
            failed: "失败",
            cancelled: "已取消",
            needs_review: "待审核",
          }).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <JobRows
        key={`${project.project_id}:${state}`}
        project={project}
        state={state}
        run={run}
        busy={busy}
      />
      <details className="panel event-log">
        <summary>最近 500 条持久化事件</summary>
        {events.map((e) => (
          <div key={e.event_id}>
            <small>
              #{e.event_id} ·{" "}
              {new Date(e.timestamp * 1000).toLocaleTimeString()}
            </small>{" "}
            {e.type}
          </div>
        ))}
      </details>
    </section>
  );
}
function JobRows({
  project,
  state,
  run,
  busy,
}: {
  project: Project;
  state: string;
  run: Run;
  busy: boolean;
}) {
  const page = useCursorPage<Job>(
    "jobs",
    project.project_id,
    `jobs/page?state=${state}`,
  );
  const data = page.data as JobPage | undefined;
  return (
    <>
      <div className="section-heading">
        <h2>任务与执行记录</h2>
        <span>共 {data?.total ?? 0} 项任务</span>
      </div>
      {page.error && <p role="alert">{page.error.message}</p>}
      {page.pagination}
      {data?.items.map((j) => (
        <article className="job-row panel" key={j.id}>
          <div>
            <h3>
              {(
                {
                  generate_video: "生成视频",
                  generate_image: "生成参考图像",
                  generate_speech: "合成语音旁白",
                  import_audio: "导入音频素材",
                  import_image: "导入参考图像",
                  review_shot: "镜头审片与证据",
                  export_project: "导出工程",
                  restore_project: "恢复工程副本",
                  plan_storyboard: "生成分镜建议",
                  plan_semantic: "生成语义修改建议",
                  cleanup_storage: "清理存储",
                  render_timeline: "时间轴渲染",
                } as Record<string, string>
              )[j.operation] || j.operation}
            </h3>
            <small>
              {j.provider_id} · 尝试 {j.attempt} ·{" "}
              {j.elapsed_ms == null
                ? "等待结果"
                : `${(j.elapsed_ms / 1000).toFixed(1)} 秒`}
            </small>
            <progress value={j.progress} max={100} />
            {j.error && (
              <p className="job-error">
                {j.error.code} · {j.error.message}
              </p>
            )}
            {j.external_task_id && <p>远端任务 {j.external_task_id}</p>}
            {j.artifact && j.state === "completed" && (
              <a
                className="button"
                href={`/api/v1/projects/${project.project_id}/exports/${j.id}/file`}
                download
              >
                下载 {j.artifact.filename}
              </a>
            )}
            {j.remote_notice && (
              <p className="protocol-note">{j.remote_notice}</p>
            )}
            {(j.provider_id.startsWith("openai-") ||
              j.protocol === "comfyui") &&
              ["failed", "cancelled"].includes(j.state) && (
                <RemoteJobRecovery
                  job={j}
                  projectId={project.project_id}
                  busy={busy}
                  run={run}
                />
              )}
            <JobDetail job={j} projectId={project.project_id} />
          </div>
          <span className={`status status-${j.state}`}>
            {statusLabel[j.state] || j.state}
          </span>
          {["queued", "claimed", "running", "verifying", "retry_wait"].includes(
            j.state,
          ) && (
            <button
              disabled={busy}
              onClick={() =>
                void run(() =>
                  api(
                    `/projects/${project.project_id}/jobs/${j.id}/cancel`,
                    {},
                  ),
                )
              }
            >
              取消
            </button>
          )}
          {["failed", "cancelled"].includes(j.state) &&
            j.error?.code !== "PROVIDER_JOB_FAILED" && (
              <button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api(
                      `/projects/${project.project_id}/jobs/${j.id}/retry`,
                      {},
                    ),
                  )
                }
              >
                重试
              </button>
            )}
        </article>
      ))}
    </>
  );
}
