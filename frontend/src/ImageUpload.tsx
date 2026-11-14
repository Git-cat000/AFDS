import { useState } from "react";
import { api, ApiError } from "./api";
import { useQuery } from "@tanstack/react-query";
import type { Job, Project } from "./types";

export function ImageUpload({
  project,
  run,
  busy,
}: {
  project: Project;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const [file, setFile] = useState<File>();
  const [rights, setRights] = useState(false);
  const [jobId, setJobId] = useState("");
  const job = useQuery({
    queryKey: ["image-import", project.project_id, jobId],
    enabled: !!jobId,
    queryFn: () => api<Job>(`/projects/${project.project_id}/jobs/${jobId}`),
    refetchInterval: (q) =>
      ["completed", "failed", "cancelled"].includes(q.state.data?.state || "")
        ? false
        : 1000,
  });
  return (
    <details className="panel image-import">
      <summary>导入参考图像</summary>
      <label>
        PNG / JPEG / WebP · 最大 16 MiB
        <input
          aria-label="参考图文件"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          onChange={(e) => {
            setFile(e.target.files?.[0]);
            setRights(false);
          }}
        />
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={rights}
          onChange={(e) => setRights(e.target.checked)}
        />
        我有权使用并上传该图像
      </label>
      <button
        disabled={
          busy ||
          !rights ||
          !file ||
          (!!jobId &&
            !["completed", "failed", "cancelled"].includes(
              job.data?.state || "",
            ))
        }
        onClick={() =>
          void run(async () => {
            if (!file || file.size > 16 * 1024 * 1024)
              throw new Error("请选择不超过 16 MiB 的图像");
            const params = new URLSearchParams({
              base_revision: String(project.revision),
              name: file.name,
              rights_confirmed: "true",
            });
            const response = await fetch(
              `/api/v1/projects/${project.project_id}/images/import?${params}`,
              {
                method: "POST",
                headers: {
                  "Content-Type": file.type || "application/octet-stream",
                },
                body: file,
              },
            );
            const data = await response.json();
            if (!response.ok)
              throw new ApiError(
                data.error?.code || "HTTP_ERROR",
                data.error?.message || "上传失败",
                data.error?.details,
              );
            setJobId(data.id);
          })
        }
      >
        上传并导入图像
      </button>
      {job.data && (
        <p role="status">
          {job.data.state === "completed"
            ? "参考图已加入素材库"
            : `导入任务 ${job.data.state} · ${job.data.progress}%`}
          {job.data.error && ` · ${job.data.error.message}`}
        </p>
      )}
      {job.error && <p role="alert">{job.error.message}</p>}
    </details>
  );
}
