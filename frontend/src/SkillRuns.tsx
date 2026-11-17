import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { statusLabel } from "./format";
import type { Project } from "./types";
import type { ProductionStage, SkillAction, SkillRun } from "./skill-types";

export function SkillRuns({
  project,
  stage,
  busy,
  perform,
}: {
  project: Project;
  stage: ProductionStage;
  busy: boolean;
  perform: SkillAction;
}) {
  const [offset, setOffset] = useState(0);
  const runs = useQuery({
    queryKey: ["skill-runs", project.project_id, stage, offset],
    queryFn: () =>
      api<{ items: SkillRun[]; total: number }>(
        `/projects/${project.project_id}/skills/runs?stage=${stage}&offset=${offset}&limit=10`,
      ),
    refetchInterval: (query) =>
      query.state.data?.items.some((job) =>
        [
          "queued",
          "claimed",
          "running",
          "verifying",
          "cancel_requested",
        ].includes(job.state),
      )
        ? 1000
        : 5000,
  });
  return (
    <section className="skill-runs" aria-label="技能运行记录">
      <h3>检查与脚本记录</h3>
      {runs.error && <p role="alert">{runs.error.message}</p>}
      {!runs.data?.items.length && (
        <p className="muted">本阶段尚无运行记录。</p>
      )}
      {runs.data?.items.map((job) => (
        <article key={job.id} className="skill-run-result">
          <header>
            <strong>
              {job.operation === "check_production" ? "制作检查" : "外部脚本"}
              {job.request?.script_path && ` · ${job.request.script_path}`}
            </strong>
            <span>
              {statusLabel[job.state] || job.state} · 来源修订{" "}
              {job.source_revision}
            </span>
          </header>
          {job.operation === "run_skill_script" && (
            <>
              <small>
                {job.request?.execution_mode === "container"
                  ? "隔离容器 · 网络关闭"
                  : "本机用户权限 · 外部调用费用由脚本管理"}
              </small>
              {job.skill_result?.actual_policy && (
                <details>
                  <summary>实际执行策略与解释器版本</summary>
                  <pre>
                    {JSON.stringify(job.skill_result.actual_policy, null, 2)}
                  </pre>
                </details>
              )}
            </>
          )}
          {job.source_revision !== project.revision && (
            <p className="muted">工程已更新，这份结果对应较早修订。</p>
          )}
          {job.error && (
            <p role="alert">
              {job.error.code} · {job.error.message}
            </p>
          )}
          {["queued", "claimed", "running", "retry_wait"].includes(
            job.state,
          ) && (
            <button
              disabled={busy}
              onClick={() =>
                void perform(() =>
                  api(
                    `/projects/${project.project_id}/jobs/${job.id}/cancel`,
                    {},
                  ),
                )
              }
            >
              取消此次运行
            </button>
          )}
          {job.skill_result?.checks && (
            <ul>
              {job.skill_result.checks.map((finding, index) => (
                <li key={`${finding.code}-${index}`}>
                  <span
                    className={
                      finding.status === "warning" ? "skill-warning" : "muted"
                    }
                  >
                    {finding.status === "warning" ? "需检查" : "已检查"}
                  </span>{" "}
                  {finding.message}
                  {finding.evidence !== null &&
                    finding.evidence !== undefined && (
                      <small>{JSON.stringify(finding.evidence)}</small>
                    )}
                </li>
              ))}
            </ul>
          )}
          {job.skill_result?.truncated && (
            <p>检查条目已达显示上限，请缩小镜头范围继续检查。</p>
          )}
          {job.skill_result?.stdout && (
            <details open>
              <summary>标准输出</summary>
              <pre>{job.skill_result.stdout}</pre>
            </details>
          )}
          {job.skill_result?.stderr && (
            <details open>
              <summary>错误输出</summary>
              <pre>{job.skill_result.stderr}</pre>
            </details>
          )}
          {job.skill_result?.logs_truncated && (
            <p className="muted">
              日志显示前64KiB，完整输出请由脚本写入结果文件。
            </p>
          )}
          {job.skill_result?.artifacts?.map((file) => (
            <a
              key={file.id}
              href={`/api/v1/projects/${project.project_id}/skills/runs/${job.id}/files/${file.id}`}
              download
            >
              下载 {file.name}
            </a>
          ))}
        </article>
      ))}
      {!!runs.data && runs.data.total > 10 && (
        <div className="button-row">
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 10))}
          >
            上一页
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 10, runs.data.total)} /{" "}
            {runs.data.total}
          </span>
          <button
            disabled={offset + 10 >= runs.data.total}
            onClick={() => setOffset(offset + 10)}
          >
            下一页
          </button>
        </div>
      )}
    </section>
  );
}
