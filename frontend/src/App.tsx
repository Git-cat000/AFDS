import { useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  Clapperboard,
  Download,
  Film,
  FolderOpen,
  Layers,
  Lock,
  Play,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  Unlock,
  X,
} from "lucide-react";
import {
  api,
  ApiError,
  mediaUrl,
  fetchEventHistory,
  type ProjectEvent,
} from "./api";
import { framesToTime, statusLabel } from "./format";
import { useStudio } from "./store";
import { ProviderSettings } from "./ProviderSettings";
import { AuxiliaryMedia } from "./AuxiliaryMedia";
import { RemoteJobRecovery } from "./RemoteJobRecovery";
import { ProjectBudget } from "./ProjectBudget";
import { ContinuityPanel } from "./ContinuityPanel";
import { ReviewPanel } from "./ReviewPanel";
import { TimelineEditor } from "./TimelineEditor";
import { BackupRestorePanel } from "./BackupRestorePanel";
import { OperationsDashboard } from "./OperationsDashboard";
import type {
  ChangeSet,
  Job,
  Plan,
  Preview,
  Project,
  Provider,
  Revision,
  Shot,
  ModelSettings,
} from "./types";

function Status({ state }: { state: string }) {
  return (
    <span className={`status status-${state}`}>
      {statusLabel[state] || state}
    </span>
  );
}

function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  return (
    <div className="modal-shade">
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
      >
        <header>
          <h2>{title}</h2>
          <button className="icon-button" onClick={close} aria-label="关闭">
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function App() {
  const { projectId, setProject } = useStudio();
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("storyboard");
  const [editing, setEditing] = useState<Shot | null>(null);
  const [previewAsset, setPreviewAsset] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [message, setMessage] = useState("用四个镜头，讲述这个故事。");
  const [llmChoice, setLlmChoice] = useState<string | null>(null);
  const [videoChoice, setVideoChoice] = useState<string | null>(null);
  const [remoteConsent, setRemoteConsent] = useState(false);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
  });
  const project = useQuery({
    queryKey: ["project", projectId],
    enabled: !!projectId,
    queryFn: () => api<Project>(`/projects/${projectId}/apr`),
    refetchInterval: 1500,
  });
  const jobs = useQuery({
    queryKey: ["jobs", projectId],
    enabled: !!projectId,
    queryFn: () => api<Job[]>(`/projects/${projectId}/jobs`),
    refetchInterval: 1000,
  });
  const plans = useQuery({
    queryKey: ["plans", projectId],
    enabled: !!projectId,
    queryFn: () => api<Plan[]>(`/projects/${projectId}/plans`),
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api<Provider[]>("/models"),
  });
  const modelSettings = useQuery({
    queryKey: ["model-settings"],
    queryFn: () => api<ModelSettings>("/settings/models"),
  });
  const llmProvider =
    llmChoice || modelSettings.data?.defaults.llm || "mock-director";
  const videoProvider =
    videoChoice || modelSettings.data?.defaults.video || "mock-video-local";
  const usesRemote =
    llmProvider.startsWith("openai-") || videoProvider.startsWith("openai-");
  const history = useQuery({
    queryKey: ["history", projectId, project.data?.revision],
    enabled: !!projectId,
    queryFn: () => api<Revision[]>(`/projects/${projectId}/revisions`),
  });
  const apr = project.data;
  const plan = plans.data
    ?.filter(
      (p) => p.state === "proposed" && (!p.type || p.type === "storyboard"),
    )
    .at(-1);
  const activeJobs =
    jobs.data?.filter((j) =>
      ["queued", "claimed", "running", "verifying", "retry_wait"].includes(
        j.state,
      ),
    ) || [];

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : "操作失败";
      setError(
        cause instanceof ApiError && cause.code === "REVISION_CONFLICT"
          ? `项目已有新版本。请关闭编辑窗口，刷新后重新预览修改。${JSON.stringify(cause.details)}`
          : text,
      );
    } finally {
      setBusy(false);
      await queryClient.invalidateQueries();
    }
  }

  function selectProject(id: string | null) {
    setProject(id);
    setError("");
    setEditing(null);
    setTab("storyboard");
    setRemoteConsent(false);
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    await run(async () => {
      const created = await api<Project>("/projects", {
        title: fields.get("title"),
        brief: fields.get("brief"),
      });
      selectProject(created.project_id);
    });
  }

  function generate(ids: string[]) {
    if (!apr) return;
    void run(() =>
      api(`/projects/${projectId}/generate`, {
        base_revision: apr.revision,
        shot_ids: ids,
        provider_id: videoProvider,
        allow_remote: remoteConsent,
      }),
    );
  }

  function reorder(index: number, direction: number) {
    if (!apr) return;
    const ids = apr.timeline.tracks[0].items.map((item) => item.shot_id);
    [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
    void run(() =>
      api(`/projects/${projectId}/timeline`, {
        base_revision: apr.revision,
        shot_ids: ids,
      }),
    );
  }

  const visibleError =
    error || project.error?.message || projects.error?.message || "";
  return (
    <div className="app-shell">
      <aside className="rail">
        <button
          className="brand"
          onClick={() => selectProject(null)}
          aria-label="返回项目列表"
        >
          <Clapperboard />
          <span>AFDS</span>
        </button>
        <span className="rail-label">WORKSPACE</span>
        <button
          className={!projectId ? "nav active" : "nav"}
          onClick={() => selectProject(null)}
        >
          <FolderOpen size={18} />
          项目
        </button>
        {projectId && (
          <>
            <button
              className={`nav ${tab === "storyboard" ? "active" : ""}`}
              onClick={() => setTab("storyboard")}
            >
              <Film size={18} />
              分镜工作室
            </button>
            <button
              className={`nav ${tab === "continuity" ? "active" : ""}`}
              onClick={() => setTab("continuity")}
            >
              <Clapperboard size={18} />
              角色与连续性
            </button>
            <button
              className={`nav ${tab === "review" ? "active" : ""}`}
              onClick={() => setTab("review")}
            >
              <Check size={18} />
              审片与证据
            </button>
            <button
              className={`nav ${tab === "timeline" ? "active" : ""}`}
              onClick={() => setTab("timeline")}
            >
              <Layers size={18} />
              音画时间轴
            </button>
            <button
              className={`nav ${tab === "assets" ? "active" : ""}`}
              onClick={() => setTab("assets")}
            >
              <Layers size={18} />
              素材库
            </button>
            <button
              className={`nav ${tab === "jobs" ? "active" : ""}`}
              onClick={() => setTab("jobs")}
            >
              <RefreshCw size={18} />
              任务队列<span className="count">{activeJobs.length}</span>
            </button>
          </>
        )}
        <button
          className={`nav ${tab === "operations" ? "active" : ""}`}
          onClick={() => setTab("operations")}
        >
          <RefreshCw size={18} />
          运行概览
        </button>
        <button
          className={`nav ${tab === "providers" ? "active" : ""}`}
          onClick={() => setTab("providers")}
        >
          <Settings2 size={18} />
          模型与工具
        </button>
        <div className="rail-bottom">
          <span className="online-dot" />
          本地工作区<small>v0.5 · FILM STUDIO</small>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <span>/</span> {apr?.title || "项目总览"}
          </div>
          <div className="topbar-actions">
            <span className="offline">
              {usesRemote ? "已选择外部模型" : "离线 Mock 工作室"}
            </span>
            <button
              className="icon-button"
              aria-label="刷新项目"
              onClick={() => void queryClient.invalidateQueries()}
            >
              <RefreshCw size={16} />
            </button>
          </div>
        </header>
        {visibleError && (
          <div role="alert" className="error-banner">
            <strong>操作未完成</strong>
            <span>{visibleError}</span>
            <button
              onClick={() => {
                setError("");
                void queryClient.invalidateQueries();
              }}
            >
              刷新数据
            </button>
          </div>
        )}
        {tab === "providers" ? (
          <ProviderSettings />
        ) : tab === "operations" ? (
          <OperationsDashboard projectId={projectId || undefined} />
        ) : !projectId ? (
          <section className="page dashboard">
            <p className="eyebrow">YOUR NEXT FILM STARTS HERE</p>
            <h1>
              把一个念头，
              <br />
              <em>变成一组镜头。</em>
            </h1>
            <div className="dashboard-grid">
              <div className="project-start">
                <form className="panel create-project" onSubmit={create}>
                  <h2>开始新的创作</h2>
                  <label>
                    项目名称
                    <input
                      aria-label="项目名称"
                      name="title"
                      required
                      maxLength={200}
                      placeholder="例如，雨夜告别"
                    />
                  </label>
                  <label>
                    创作简述
                    <textarea
                      aria-label="创作简述"
                      name="brief"
                      rows={5}
                      maxLength={10000}
                      placeholder="故事发生在哪里？谁在场？你希望观众感受到什么？"
                    />
                  </label>
                  <div className="format-note">
                    <Film size={16} />
                    1280 × 720 · 24 fps · 四镜头演示
                  </div>
                  <button className="primary" disabled={busy}>
                    创建项目 <ArrowRight size={16} />
                  </button>
                </form>
                <BackupRestorePanel
                  onQueued={(job) => {
                    selectProject(job.project_id);
                    setTab("jobs");
                    void queryClient.invalidateQueries();
                  }}
                />
              </div>
              <section className="recent">
                <h2>最近的项目</h2>
                {projects.isLoading && (
                  <p className="muted">正在读取本地项目…</p>
                )}
                {projects.data?.length === 0 && (
                  <div className="empty-small">
                    <FolderOpen size={32} />
                    <p>第一个故事，从这里开始。</p>
                  </div>
                )}
                {projects.data?.map((p) => (
                  <button
                    className="project-row"
                    key={p.project_id}
                    onClick={() => selectProject(p.project_id)}
                  >
                    <div className="project-icon">
                      <Film size={24} />
                    </div>
                    <div>
                      <strong>{p.title}</strong>
                      <small>
                        修订 {p.revision} · {p.format.width} × {p.format.height}
                      </small>
                    </div>
                    <ArrowRight size={18} />
                  </button>
                ))}
              </section>
            </div>
          </section>
        ) : !apr ? (
          <section className="page">
            <p>正在打开项目…</p>
            <button onClick={() => selectProject(null)}>
              <ArrowLeft size={16} />
              返回项目
            </button>
          </section>
        ) : (
          <>
            <section className="studio-heading">
              <div>
                <p className="eyebrow">FILM PROJECT / MOCK PRODUCTION</p>
                <h1>{apr.title}</h1>
                <p className="muted">
                  {apr.format.width} × {apr.format.height} <span>·</span>{" "}
                  {apr.format.fps_num / apr.format.fps_den} fps <span>·</span>{" "}
                  修订 {apr.revision}
                </p>
              </div>
              <div className="heading-actions">
                <button onClick={() => setSettingsOpen(true)}>项目设定</button>
                <button onClick={() => setHistoryOpen(true)}>版本记录</button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`/projects/${projectId}/exports`, {
                        base_revision: apr.revision,
                        format: "zip",
                      });
                      setTab("jobs");
                    })
                  }
                >
                  导出工程包
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(`/projects/${projectId}/exports`, {
                        base_revision: apr.revision,
                        format: "otio",
                      });
                      setTab("jobs");
                    })
                  }
                >
                  导出 OTIO
                </button>
                <button
                  className="primary"
                  disabled={busy || !apr.shots.length || activeJobs.length > 0}
                  onClick={() =>
                    void run(() =>
                      api(`/projects/${projectId}/render`, {
                        base_revision: apr.revision,
                      }),
                    )
                  }
                >
                  <Play size={15} />
                  渲染成片
                </button>
              </div>
            </section>
            {tab === "storyboard" && (
              <section className="studio-models">
                <label>
                  导演 LLM
                  <select
                    aria-label="导演 LLM"
                    value={llmProvider}
                    onChange={(e) => {
                      setLlmChoice(e.target.value);
                      setRemoteConsent(false);
                    }}
                  >
                    {providers.data
                      ?.filter((p) => p.capabilities.includes("storyboard"))
                      .map((p) => (
                        <option
                          key={p.provider_id}
                          value={p.provider_id}
                          disabled={
                            !["available", "unverified"].includes(
                              p.availability,
                            )
                          }
                        >
                          {p.display_name}
                          {p.model_id ? ` · ${p.model_id}` : ""}
                          {p.availability === "unverified"
                            ? "（待生成验证）"
                            : p.availability !== "available"
                              ? "（未启用）"
                              : ""}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  视频模型
                  <select
                    aria-label="视频模型"
                    value={videoProvider}
                    onChange={(e) => {
                      setVideoChoice(e.target.value);
                      setRemoteConsent(false);
                    }}
                  >
                    {providers.data
                      ?.filter((p) =>
                        [
                          ...p.capabilities,
                          ...(p.provisional_capabilities || []),
                        ].includes("text_to_video"),
                      )
                      .map((p) => (
                        <option
                          key={p.provider_id}
                          value={p.provider_id}
                          disabled={
                            !["available", "unverified"].includes(
                              p.availability,
                            )
                          }
                        >
                          {p.display_name}
                          {p.model_id ? ` · ${p.model_id}` : ""}
                          {p.availability === "unverified"
                            ? "（待生成验证）"
                            : p.availability !== "available"
                              ? "（未启用）"
                              : ""}
                        </option>
                      ))}
                  </select>
                </label>
                {usesRemote && (
                  <label className="check-label remote-consent">
                    <input
                      type="checkbox"
                      checked={remoteConsent}
                      onChange={(e) => setRemoteConsent(e.target.checked)}
                    />
                    允许发送创作内容并承担服务商费用（金额未知）
                  </label>
                )}
              </section>
            )}
            {tab === "storyboard" && (
              <div className="studio-layout">
                <aside className="director panel">
                  <header>
                    <div className="director-icon">
                      <Clapperboard size={18} />
                    </div>
                    <div>
                      <h2>导演助手</h2>
                      <small>
                        {llmProvider === "mock-director"
                          ? "Mock Director · 离线"
                          : `外部 LLM · ${modelSettings.data?.providers.find((p) => p.kind === "llm")?.model || ""}`}
                      </small>
                    </div>
                  </header>
                  <div className="director-message">
                    <p>先从故事出发。</p>
                    <p>
                      我会根据创作简述生成四个镜头的结构化建议。你可以审阅并修改，再开始制作。
                    </p>
                  </div>
                  <div className="brief-card">
                    <span>创作简述</span>
                    <p>{apr.brief || "尚未填写。可以在项目设定中补充。"}</p>
                  </div>
                  <label className="director-input">
                    导演指令
                    <textarea
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                      rows={3}
                      maxLength={5000}
                    />
                  </label>
                  <button
                    className="primary"
                    disabled={
                      busy ||
                      !message.trim() ||
                      (llmProvider === "openai-llm" && !remoteConsent)
                    }
                    onClick={() =>
                      void run(() =>
                        api(`/projects/${projectId}/director`, {
                          base_revision: apr.revision,
                          message,
                          provider_id: llmProvider,
                          allow_remote: remoteConsent,
                        }),
                      )
                    }
                  >
                    <Send size={15} />
                    生成分镜建议
                  </button>
                  {plan && (
                    <PlanProposal
                      key={plan.id}
                      plan={plan}
                      disabled={busy}
                      approve={(edited) =>
                        void run(async () => {
                          await api(
                            `/projects/${projectId}/plans/${plan.id}`,
                            { base_revision: apr.revision, shots: edited },
                            "PATCH",
                          );
                          await api(
                            `/projects/${projectId}/plans/${plan.id}/execute`,
                            { base_revision: apr.revision, approved: true },
                          );
                        })
                      }
                    />
                  )}
                </aside>
                <section className="storyboard">
                  <div className="section-heading">
                    <div>
                      <h2>
                        故事分镜{" "}
                        <span>
                          {
                            apr.shots.filter((s) => s.status !== "archived")
                              .length
                          }
                        </span>
                      </h2>
                      <p className="muted">选择镜头，逐一雕琢。</p>
                    </div>
                    <button
                      disabled={
                        busy ||
                        !apr.shots.length ||
                        (videoProvider === "openai-video" && !remoteConsent)
                      }
                      onClick={() =>
                        generate(
                          apr.shots
                            .filter((s) => !s.locked && s.status !== "archived")
                            .map((s) => s.id),
                        )
                      }
                    >
                      <Plus size={16} />
                      生成全部镜头
                    </button>
                  </div>
                  {!apr.shots.length && (
                    <div className="story-empty">
                      <Clapperboard size={46} strokeWidth={1} />
                      <h2>故事正在等待第一帧</h2>
                      <p>在左侧生成分镜建议，审阅后加入项目。</p>
                    </div>
                  )}
                  <div className="shot-grid">
                    {apr.shots
                      .filter((s) => s.status !== "archived")
                      .map((shot) => {
                        const version = apr.asset_versions.find(
                          (v) => v.id === shot.active_asset_version_id,
                        );
                        const currentJob = activeJobs.find(
                          (j) => j.target_entity_id === shot.id,
                        );
                        return (
                          <article
                            className="shot-card"
                            data-testid="shot-card"
                            key={shot.id}
                          >
                            <button
                              className={`shot-visual visual-${shot.order % 4}`}
                              aria-label={`预览 ${shot.title}`}
                              disabled={!version}
                              onClick={() => setPreviewAsset(version!.asset_id)}
                            >
                              {version ? (
                                <img
                                  src={mediaUrl(
                                    apr.project_id,
                                    version.poster_asset_id,
                                  )}
                                  alt={`${shot.title} 素材预览`}
                                />
                              ) : (
                                <div className="shot-placeholder">
                                  <Film size={30} strokeWidth={1} />
                                  <span>等待生成素材</span>
                                </div>
                              )}
                              <span className="shot-number">
                                SHOT {String(shot.order + 1).padStart(2, "0")}
                              </span>
                              <span className="duration">
                                {framesToTime(shot.duration_frames, apr.format)}
                              </span>
                            </button>
                            <div className="shot-info">
                              <div className="shot-title">
                                <h3>{shot.title}</h3>
                                {shot.locked && (
                                  <Lock size={14} aria-label="已锁定" />
                                )}
                              </div>
                              <Status
                                state={currentJob?.state || shot.status}
                              />
                              <p>{shot.action}</p>
                              <footer>
                                <span>
                                  {shot.camera.shot_size} ·{" "}
                                  {shot.camera.movement}
                                </span>
                                <button
                                  className="text-button"
                                  aria-label="编辑镜头"
                                  onClick={() => setEditing(shot)}
                                >
                                  编辑 <ArrowRight size={14} />
                                </button>
                              </footer>
                            </div>
                          </article>
                        );
                      })}
                  </div>
                </section>
              </div>
            )}
            {tab === "continuity" && (
              <ContinuityPanel
                key={apr.project_id}
                project={apr}
                run={run}
                busy={busy}
              />
            )}
            {tab === "timeline" && (
              <TimelineEditor
                key={`${apr.project_id}:${apr.revision}`}
                project={apr}
                run={run}
                busy={busy}
              />
            )}
            {tab === "review" && (
              <ReviewPanel
                key={apr.project_id}
                project={apr}
                settings={modelSettings.data}
                run={run}
                busy={busy}
              />
            )}
            {tab === "jobs" && (
              <section className="page compact">
                <div className="section-heading">
                  <h2>任务与执行记录</h2>
                  <span className="muted">{activeJobs.length} 个活动任务</span>
                </div>
                <ProjectBudget
                  projectId={apr.project_id}
                  run={run}
                  busy={busy}
                />
                {!jobs.data?.length && (
                  <p className="muted">生成镜头后，执行记录会出现在这里。</p>
                )}
                {jobs.data?.map((j) => (
                  <article className="job-row panel" key={j.id}>
                    <div>
                      <h3>
                        {j.operation === "generate_video"
                          ? `镜头 ${(apr.shots.find((s) => s.id === j.target_entity_id)?.order ?? 0) + 1} · ${j.provider_id === "mock-video-local" ? "生成测试视频" : "生成视频"}`
                          : {
                              generate_image: "生成参考图像",
                              generate_speech: "合成语音旁白",
                              import_audio: "导入音频素材",
                              review_shot: "镜头审片与证据",
                              export_project: "导出工程",
                              restore_project: "恢复工程副本",
                            }[j.operation] || "时间轴 · 渲染成片"}
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
                      {j.external_task_id && (
                        <p className="muted">远端任务 {j.external_task_id}</p>
                      )}
                      {j.artifact && j.state === "completed" && (
                        <a
                          className="button"
                          href={`/api/v1/projects/${apr.project_id}/exports/${j.id}/file`}
                          download
                        >
                          <Download size={15} />
                          下载 {j.artifact.filename}
                        </a>
                      )}
                      {j.remote_notice && (
                        <p className="protocol-note">{j.remote_notice}</p>
                      )}
                      {j.provider_id.startsWith("openai-") &&
                        ["failed", "cancelled"].includes(j.state) && (
                          <RemoteJobRecovery
                            job={j}
                            projectId={apr.project_id}
                            busy={busy}
                            run={run}
                          />
                        )}
                      <details>
                        <summary>请求与溯源</summary>
                        <pre>{JSON.stringify(j.request, null, 2)}</pre>
                        <small>输入哈希 {j.input_summary.request_hash}</small>
                      </details>
                    </div>
                    <Status state={j.state} />
                    {[
                      "queued",
                      "claimed",
                      "running",
                      "verifying",
                      "retry_wait",
                    ].includes(j.state) && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(() =>
                            api(
                              `/projects/${projectId}/jobs/${j.id}/cancel`,
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
                                `/projects/${projectId}/jobs/${j.id}/retry`,
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
                <EventLog projectId={apr.project_id} />
              </section>
            )}
            {tab === "assets" && (
              <section className="page compact">
                <h2>素材与候选版本</h2>
                <div className="asset-grid">
                  {apr.asset_versions.map((v, index) => (
                    <article className="panel asset-card" key={v.id}>
                      <img
                        src={mediaUrl(apr.project_id, v.poster_asset_id)}
                        alt="镜头素材预览"
                      />
                      <h3>
                        镜头{" "}
                        {(apr.shots.find((s) => s.id === v.shot_id)?.order ??
                          0) + 1}{" "}
                        · 候选 {index + 1}
                      </h3>
                      <Status state={v.status} />
                      <button onClick={() => setPreviewAsset(v.asset_id)}>
                        <Play size={15} />
                        播放素材
                      </button>
                      <button
                        disabled={busy || v.status === "stale"}
                        onClick={() =>
                          setEditing(apr.shots.find((s) => s.id === v.shot_id)!)
                        }
                      >
                        选择版本
                      </button>
                    </article>
                  ))}
                </div>
                {!apr.asset_versions.length && (
                  <p className="muted">生成镜头后在这里查看全部素材版本。</p>
                )}
                <AuxiliaryMedia
                  project={apr}
                  settings={modelSettings.data}
                  busy={busy}
                  run={run}
                  preview={setPreviewAsset}
                />
              </section>
            )}
            <section className="timeline-panel">
              <header>
                <div>
                  <Layers size={16} />
                  <h2>时间轴</h2>
                  <span className="muted">V1 · 单轨视频</span>
                </div>
                <strong>
                  {framesToTime(
                    apr.timeline.tracks[0].items.reduce(
                      (n, i) => n + i.duration_frames,
                      0,
                    ),
                    apr.format,
                  )}
                </strong>
              </header>
              <div className="timeline-track">
                <span className="track-label">V1</span>
                {apr.timeline.tracks[0].items.map((item, index, items) => {
                  const shot = apr.shots.find((s) => s.id === item.shot_id)!;
                  return (
                    <div
                      className={`timeline-clip visual-${shot.order % 4}`}
                      key={`${item.shot_id}-${index}`}
                      style={{ flexGrow: item.duration_frames }}
                    >
                      <span>
                        {String(shot.order + 1).padStart(2, "0")} {shot.title}
                      </span>
                      <small>
                        {framesToTime(item.duration_frames, apr.format)}
                      </small>
                      <div>
                        <button
                          aria-label={`前移镜头 ${shot.order + 1}`}
                          disabled={busy || index === 0}
                          onClick={() => reorder(index, -1)}
                        >
                          <ArrowUp size={12} />
                        </button>
                        <button
                          aria-label={`后移镜头 ${shot.order + 1}`}
                          disabled={busy || index === items.length - 1}
                          onClick={() => reorder(index, 1)}
                        >
                          <ArrowDown size={12} />
                        </button>
                      </div>
                    </div>
                  );
                })}
                {!apr.shots.length && (
                  <span className="muted">分镜获批后自动建立时间轴</span>
                )}
              </div>
            </section>
            <footer className="delivery">
              <div>
                <span className="online-dot" />
                <span>项目已保存至本地</span>
                <Status state={apr.render_status} />
              </div>
              <div>
                <a
                  href={`/api/v1/projects/${projectId}/export`}
                  target="_blank"
                  rel="noreferrer"
                >
                  导出 APR
                </a>
                <a
                  href={`/api/v1/projects/${projectId}/manifest`}
                  target="_blank"
                  rel="noreferrer"
                >
                  素材清单
                </a>
                {apr.render_asset_id && (
                  <>
                    <button
                      onClick={() => setPreviewAsset(apr.render_asset_id)}
                    >
                      <Play size={14} />
                      预览成片
                    </button>
                    {apr.render_status === "ready" && (
                      <a
                        className="download"
                        href={mediaUrl(
                          apr.project_id,
                          apr.render_asset_id,
                          true,
                        )}
                        download
                      >
                        <Download size={15} />
                        下载 MP4
                      </a>
                    )}
                  </>
                )}
              </div>
            </footer>
          </>
        )}
      </main>
      {editing && apr && (
        <ShotEditor
          key={editing.id}
          shot={editing}
          project={apr}
          busy={busy}
          close={() => setEditing(null)}
          run={run}
          generate={() => generate([editing.id])}
        />
      )}
      {previewAsset && apr && (
        <Modal
          title={
            apr.assets.find((a) => a.id === previewAsset)?.provenance.is_mock ||
            apr.assets.find((a) => a.id === previewAsset)?.provenance
              .contains_mock
              ? "媒体预览 · DEMO / MOCK"
              : "媒体预览"
          }
          close={() => setPreviewAsset(null)}
        >
          {apr.assets.find((a) => a.id === previewAsset)?.media_type ===
          "image" ? (
            <img
              className="image-player"
              src={mediaUrl(apr.project_id, previewAsset)}
              alt="生成的图像素材"
            />
          ) : apr.assets.find((a) => a.id === previewAsset)?.media_type ===
            "audio" ? (
            <audio
              className="audio-player"
              controls
              autoPlay
              src={mediaUrl(apr.project_id, previewAsset)}
            />
          ) : (
            <video
              className="media-player"
              controls
              autoPlay
              src={mediaUrl(apr.project_id, previewAsset)}
              onError={() =>
                setError("媒体无法播放，请检查文件是否存在或查看任务错误。")
              }
            />
          )}
          <p className="muted">
            {apr.assets.find((a) => a.id === previewAsset)?.provenance
              .is_mock ||
            apr.assets.find((a) => a.id === previewAsset)?.provenance
              .contains_mock
              ? "本片由本地测试画面组成。"
              : `来源 ${apr.assets.find((a) => a.id === previewAsset)?.provenance.provider_id || ""}`}
          </p>
        </Modal>
      )}
      {settingsOpen && apr && (
        <ProjectSettings
          project={apr}
          close={() => setSettingsOpen(false)}
          run={run}
        />
      )}
      {historyOpen && apr && (
        <Modal title="项目版本记录" close={() => setHistoryOpen(false)}>
          {history.data?.map((r) => (
            <div className="history-row" key={r.revision}>
              <strong>r{r.revision}</strong>
              <div>
                {r.reason}
                <small>
                  {r.author} · {new Date(r.created_at * 1000).toLocaleString()}
                </small>
              </div>
              {r.revision === apr.revision && r.revision > 1 && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api(
                        `/projects/${projectId}/revisions/${r.revision}/revert`,
                        { base_revision: apr.revision, approved: true },
                      );
                      setHistoryOpen(false);
                    })
                  }
                >
                  批准回滚此版本
                </button>
              )}
            </div>
          ))}
        </Modal>
      )}
    </div>
  );
}

function PlanProposal({
  plan,
  disabled,
  approve,
}: {
  plan: Plan;
  disabled: boolean;
  approve: (shots: Shot[]) => void;
}) {
  const [shots, setShots] = useState(plan.shots);
  return (
    <div className="proposal">
      <h3>分镜建议</h3>
      <small>
        {shots.length} 个镜头 ·{" "}
        {plan.estimated_cost?.status === "unknown" ? "费用未知" : "$0 · Mock"}
      </small>
      {shots.map((shot, index) => (
        <label key={shot.id}>
          {index + 1}. {shot.title}
          <textarea
            aria-label={`建议镜头 ${index + 1}`}
            rows={3}
            value={shot.action}
            onChange={(e) =>
              setShots(
                shots.map((s, i) =>
                  i === index ? { ...s, action: e.target.value } : s,
                ),
              )
            }
          />
        </label>
      ))}
      <button
        className="primary"
        disabled={disabled}
        onClick={() => approve(shots)}
      >
        <Check size={15} />
        批准并加入分镜
      </button>
    </div>
  );
}

type Run = (action: () => Promise<unknown>) => Promise<void>;

function ShotEditor({
  shot,
  project,
  close,
  run,
  busy,
  generate,
}: {
  shot: Shot;
  project: Project;
  close: () => void;
  run: Run;
  busy: boolean;
  generate: () => void;
}) {
  const [baseRevision] = useState(project.revision);
  const [title, setTitle] = useState(shot.title);
  const [action, setAction] = useState(shot.action);
  const [frames, setFrames] = useState(shot.duration_frames);
  const [size, setSize] = useState(shot.camera.shot_size);
  const [locked, setLocked] = useState(shot.locked);
  const [approved, setApproved] = useState(false);
  const [version, setVersion] = useState(shot.active_asset_version_id || "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [change, setChange] = useState<ChangeSet | null>(null);
  const candidates = project.asset_versions.filter(
    (v) => v.shot_id === shot.id,
  );
  const path = `/projects/${project.project_id}`;
  function previewChanges() {
    const fields: [string, unknown, unknown][] = [
      ["title", title, shot.title],
      ["action", action, shot.action],
      ["duration_frames", frames, shot.duration_frames],
      ["camera.shot_size", size, shot.camera.shot_size],
      ["locked", locked, shot.locked],
      [
        "active_asset_version_id",
        version || null,
        shot.active_asset_version_id,
      ],
    ];
    const operations = fields
      .filter(([, value, old]) => value !== old)
      .map(([field, value]) => ({
        op: "set" as const,
        entity_type: "shot" as const,
        entity_id: shot.id,
        field,
        value,
      }));
    if (!operations.length) {
      close();
      return;
    }
    const body = {
      base_revision: baseRevision,
      approved,
      reason: "Studio shot edit",
      operations,
    };
    void run(async () => {
      const result = await api<Preview>(`${path}/changesets/preview`, body);
      setPreview(result);
      setChange(body);
    });
  }
  return (
    <Modal title={`镜头 ${shot.order + 1} · 属性与版本`} close={close}>
      {preview && change ? (
        <div className="change-preview">
          <h3>确认修改影响</h3>
          {preview.changes.map((c) => (
            <div className="diff" key={c.field}>
              <strong>{c.field}</strong>
              <del>{String(c.before)}</del>
              <ins>{String(c.after)}</ins>
            </div>
          ))}
          <p>
            受影响镜头 {preview.affected_shot_ids.length} 个。
            {preview.render_invalidated
              ? "成片需要重新渲染。"
              : "成片保持可用。"}
          </p>
          {!!preview.protected_shot_ids?.length && (
            <p className="protocol-note">
              其中 {preview.protected_shot_ids.length}{" "}
              个保护镜头会保留选中素材并标记待审。
            </p>
          )}
          <div className="button-row">
            <button onClick={() => setPreview(null)}>返回编辑</button>
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api(`${path}/changesets/commit`, change);
                  close();
                })
              }
            >
              确认提交修改
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="editor-grid">
            <label>
              镜头名称
              <input
                value={title}
                maxLength={200}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              景别
              <select value={size} onChange={(e) => setSize(e.target.value)}>
                <option value="wide">全景</option>
                <option value="medium">中景</option>
                <option value="close_up">近景</option>
                <option value="detail">特写</option>
              </select>
            </label>
          </div>
          <label>
            动作描述
            <textarea
              aria-label="动作描述"
              rows={4}
              value={action}
              maxLength={5000}
              onChange={(e) => setAction(e.target.value)}
            />
          </label>
          <label>
            时长（帧）
            <input
              type="number"
              min={1}
              max={1800}
              value={frames}
              onChange={(e) => setFrames(Number(e.target.value))}
            />
            <small>{framesToTime(frames, project.format)}</small>
          </label>
          {candidates.length > 0 && (
            <label>
              选用素材版本
              <select
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              >
                {!version && <option value="">请选择</option>}
                {candidates.map((v, i) => (
                  <option
                    value={v.id}
                    key={v.id}
                    disabled={v.status === "stale"}
                  >
                    候选 {i + 1} · {statusLabel[v.status]}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="check-label">
            <input
              type="checkbox"
              checked={locked}
              onChange={(e) => setLocked(e.target.checked)}
            />
            {locked ? <Lock size={14} /> : <Unlock size={14} />}锁定镜头
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={approved}
              onChange={(e) => setApproved(e.target.checked)}
            />
            授权本次修改已批准镜头或替换选中素材
          </label>
          <div className="button-row">
            <button disabled={busy || shot.locked} onClick={generate}>
              生成此镜头
            </button>
            <button
              className="primary"
              disabled={busy || !title.trim() || frames < 1}
              onClick={previewChanges}
            >
              预览修改影响
            </button>
          </div>
          {shot.active_asset_version_id && shot.status === "ready" && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api(
                    `${path}/shots/${shot.id}`,
                    {
                      base_revision: baseRevision,
                      approved: true,
                      patch: { status: "approved" },
                    },
                    "PATCH",
                  );
                  close();
                })
              }
            >
              批准当前素材
            </button>
          )}
        </>
      )}
    </Modal>
  );
}

function ProjectSettings({
  project,
  close,
  run,
}: {
  project: Project;
  close: () => void;
  run: Run;
}) {
  const [baseRevision] = useState(project.revision);
  const [title, setTitle] = useState(project.title);
  const [brief, setBrief] = useState(project.brief);
  const [style, setStyle] = useState(
    String(project.style_bible.visual_style || ""),
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [body, setBody] = useState<ChangeSet | null>(null);
  return (
    <Modal title="项目设定" close={close}>
      {preview && body ? (
        <>
          <p>
            此修改影响 {preview.affected_shot_ids.length}{" "}
            个镜头，成片状态将按依赖更新。
          </p>
          <button
            className="primary"
            onClick={() =>
              void run(async () => {
                await api(
                  `/projects/${project.project_id}/changesets/commit`,
                  body,
                );
                close();
              })
            }
          >
            批准并保存设定
          </button>
        </>
      ) : (
        <>
          <label>
            项目名称
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            创作简述
            <textarea
              value={brief}
              rows={5}
              onChange={(e) => setBrief(e.target.value)}
            />
          </label>
          <label>
            视觉风格
            <input value={style} onChange={(e) => setStyle(e.target.value)} />
          </label>
          <button
            className="primary"
            onClick={() =>
              void run(async () => {
                const fields: [string, unknown][] = [
                  ["title", title],
                  ["brief", brief],
                ];
                if (style !== String(project.style_bible.visual_style || ""))
                  fields.push([
                    "style_bible",
                    { ...project.style_bible, visual_style: style },
                  ]);
                const changes: ChangeSet = {
                  base_revision: baseRevision,
                  approved: true,
                  reason: "Studio project settings",
                  operations: fields.map(([field, value]) => ({
                    op: "set",
                    entity_type: "project",
                    entity_id: project.project_id,
                    field,
                    value,
                  })),
                };
                setPreview(
                  await api<Preview>(
                    `/projects/${project.project_id}/changesets/preview`,
                    changes,
                  ),
                );
                setBody(changes);
              })
            }
          >
            预览设定修改
          </button>
        </>
      )}
    </Modal>
  );
}

function EventLog({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const events = useQuery({
    queryKey: ["events", projectId],
    queryFn: async () => {
      const previous =
        client.getQueryData<ProjectEvent[]>(["events", projectId]) || [];
      const newer = await fetchEventHistory(
        projectId,
        previous.at(-1)?.event_id || 0,
      );
      return [...previous, ...newer].slice(-500);
    },
    refetchInterval: 2000,
  });
  return (
    <details className="panel event-log">
      <summary>最近 500 条持久化事件</summary>
      {events.data?.map((event) => (
        <div key={event.event_id}>
          <small>
            #{event.event_id} ·{" "}
            {new Date(event.timestamp * 1000).toLocaleTimeString()}
          </small>{" "}
          {event.type}
        </div>
      ))}
    </details>
  );
}
