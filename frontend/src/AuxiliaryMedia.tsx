import { useState } from "react";
import { Download, Image, AudioLines, Plus } from "lucide-react";
import { api, mediaUrl } from "./api";
import type { ModelSettings, Project } from "./types";

export function AuxiliaryMedia({
  project,
  settings,
  run,
  busy,
  preview,
}: {
  project: Project;
  settings?: ModelSettings;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
  preview: (id: string) => void;
}) {
  const [kind, setKind] = useState<"image" | "speech">("image");
  const [prompt, setPrompt] = useState("");
  const [consent, setConsent] = useState(false);
  const config = settings?.providers.find((item) => item.kind === kind);
  const referenceIds = new Set(
    project.asset_versions.flatMap((v) => [v.asset_id, v.poster_asset_id]),
  );
  const assets = project.assets.filter(
    (asset) => !referenceIds.has(asset.id) && asset.media_type !== "video",
  );

  return (
    <section className="auxiliary-media">
      <h2>图像与语音素材</h2>
      <div className="panel auxiliary-form">
        <div className="editor-grid">
          <label>
            素材类型
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as "image" | "speech")}
            >
              <option value="image">参考图像</option>
              <option value="speech">语音旁白</option>
            </select>
          </label>
          <div className="aux-model">
            <small>使用模型</small>
            <strong>
              {config?.enabled ? config.model : "请先在模型与工具中启用连接"}
            </strong>
          </div>
        </div>
        <label>
          {kind === "image" ? "图像描述" : "旁白文本"}
          <textarea
            rows={3}
            maxLength={5000}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>
        <label className="check-label">
          <input
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
          />
          允许发送上述内容，并承担服务商费用（金额未知）
        </label>
        <button
          className="primary"
          disabled={busy || !config?.enabled || !consent || !prompt.trim()}
          onClick={() =>
            void run(async () => {
              await api(`/projects/${project.project_id}/media`, {
                base_revision: project.revision,
                operation:
                  kind === "image" ? "generate_image" : "generate_speech",
                prompt,
                provider_id: settings?.defaults[kind],
                allow_remote: true,
                request_id: crypto.randomUUID(),
              });
              setConsent(false);
            })
          }
        >
          <Plus size={15} />
          {kind === "image" ? "生成图像" : "生成语音"}
        </button>
      </div>
      <div className="asset-grid">
        {assets.map((asset) => (
          <article className="panel asset-card" key={asset.id}>
            {asset.media_type === "image" ? (
              <img
                src={mediaUrl(project.project_id, asset.id)}
                alt="生成的参考图像"
              />
            ) : (
              <div className="audio-art">
                <AudioLines size={40} />
              </div>
            )}
            <h3>{asset.media_type === "image" ? "参考图像" : "生成旁白"}</h3>
            <small>{asset.provenance.provider_id}</small>
            <div className="button-row">
              <button onClick={() => preview(asset.id)}>
                {asset.media_type === "image" ? (
                  <Image size={14} />
                ) : (
                  <AudioLines size={14} />
                )}
                预览
              </button>
              <a
                className="download"
                download
                href={mediaUrl(project.project_id, asset.id, true)}
              >
                <Download size={14} />
                下载
              </a>
            </div>
          </article>
        ))}
      </div>
      <p className="muted">
        生成的旁白可播放和下载。时间轴音频混合将在后续版本加入。
      </p>
    </section>
  );
}
