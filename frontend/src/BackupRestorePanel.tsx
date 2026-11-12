import { useState, type FormEvent } from "react";
import { ArchiveRestore } from "lucide-react";
import { ApiError, restoreArchive } from "./api";
import type { Job } from "./types";
import "./operations.css";

export function BackupRestorePanel({
  onQueued,
}: {
  onQueued: (job: Job) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const oversized = !!file && file.size > 2 * 1024 ** 3;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !title.trim() || oversized || busy) return;
    setBusy(true);
    setError("");
    try {
      onQueued(await restoreArchive(file, title.trim(), requestId));
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "上传结果尚未确认。保持文件与名称不变并重试，可复用同一恢复请求。",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="panel restore-panel"
      onSubmit={(event) => void submit(event)}
    >
      <h2>
        <ArchiveRestore size={20} /> 从工程包恢复
      </h2>
      <p className="muted">
        将 AFDS ZIP
        恢复为可编辑的新项目，保留素材、选择的镜头、音轨与字幕。原项目不会被覆盖。
      </p>
      <label>
        工程包 ZIP
        <input
          type="file"
          accept=".zip,application/zip"
          disabled={busy}
          onChange={(event) => {
            const selected = event.target.files?.[0] || null;
            setFile(selected);
            setRequestId(crypto.randomUUID());
            setError("");
            if (selected)
              setTitle(
                `${selected.name.replace(/\.zip$/i, "")} · 恢复副本`.slice(
                  0,
                  200,
                ),
              );
          }}
        />
      </label>
      <label>
        恢复副本名称
        <input
          value={title}
          maxLength={200}
          required
          disabled={busy}
          onChange={(event) => {
            setTitle(event.target.value);
            setRequestId(crypto.randomUUID());
            setError("");
          }}
        />
      </label>
      <p className="protocol-note">
        不恢复连接密钥、旧任务/审片/修订历史及费用账本；历史账单仍需核对。新副本默认关闭外部模型调用，预算归零。恢复过程不调用模型。
      </p>
      <small className="muted">
        最大 2 GB。Worker 校验完整性后导入。更改文件或名称会开始新的恢复请求。
      </small>
      {oversized && <p role="alert">工程包超过 2 GB，请使用更小的工程包。</p>}
      {error && (
        <p role="alert" className="job-error">
          {error}
        </p>
      )}
      <button
        className="primary"
        disabled={busy || !file || !title.trim() || oversized}
      >
        {busy ? "正在上传工程包…" : "恢复为新项目"}
      </button>
    </form>
  );
}
