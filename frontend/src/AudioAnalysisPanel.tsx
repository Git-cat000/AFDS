import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Connection, ModelSettings } from "./types";
import type { Composition, EditorProject } from "./TimelineEditor";

type AnalysisJob = {
  id: string;
  state: string;
  operation?: string;
  source_revision: number;
  error?: { message: string } | null;
};
type AnalysisResult = {
  kind: "beats" | "transcription";
  source_sha256?: string;
  frames?: number[];
  bpm?: number | null;
  confidence?: number | null;
  segments?: { start_ms: number; end_ms: number; text: string }[];
};
type ApplyBody = {
  base_revision: number;
  selected_indices: number[];
  target_start_frame: number;
  replace_caption_ids: string[];
};
type ApplyPreview = {
  preview_token: string;
  add_captions: {
    text: string;
    start_frame: number;
    duration_frames: number;
  }[];
  add_markers: { frame: number }[];
  remove_captions: { id: string; text: string }[];
};
const activeStates = [
  "queued",
  "claimed",
  "running",
  "verifying",
  "retry_wait",
  "cancel_requested",
];

export function AudioAnalysisPanel({
  project,
  run,
  busy,
}: {
  project: EditorProject;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const audio = project.assets.filter((asset) => asset.media_type === "audio");
  const fps = project.format.fps_num / project.format.fps_den;
  const [assetId, setAssetId] = useState(audio[0]?.id || "");
  const selectedAsset = audio.find((asset) => asset.id === assetId) || audio[0];
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(
    Math.max(
      1,
      Math.min(
        Math.floor((audio[0]?.duration_seconds || 1) * fps),
        Math.floor(1800 * fps),
      ),
    ),
  );
  const [language, setLanguage] = useState("");
  const [profiles, setProfiles] = useState<Connection[]>([]);
  const [providerId, setProviderId] = useState("");
  const profile =
    profiles.find((item) => item.provider_id === providerId) || profiles[0];
  const [consent, setConsent] = useState(false);
  const [history, setHistory] = useState<AnalysisJob[]>([]);
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [jobId, setJobId] = useState("");
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [target, setTarget] = useState(0);
  const [replaceIds, setReplaceIds] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [proposal, setProposal] = useState<{
    body: ApplyBody;
    preview: ApplyPreview;
  } | null>(null);
  const pending = useRef<{ key: string; id: string; revision: number } | null>(
    null,
  );
  const working = !!job && activeStates.includes(job.state);
  const captions = (project.timeline as Composition).captions || [];
  useEffect(() => {
    let active = true;
    void Promise.all([
      api<ModelSettings>("/settings/models"),
      api<AnalysisJob[]>(
        `/projects/${project.project_id}/timeline/analysis?limit=20`,
      ),
    ])
      .then(([settings, jobs]) => {
        if (!active) return;
        setProfiles(
          settings.providers.filter(
            (item) => item.kind === "transcription" && item.enabled,
          ),
        );
        setHistory(jobs);
      })
      .catch(() => {
        if (active) setError("暂时无法读取连接或分析记录，可重新读取。");
      });
    return () => {
      active = false;
    };
  }, [project.project_id, retry]);
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<AnalysisJob>(
          `/projects/${project.project_id}/jobs/${jobId}`,
        );
        if (!active) return;
        setJob(value);
        if (value.state === "completed") {
          const response = await api<{ result: AnalysisResult }>(
            `/projects/${project.project_id}/timeline/analysis/${jobId}`,
          );
          if (active) {
            setResult(response.result);
            setError("");
          }
        } else if (activeStates.includes(value.state))
          timer = setTimeout(() => void poll(), 1000);
      } catch {
        if (active)
          setError("分析任务暂时无法读取，可重新读取或在任务队列中查看。");
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [project.project_id, jobId, retry]);

  async function enqueue(kind: "beats" | "transcription") {
    const body = {
      base_revision: project.revision,
      kind,
      asset_id: selectedAsset?.id,
      source_start_frame: start,
      duration_frames: duration,
      language: kind === "transcription" ? language || null : null,
      provider_id: kind === "transcription" ? profile?.provider_id : null,
      allow_remote: kind === "transcription" && consent,
    };
    const key = JSON.stringify({ ...body, base_revision: 0 });
    if (pending.current?.key !== key)
      pending.current = {
        key,
        id: crypto.randomUUID(),
        revision: project.revision,
      };
    let value: AnalysisJob;
    try {
      value = await api<AnalysisJob>(
        `/projects/${project.project_id}/timeline/analysis`,
        {
          ...body,
          base_revision: pending.current.revision,
          request_id: pending.current.id,
        },
      );
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "REVISION_CONFLICT")
        pending.current = null;
      if (!(failure instanceof ApiError))
        setError("提交结果暂未确认，可用同一请求重试或重新读取最近分析。");
      throw failure;
    }
    pending.current = null;
    setResult(null);
    setSelected([]);
    setPage(0);
    setProposal(null);
    setReplaceIds([]);
    setJob(value);
    setJobId(value.id);
    setConsent(false);
    setError("");
    setHistory((items) =>
      [value, ...items.filter((item) => item.id !== value.id)].slice(0, 20),
    );
  }
  function chooseJob(id: string) {
    setJobId(id);
    setResult(null);
    setSelected([]);
    setPage(0);
    setProposal(null);
    setReplaceIds([]);
    setJob(history.find((item) => item.id === id) || null);
  }
  const count = result?.frames?.length ?? result?.segments?.length ?? 0;
  const rangeValid =
    !!selectedAsset &&
    start >= 0 &&
    duration > 0 &&
    duration <= fps * 1800 &&
    (start + duration) / fps <=
      (selectedAsset.duration_seconds || 0) + 1 / 48000;
  return (
    <section
      className="panel timeline-form audio-analysis"
      aria-label="节拍与语音分析"
    >
      <h3>节拍与语音分析</h3>
      <p className="muted">
        节拍在本机分析。语音转写将所选范围发送至已配置的服务，结果经预览和批准后写入时间轴。
      </p>
      <div className="composition-fields">
        <label>
          分析音频
          <select
            value={selectedAsset?.id || ""}
            onChange={(event) => {
              const value = audio.find(
                (item) => item.id === event.target.value,
              );
              setAssetId(event.target.value);
              setStart(0);
              setDuration(
                Math.max(
                  1,
                  Math.min(
                    Math.floor((value?.duration_seconds || 1) * fps),
                    Math.floor(fps * 1800),
                  ),
                ),
              );
            }}
          >
            {!audio.length && <option value="">请先导入音频</option>}
            {audio.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.provenance?.name || asset.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <label>
          分析源起始帧
          <input
            type="number"
            min={0}
            value={start}
            onChange={(event) => setStart(Number(event.target.value))}
          />
        </label>
        <label>
          分析范围帧数
          <input
            type="number"
            min={1}
            max={Math.floor(fps * 1800)}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
          />
        </label>
      </div>
      <button
        disabled={busy || working || !rangeValid}
        onClick={() => void run(() => enqueue("beats"))}
      >
        检测节拍
      </button>
      <div className="composition-fields">
        <label>
          转写连接
          <select
            value={profile?.provider_id || ""}
            onChange={(event) => setProviderId(event.target.value)}
          >
            {!profiles.length && (
              <option value="">请在模型与工具中启用转写连接</option>
            )}
            {profiles.map((item) => (
              <option key={item.provider_id} value={item.provider_id}>
                {item.display_name || item.provider_id} · {item.model}
              </option>
            ))}
          </select>
        </label>
        <label>
          转写语言
          <input
            value={language}
            placeholder="自动识别，或填写 zh / en"
            onChange={(event) => setLanguage(event.target.value)}
          />
        </label>
      </div>
      {profile && !profile.transcription_timestamps && (
        <p role="status">请在连接设置中确认模型支持分段时间戳。</p>
      )}
      <label className="check-label">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
        />
        批准发送所选音频并承担本次转写费用
      </label>
      <button
        disabled={
          busy ||
          working ||
          !rangeValid ||
          !consent ||
          !profile?.transcription_timestamps
        }
        onClick={() => void run(() => enqueue("transcription"))}
      >
        转写语音
      </button>
      {history.length > 0 && (
        <label>
          最近分析
          <select
            value={jobId}
            onChange={(event) => chooseJob(event.target.value)}
          >
            <option value="">选择分析记录</option>
            {history.map((item) => (
              <option key={item.id} value={item.id}>
                {item.operation === "analyze_beats" ? "节拍" : "转写"} ·{" "}
                {item.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      )}
      {job && (
        <p role="status">
          源修订 {job.source_revision} · 当前修订 {project.revision} ·{" "}
          {job.state}
        </p>
      )}
      {job?.error && <p role="alert">{job.error.message}</p>}
      {working && (
        <button
          disabled={busy}
          onClick={() =>
            void run(() =>
              api(`/projects/${project.project_id}/jobs/${jobId}/cancel`, {}),
            )
          }
        >
          取消分析
        </button>
      )}
      {error && (
        <p role="status">
          {error}{" "}
          <button onClick={() => setRetry((value) => value + 1)}>
            重新读取分析
          </button>
        </p>
      )}
      {result && (
        <>
          <p>
            {count} 项候选{result.bpm ? ` · 估计 ${result.bpm} BPM` : ""}
            {result.confidence != null
              ? ` · 周期一致度 ${Math.round(result.confidence * 100)}%`
              : ""}
          </p>
          {count === 0 && <p>所选范围没有检测到候选。</p>}
          <div className="analysis-candidates">
            {Array.from(
              { length: Math.min(50, Math.max(0, count - page * 50)) },
              (_, offset) => {
                const index = page * 50 + offset;
                const segment = result.segments?.[index];
                return (
                  <label key={index} className="check-label">
                    <input
                      type="checkbox"
                      aria-label={`候选 ${index + 1}`}
                      checked={selected.includes(index)}
                      onChange={(event) => {
                        setSelected((items) =>
                          event.target.checked
                            ? [...items, index]
                            : items.filter((item) => item !== index),
                        );
                        setProposal(null);
                      }}
                    />
                    {segment
                      ? `${segment.start_ms}–${segment.end_ms} ms · ${segment.text}`
                      : `${result.frames![index]} 帧`}
                  </label>
                );
              },
            )}
          </div>
          {count > 50 && (
            <div className="composition-actions">
              <button
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                上一页候选
              </button>
              <span>第 {page + 1} 页</span>
              <button
                disabled={(page + 1) * 50 >= count}
                onClick={() => setPage((value) => value + 1)}
              >
                下一页候选
              </button>
            </div>
          )}
          <div className="composition-actions">
            <button
              disabled={
                !count || count > (result.kind === "beats" ? 1000 : 500)
              }
              onClick={() => {
                setSelected(Array.from({ length: count }, (_, index) => index));
                setProposal(null);
              }}
            >
              选择全部候选
            </button>
            <button
              disabled={!selected.length}
              onClick={() => {
                setSelected([]);
                setProposal(null);
              }}
            >
              清空候选选择
            </button>
          </div>
          <label>
            候选写入起始帧
            <input
              type="number"
              min={0}
              value={target}
              onChange={(event) => {
                setTarget(Number(event.target.value));
                setProposal(null);
              }}
            />
          </label>
          {result.kind === "transcription" && (
            <details>
              <summary>选择需要替换的现有字幕</summary>
              <p>默认保留现有字幕，可逐条选择要替换的内容。</p>
              {captions.map((caption) => (
                <label key={caption.id} className="check-label">
                  <input
                    type="checkbox"
                    checked={replaceIds.includes(caption.id)}
                    onChange={(event) => {
                      setReplaceIds((items) =>
                        event.target.checked
                          ? [...items, caption.id]
                          : items.filter((id) => id !== caption.id),
                      );
                      setProposal(null);
                    }}
                  />
                  {caption.text}
                </label>
              ))}
            </details>
          )}
          <button
            disabled={busy || !selected.length || selected.length > 1000}
            onClick={() =>
              void run(async () => {
                const body = {
                  base_revision: project.revision,
                  selected_indices: selected,
                  target_start_frame: target,
                  replace_caption_ids: replaceIds,
                };
                const preview = await api<ApplyPreview>(
                  `/projects/${project.project_id}/timeline/analysis/${jobId}/preview`,
                  body,
                );
                setProposal({ body, preview });
              })
            }
          >
            预览选中候选
          </button>
        </>
      )}
      {proposal && (
        <div className="composition-plan">
          <p>
            新增 {proposal.preview.add_captions.length} 条字幕、
            {proposal.preview.add_markers.length} 个节拍标记。
            {proposal.preview.remove_captions.length
              ? `替换 ${proposal.preview.remove_captions.length} 条字幕。`
              : "保留现有字幕。"}
          </p>
          {proposal.preview.remove_captions.map((caption) => (
            <p key={caption.id}>将移除 {caption.text}</p>
          ))}
          <p className="muted">
            预览基于修订 {proposal.body.base_revision}，当前修订{" "}
            {project.revision}。
          </p>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api(
                  `/projects/${project.project_id}/timeline/analysis/${jobId}/commit`,
                  {
                    ...proposal.body,
                    approved: true,
                    preview_token: proposal.preview.preview_token,
                  },
                );
                setProposal(null);
                setSelected([]);
                setReplaceIds([]);
              })
            }
          >
            批准写入时间轴
          </button>
        </div>
      )}
    </section>
  );
}
