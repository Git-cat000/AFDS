import { useEffect, useState } from "react";
import { api, mediaUrl } from "./api";
import type { EditorProject } from "./TimelineEditor";

type PreviewJob = {
  id: string;
  state: string;
  source_revision: number;
  output_asset_ids: string[];
  error?: { message: string } | null;
};

export function CompositionPreview({
  project,
  run,
  busy,
  totalFrames,
}: {
  project: EditorProject;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
  totalFrames: number;
}) {
  const fps = project.format.fps_num / project.format.fps_den;
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(
    Math.max(1, Math.min(totalFrames, Math.floor(fps * 10))),
  );
  const [job, setJob] = useState<PreviewJob | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const jobId = job?.id;
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<PreviewJob>(
          `/projects/${project.project_id}/jobs/${jobId}`,
        );
        if (!active) return;
        setJob(value);
        setError("");
        if (
          [
            "queued",
            "claimed",
            "running",
            "verifying",
            "retry_wait",
            "cancel_requested",
          ].includes(value.state)
        ) {
          timer = setTimeout(() => void poll(), 1000);
        }
      } catch {
        if (active)
          setError("预览任务暂时无法读取，可重试或在任务队列中查看。");
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [project.project_id, jobId, retry]);
  const video =
    job?.state === "completed" ? job.output_asset_ids[0] : undefined;
  return (
    <section className="composition-preview" aria-label="成片预览">
      <h3>成片预览</h3>
      <p className="muted">保存剪辑后，可生成最多 30 秒的预览片段。</p>
      <div className="composition-fields">
        <label>
          预览开始帧
          <input
            type="number"
            min={0}
            max={totalFrames}
            value={start}
            onChange={(event) => setStart(Number(event.target.value))}
          />
        </label>
        <label>
          预览时长帧
          <input
            type="number"
            min={1}
            max={Math.min(totalFrames, Math.floor(fps * 30))}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
          />
        </label>
        <button
          disabled={
            busy ||
            !totalFrames ||
            start + duration > totalFrames ||
            duration < 1
          }
          onClick={() =>
            void run(async () => {
              setError("");
              const value = await api<PreviewJob>(
                `/projects/${project.project_id}/timeline/preview`,
                {
                  base_revision: project.revision,
                  start_frame: start,
                  duration_frames: duration,
                },
              );
              setJob(value);
            })
          }
        >
          生成预览片段
        </button>
      </div>
      {job && (
        <p className="muted">
          源修订 {job.source_revision} · 当前修订 {project.revision} ·{" "}
          {job.state === "completed"
            ? "预览已生成"
            : job.state === "failed"
              ? "生成失败"
              : job.state === "cancelled"
                ? "已取消"
                : "正在生成预览"}
        </p>
      )}
      {job?.error && <p role="status">{job.error.message}</p>}
      {error && (
        <p role="status">
          {error}{" "}
          <button onClick={() => setRetry((value) => value + 1)}>
            重新读取预览
          </button>
        </p>
      )}
      {video && (
        <video
          aria-label="剪辑预览视频"
          controls
          preload="metadata"
          src={mediaUrl(project.project_id, video)}
        />
      )}
    </section>
  );
}
