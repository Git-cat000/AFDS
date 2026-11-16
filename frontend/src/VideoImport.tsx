import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { api } from "./api";
import type { EditorProject } from "./TimelineEditor";

export function VideoImport({
  project,
  run,
  busy,
}: {
  project: EditorProject;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [extract, setExtract] = useState(true);
  const [status, setStatus] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const oversized = !!file && file.size > 256 * 1024 ** 2;
  return (
    <section className="composition-import" aria-label="本机视频导入">
      <h3>导入视频</h3>
      <label>
        本机视频
        <input
          ref={input}
          type="file"
          accept=".mp4,.mov,.mkv,.webm"
          onChange={(event) => {
            setFile(event.target.files?.[0] || null);
            setRights(false);
            setStatus("");
          }}
        />
      </label>
      <small className="muted">
        MP4、MOV、MKV、WebM，最大 256 MiB。导入后统一项目画幅和帧率。
      </small>
      <label className="check">
        <input
          type="checkbox"
          checked={rights}
          onChange={(event) => setRights(event.target.checked)}
        />
        确认有权使用本视频
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={extract}
          onChange={(event) => setExtract(event.target.checked)}
        />
        提取可独立剪辑的音轨
      </label>
      {oversized && <p role="status">视频超过 256 MiB，请先裁切或压缩。</p>}
      <button
        disabled={busy || !file || !rights || oversized}
        onClick={() =>
          void run(async () => {
            if (!file) return;
            const query = new URLSearchParams({
              base_revision: String(project.revision),
              name: file.name,
              rights_confirmed: String(rights),
              extract_audio: String(extract),
            });
            await api(
              `/projects/${project.project_id}/timeline/video/import?${query}`,
              file,
            );
            setStatus("导入任务已提交，可在任务队列查看进度。");
            setFile(null);
            setRights(false);
            if (input.current) input.current.value = "";
          })
        }
      >
        <Upload size={14} />
        导入本机视频
      </button>
      {status && <p role="status">{status}</p>}
    </section>
  );
}
