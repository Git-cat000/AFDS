import { useState } from "react";
import { api } from "./api";
import type { Job } from "./types";

export function RemoteJobRecovery({
  job,
  projectId,
  busy,
  run,
}: {
  job: Job;
  projectId: string;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [taskId, setTaskId] = useState(job.external_task_id || "");
  const [consent, setConsent] = useState(false);
  return (
    <details className="remote-recovery">
      <summary>远端任务恢复与重新生成</summary>
      {(job.provider_id === "openai-video" ||
        (job.request as { provider_config?: { protocol?: string } })
          ?.provider_config?.protocol === "comfyui") && (
        <>
          <p>
            复制属于本次生成的远端任务 ID 或 ComfyUI
            prompt_id，继续查询和下载结果。
          </p>
          <label>
            远端视频任务 ID
            <input value={taskId} onChange={(e) => setTaskId(e.target.value)} />
          </label>
          <button
            disabled={busy || !/^[A-Za-z0-9_-]{1,200}$/.test(taskId)}
            onClick={() =>
              void run(() =>
                api(`/projects/${projectId}/jobs/${job.id}/remote`, {
                  external_task_id: taskId,
                  approved: true,
                }),
              )
            }
          >
            确认归属并恢复
          </button>
        </>
      )}
      <p>如需重新生成，请先核对远端状态和账单，再授权新的生成请求。</p>
      <label className="check-label">
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
        />
        我已核对远端任务，授权重新提交（可能再次计费）
      </label>
      <button
        disabled={busy || !consent}
        onClick={() =>
          void run(() =>
            api(`/projects/${projectId}/jobs/${job.id}/retry`, {
              restart_remote: true,
              allow_remote: true,
            }),
          )
        }
      >
        重新提交生成
      </button>
    </details>
  );
}
