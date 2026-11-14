import { useState } from "react";
import { api } from "./api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Job } from "./types";

type Cleanup = {
  id: string;
  preview_token: string;
  base_revision: number;
  files: { name: string; size: number }[];
  bytes: number;
  scanned: number;
  truncated: boolean;
  expires_at: number;
};
export function LocalMaintenance({ projectId }: { projectId?: string }) {
  const client = useQueryClient();
  const [preview, setPreview] = useState<Cleanup>();
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const job = useQuery({
    queryKey: ["cleanup-job", projectId, jobId],
    enabled: !!jobId && !!projectId,
    queryFn: () => api<Job>(`/projects/${projectId}/jobs/${jobId}`),
    refetchInterval: (q) =>
      ["completed", "failed", "cancelled"].includes(q.state.data?.state || "")
        ? false
        : 1000,
  });
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await client.invalidateQueries();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel image-import">
      <div className="section-heading">
        <h2>本机维护</h2>
        <a
          className="button"
          href={`/api/v1/system/report${projectId ? `?project_id=${projectId}` : ""}`}
          download
        >
          下载诊断报告
        </a>
      </div>
      {projectId && (
        <>
          <p className="muted">预览超过 24 小时、尚未登记的项目素材文件。</p>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const apr = await api<{ revision: number }>(
                  `/projects/${projectId}/apr`,
                );
                setPreview(
                  await api<Cleanup>(
                    `/projects/${projectId}/maintenance/cleanup/preview`,
                    { base_revision: apr.revision },
                  ),
                );
                setJobId("");
              })
            }
          >
            预览存储清理
          </button>
          {preview && (
            <div aria-label="清理预览">
              <p>
                扫描 {preview.scanned} 项 · 可清理 {preview.files.length} 个文件
                · {preview.bytes} 字节
              </p>
              {preview.truncated && (
                <p role="status">达到单次扫描上限，本次仅处理已列出的文件。</p>
              )}
              <small>
                预览有效至{" "}
                {new Date(preview.expires_at * 1000).toLocaleTimeString()}
              </small>
              <ul>
                {preview.files.map((f) => (
                  <li key={f.name}>
                    {f.name} · {f.size} 字节
                  </li>
                ))}
              </ul>
              <button
                disabled={busy || !preview.files.length || !!jobId}
                onClick={() =>
                  void run(async () => {
                    const queued = await api<Job>(
                      `/projects/${projectId}/maintenance/cleanup`,
                      {
                        plan_id: preview.id,
                        preview_token: preview.preview_token,
                        base_revision: preview.base_revision,
                        approved: true,
                      },
                    );
                    setJobId(queued.id);
                  })
                }
              >
                批准清理上述文件
              </button>
            </div>
          )}
          {job.data && (
            <div role="status">
              <p>清理任务 {job.data.state}</p>
              {job.data.cleanup_result && (
                <pre>{JSON.stringify(job.data.cleanup_result, null, 2)}</pre>
              )}
              {job.data.error && <p>{job.data.error.message}</p>}
            </div>
          )}
          {job.error && <p role="alert">{job.error.message}</p>}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
