import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, mediaUrl } from "./api";
import type { ModelSettings, Project } from "./types";
import "./reviews.css";

type Report = {
  id: string;
  shot_id: string;
  asset_version_id: string;
  mode: string;
  stale: boolean;
  created_at: number;
  checks: {
    code: string;
    status: string;
    severity: string;
    description?: string;
    confidence?: number | null;
    evidence?: { frame_asset_ids?: string[]; timestamps?: number[] } | null;
  }[];
  frames: {
    asset_id: string;
    frame: number;
    timestamp_seconds: number;
    source_asset_id: string;
  }[];
};

export function ReviewPanel({
  project,
  settings,
  run,
  busy,
}: {
  project: Project;
  settings?: ModelSettings;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const available = project.shots.filter(
    (s) => s.active_asset_version_id && s.status !== "archived",
  );
  const [selected, setSelected] = useState<string[]>(
    available.length ? [available[0].id] : [],
  );
  const [mode, setMode] = useState<"deterministic" | "vision">("deterministic");
  const [consent, setConsent] = useState(false);
  const profile = settings?.providers.find((p) => p.kind === "llm");
  const vision = !!profile?.enabled && !!profile.vision_enabled;
  const reports = useQuery({
    queryKey: ["reviews", project.project_id],
    queryFn: () => api<Report[]>(`/projects/${project.project_id}/reviews`),
    refetchInterval: 1500,
  });
  const labels: Record<string, string> = {
    pass: "通过",
    warning: "需关注",
    fail: "发现问题",
    not_run: "未运行",
  };
  return (
    <section className="page compact review-page">
      <div className="section-heading">
        <div>
          <h2>审片与证据</h2>
          <p className="muted">检查结果供人工判断，不会自动重做镜头。</p>
        </div>
      </div>
      <section className="panel review-controls">
        <label>
          审片方式
          <select
            aria-label="审片方式"
            value={mode}
            onChange={(e) => {
              setMode(e.target.value as typeof mode);
              setConsent(false);
            }}
          >
            <option value="deterministic">本机媒体检查 · 离线</option>
            <option value="vision">关键帧 VLM 审片 · 外部模型</option>
          </select>
        </label>
        <p className="muted">
          离线检查核对媒体和生成关键帧；角色、动作与语义连续性会标记为未运行。VLM
          仅依据抽样画面提供证据建议。
        </p>
        <div className="review-shot-list">
          {available.map((shot) => (
            <label className="check-label" key={shot.id}>
              <input
                type="checkbox"
                checked={selected.includes(shot.id)}
                onChange={(e) =>
                  setSelected((ids) =>
                    e.target.checked
                      ? [...ids, shot.id]
                      : ids.filter((id) => id !== shot.id),
                  )
                }
              />
              {shot.order + 1} · {shot.title}
            </label>
          ))}
        </div>
        {!available.length && (
          <p className="protocol-note">先生成并选择镜头素材，再提交审片。</p>
        )}
        {mode === "vision" && (
          <>
            {!vision && (
              <p className="protocol-note">
                请在 LLM 连接高级设置中启用图像审片，并使用支持图像输入的模型。
              </p>
            )}
            <label className="check-label">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              允许发送所选镜头及相邻镜头关键帧、角色与世界状态，并承担模型费用
            </label>
          </>
        )}
        <button
          className="primary"
          disabled={
            busy ||
            !selected.length ||
            (mode === "vision" && (!vision || !consent))
          }
          onClick={() =>
            void run(async () => {
              await api(`/projects/${project.project_id}/reviews`, {
                base_revision: project.revision,
                shot_ids: selected,
                mode,
                allow_remote: mode === "vision" && consent,
              });
              setConsent(false);
            })
          }
        >
          提交审片
        </button>
      </section>
      {reports.error && <p role="alert">{reports.error.message}</p>}
      {!reports.data?.length && (
        <p className="muted">
          报告完成后会显示媒体校验、关键帧和语义检查状态。
        </p>
      )}
      {[...(reports.data || [])]
        .sort((a, b) => b.created_at - a.created_at)
        .map((report) => (
          <article className="panel review-report" key={report.id}>
            <header>
              <h3>
                {project.shots.find((s) => s.id === report.shot_id)?.title ||
                  "历史镜头"}{" "}
                · {report.mode === "vision" ? "VLM 建议" : "本机检查"}
              </h3>
              <span
                className={`status ${report.stale ? "status-stale" : "status-ready"}`}
              >
                {report.stale ? "对应旧设定，请重新检查" : "等待人工判断"}
              </span>
            </header>
            <p className="muted">
              {new Date(report.created_at * 1000).toLocaleString("zh-CN")} ·
              素材版本 {report.asset_version_id.slice(0, 8)}
            </p>
            <div className="review-evidence">
              {report.frames.map((frame, index) => (
                <a
                  key={frame.asset_id}
                  href={mediaUrl(project.project_id, frame.asset_id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    src={mediaUrl(project.project_id, frame.asset_id)}
                    alt={`审片关键帧 ${index + 1}`}
                  />
                  <span>
                    证据 {index + 1} · {frame.timestamp_seconds.toFixed(2)}s ·
                    帧 {frame.frame}
                  </span>
                </a>
              ))}
            </div>
            <div className="review-findings">
              {report.checks.map((check, index) => (
                <div className="review-finding" key={index}>
                  <strong>
                    {labels[check.status] || check.status} · {check.code}
                  </strong>
                  {check.description && <p>{check.description}</p>}
                  {typeof check.confidence === "number" && (
                    <small>
                      模型置信度 {Math.round(check.confidence * 100)}% ·
                      供人工核对
                    </small>
                  )}
                  {check.evidence?.frame_asset_ids?.length && (
                    <p className="muted">
                      关联证据{" "}
                      {check.evidence.frame_asset_ids
                        .map(
                          (id) =>
                            report.frames.findIndex((f) => f.asset_id === id) +
                            1,
                        )
                        .join("、")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </article>
        ))}
    </section>
  );
}
