import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines,
  BrainCircuit,
  Check,
  CircleHelp,
  Eye,
  EyeOff,
  Image,
  Plug,
  Save,
  Video,
} from "lucide-react";
import { api } from "./api";
import type { Connection, ModelSettings, ProviderKind } from "./types";
import "./providers.css";
import { ComfyWorkflowEditor } from "./ComfyWorkflowEditor";

const categories = [
  {
    kind: "llm" as const,
    label: "LLM",
    role: "剧本与分镜规划",
    icon: BrainCircuit,
    path: "/chat/completions",
  },
  {
    kind: "video" as const,
    label: "视频生成",
    role: "镜头视频与异步任务",
    icon: Video,
    path: "/videos",
  },
  {
    kind: "image" as const,
    label: "图像生成",
    role: "参考图与视觉素材",
    icon: Image,
    path: "/images/generations",
  },
  {
    kind: "speech" as const,
    label: "语音合成",
    role: "旁白与配音素材",
    icon: AudioLines,
    path: "/audio/speech",
  },
];

export function ProviderSettings() {
  const client = useQueryClient();
  const [kind, setKind] = useState<ProviderKind>("llm");
  const [notice, setNotice] = useState("");
  const settings = useQuery({
    queryKey: ["model-settings"],
    queryFn: () => api<ModelSettings>("/settings/models"),
  });
  const selected = settings.data?.providers.find((item) => item.kind === kind);

  async function setDefault(category: ProviderKind, value: string) {
    try {
      await api("/settings/defaults", { [category]: value }, "PUT");
      await client.invalidateQueries();
      setNotice("默认模型已更新");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存失败");
    }
  }

  return (
    <section className="page provider-settings">
      <div className="connection-heading">
        <div>
          <p className="eyebrow">MODEL CONNECTIONS</p>
          <h1>模型连接</h1>
          <p className="muted">把你使用的生成服务接入工作室。</p>
        </div>
        <span className="local-secrets">
          <Check size={14} />
          密钥在本机加密保存
        </span>
      </div>
      {settings.error && (
        <p role="alert" className="settings-error">
          {settings.error.message}
        </p>
      )}
      {notice && (
        <p role="status" className="settings-notice">
          {notice}
        </p>
      )}
      <div className="connection-tabs" role="tablist" aria-label="模型类别">
        {categories.map((category) => {
          const Icon = category.icon;
          const enabled = settings.data?.providers.find(
            (p) => p.kind === category.kind,
          )?.enabled;
          return (
            <button
              role="tab"
              aria-label={category.label}
              aria-selected={kind === category.kind}
              key={category.kind}
              className={kind === category.kind ? "selected" : ""}
              onClick={() => {
                setKind(category.kind);
                setNotice("");
              }}
            >
              <Icon size={21} />
              <span>
                <strong>{category.label}</strong>
                <small>{category.role}</small>
              </span>
              <i
                className={
                  enabled ? "connection-dot enabled" : "connection-dot"
                }
              />
            </button>
          );
        })}
      </div>
      {selected && (
        <ConnectionForm
          key={kind}
          profile={selected}
          saved={async () => {
            await client.invalidateQueries();
            setNotice("设置已保存");
          }}
        />
      )}
      <section className="panel connection-defaults">
        <div>
          <h2>工作室默认模型</h2>
          <p className="muted">也可以在具体创作任务中切换。</p>
        </div>
        <div className="defaults-grid">
          {categories.map((category) => {
            const connection = settings.data?.providers.find(
              (p) => p.kind === category.kind,
            );
            return (
              <label key={category.kind}>
                默认 {category.label}
                <select
                  aria-label={`默认 ${category.label}`}
                  value={settings.data?.defaults[category.kind] || ""}
                  onChange={(e) =>
                    void setDefault(category.kind, e.target.value)
                  }
                >
                  {category.kind === "llm" && (
                    <option value="mock-director">Mock Director · 离线</option>
                  )}
                  {category.kind === "video" && (
                    <option value="mock-video-local">Mock Video · 离线</option>
                  )}
                  <option
                    value={`openai-${category.kind}`}
                    disabled={!connection?.enabled}
                  >
                    {connection?.enabled ? connection.model : "连接尚未启用"}
                  </option>
                </select>
              </label>
            );
          })}
        </div>
      </section>
    </section>
  );
}

function ConnectionForm({
  profile,
  saved,
}: {
  profile: Connection;
  saved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<Connection>({
    ...profile,
    protocol: profile.protocol || "openai-compatible",
    workflow: profile.workflow || null,
    request_cost: profile.request_cost ?? null,
    cost_currency: profile.cost_currency || "USD",
  });
  const client = useQueryClient();
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const category = categories.find((c) => c.kind === profile.kind)!;
  const Icon = category.icon;

  function patch<K extends keyof Connection>(field: K, value: Connection[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function body(testing = false) {
    return {
      revision: draft.revision,
      base_url: draft.base_url,
      model: draft.model,
      enabled: testing ? false : draft.enabled,
      api_key: key,
      clear_api_key: clearKey,
      allow_private_network: draft.allow_private_network,
      timeout_seconds: draft.timeout_seconds,
      json_mode: draft.json_mode,
      video_seconds: draft.video_seconds,
      video_size: draft.video_size,
      video_poll_seconds: draft.video_poll_seconds,
      video_timeout_seconds: draft.video_timeout_seconds,
      image_size: draft.image_size,
      speech_voice: draft.speech_voice,
      protocol: draft.protocol,
      workflow: draft.workflow,
      request_cost: draft.request_cost || null,
      cost_currency: draft.cost_currency,
    };
  }

  async function submit(testing = false) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (testing) {
        const result = await api<{ models: string[]; message: string }>(
          `/settings/providers/${profile.kind}/test`,
          body(true),
        );
        setModels(result.models);
        setMessage(result.message);
        await client.invalidateQueries();
      } else {
        const result = await api<Connection>(
          `/settings/providers/${profile.kind}`,
          body(),
          "PUT",
        );
        setDraft(result);
        setKey("");
        setShowKey(false);
        setClearKey(false);
        await saved();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "连接操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="connection-layout">
      <section className="panel connection-form">
        <header>
          <div className="connection-icon">
            <Icon size={24} />
          </div>
          <div>
            <h2>{category.label} 接口</h2>
            <p className="muted">
              {draft.protocol === "comfyui"
                ? "ComfyUI 工作流"
                : "OpenAI 兼容协议"}
            </p>
          </div>
          <span className={`status ${draft.enabled ? "status-ready" : ""}`}>
            {draft.enabled ? "已启用" : "未启用"}
          </span>
        </header>
        {(profile.kind === "video" || profile.kind === "image") && (
          <label>
            接口协议
            <select
              aria-label="接口协议"
              value={draft.protocol}
              onChange={(e) =>
                patch("protocol", e.target.value as Connection["protocol"])
              }
            >
              <option value="openai-compatible">OpenAI 兼容</option>
              <option value="comfyui">ComfyUI</option>
            </select>
          </label>
        )}
        {profile.kind === "video" && draft.protocol !== "comfyui" && (
          <p className="protocol-note">
            OpenAI 官方视频服务已停止。请填写提供兼容 /videos 协议的第三方服务。
          </p>
        )}
        <label>
          服务 URL
          <input
            type="url"
            aria-label="服务 URL"
            value={draft.base_url}
            autoComplete="off"
            placeholder={
              profile.kind === "video"
                ? "https://your-video-provider.example/v1"
                : "https://api.openai.com/v1"
            }
            onChange={(e) => patch("base_url", e.target.value)}
          />
          <small>
            填写包含版本前缀的基础地址，例如 https://your-provider.example/v1。
          </small>
        </label>
        <label>
          API Key
          <div className="secret-input">
            <input
              aria-label="API Key"
              type={showKey ? "text" : "password"}
              value={key}
              autoComplete="new-password"
              spellCheck={false}
              placeholder={
                draft.has_api_key
                  ? "已保存，留空可继续使用"
                  : "填写服务商提供的 API Key"
              }
              onChange={(e) => setKey(e.target.value)}
            />
            <button
              type="button"
              className="icon-button"
              aria-label={showKey ? "隐藏输入的密钥" : "显示输入的密钥"}
              onClick={() => setShowKey(!showKey)}
            >
              {showKey ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
          <small>
            {draft.has_api_key ? "密钥已保存在本机" : "本地无鉴权服务可以留空"}
          </small>
        </label>
        <label>
          {draft.protocol === "comfyui" ? "工作流名称" : "模型名称"}
          <input
            aria-label="模型名称"
            value={draft.model}
            list={`model-options-${profile.kind}`}
            placeholder="填写模型 ID，或从发现的模型中选择"
            onChange={(e) => patch("model", e.target.value)}
          />
          <datalist id={`model-options-${profile.kind}`}>
            {models.map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
          {models.length > 0 && <small>已发现 {models.length} 个模型</small>}
        </label>
        {draft.protocol === "comfyui" && (
          <ComfyWorkflowEditor
            mediaType={profile.kind === "video" ? "video" : "image"}
            value={draft.workflow || null}
            onChange={(value) => patch("workflow", value)}
          />
        )}
        <p className="muted">
          连接检查：
          {profile.health?.status === "connected"
            ? "已通过"
            : profile.health?.status === "failed"
              ? `失败 ${profile.health.error_code || ""}`
              : "尚未检查"}
          。生成能力需运行任务验收。
        </p>
        <label className="check-label">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => patch("enabled", e.target.checked)}
          />
          启用此连接
        </label>
        <details className="connection-advanced">
          <summary>高级选项</summary>
          <div className="editor-grid">
            <label>
              单次请求预估费用
              <input
                aria-label="单次请求预估费用"
                type="number"
                min="0"
                step="0.000001"
                value={draft.request_cost ?? ""}
                placeholder="留空表示未知"
                onChange={(e) => patch("request_cost", e.target.value || null)}
              />
            </label>
            <label>
              预估费用币种
              <input
                aria-label="预估费用币种"
                maxLength={3}
                value={draft.cost_currency}
                onChange={(e) =>
                  patch("cost_currency", e.target.value.toUpperCase())
                }
              />
            </label>
            <label>
              请求超时（秒）
              <input
                type="number"
                min={5}
                max={180}
                value={draft.timeout_seconds}
                onChange={(e) =>
                  patch("timeout_seconds", Number(e.target.value))
                }
              />
            </label>
            {profile.kind === "video" && (
              <>
                <label>
                  服务生成时长（秒）
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={draft.video_seconds}
                    onChange={(e) =>
                      patch("video_seconds", Number(e.target.value))
                    }
                  />
                </label>
                <label>
                  服务生成尺寸
                  <input
                    value={draft.video_size}
                    onChange={(e) => patch("video_size", e.target.value)}
                  />
                </label>
                <label>
                  远端等待上限（秒）
                  <input
                    type="number"
                    min={30}
                    max={3600}
                    value={draft.video_timeout_seconds}
                    onChange={(e) =>
                      patch("video_timeout_seconds", Number(e.target.value))
                    }
                  />
                </label>
                <label>
                  轮询间隔（秒）
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={draft.video_poll_seconds}
                    onChange={(e) =>
                      patch("video_poll_seconds", Number(e.target.value))
                    }
                  />
                </label>
              </>
            )}
            {profile.kind === "image" && (
              <label>
                图像尺寸
                <input
                  value={draft.image_size}
                  onChange={(e) => patch("image_size", e.target.value)}
                />
              </label>
            )}
            {profile.kind === "speech" && (
              <label>
                音色名称
                <input
                  value={draft.speech_voice}
                  onChange={(e) => patch("speech_voice", e.target.value)}
                />
              </label>
            )}
          </div>
          {profile.kind === "llm" && (
            <label className="check-label">
              <input
                type="checkbox"
                checked={draft.json_mode}
                onChange={(e) => patch("json_mode", e.target.checked)}
              />
              使用 JSON mode（服务不支持时可关闭）
            </label>
          )}
          <label className="check-label">
            <input
              type="checkbox"
              checked={draft.allow_private_network}
              onChange={(e) => patch("allow_private_network", e.target.checked)}
            />
            允许连接本机 / 内网服务及 HTTP 地址
          </label>
          {draft.has_api_key && (
            <label className="check-label">
              <input
                type="checkbox"
                checked={clearKey}
                onChange={(e) => setClearKey(e.target.checked)}
              />
              清除当前连接保存的密钥
            </label>
          )}
        </details>
        {error && (
          <p className="settings-error" role="alert">
            {error}
          </p>
        )}
        {message && (
          <p className="settings-notice" role="status">
            {message}
          </p>
        )}
        <footer>
          <button
            disabled={busy || !draft.base_url}
            onClick={() => void submit(true)}
          >
            <Plug size={16} />
            {busy ? "处理中…" : "测试连接 / 获取模型"}
          </button>
          <button
            className="primary"
            disabled={
              busy || !draft.base_url || (draft.enabled && !draft.model)
            }
            onClick={() => void submit()}
          >
            <Save size={16} />
            保存设置
          </button>
        </footer>
      </section>
      <aside className="connection-help">
        <CircleHelp size={20} />
        <h2>连接说明</h2>
        <p>保存服务信息后，在工作室选择对应模型即可使用。</p>
        <dl>
          <dt>请求端点</dt>
          <dd>
            {draft.protocol === "comfyui"
              ? "/prompt → /history → /view"
              : category.path}
          </dd>
          <dt>连接测试</dt>
          <dd>
            {draft.protocol === "comfyui"
              ? "读取 /object_info，核对节点和映射。"
              : "读取 /models，验证地址与鉴权。"}
            生成时会单独请求授权。
          </dd>
          <dt>费用</dt>
          <dd>
            可填写单次预估费用用于预算预留；留空表示未知，实际费用在任务页核对。
          </dd>
        </dl>
        {profile.kind === "video" && (
          <p>
            异步视频需要支持提交、查询任务和下载内容。服务时长可在高级选项中调整，下载后按项目规格规范化。
          </p>
        )}
        <p>排队任务保留提交时的连接版本。停用连接可阻止待执行任务调用服务。</p>
      </aside>
    </div>
  );
}
