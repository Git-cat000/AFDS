import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { api } from "./api";
import type { ModelSettings, ProviderKind } from "./types";
import "./providers.css";
import { ConnectionForm } from "./ModelConnectionForm";
import { CandidateSettings } from "./CandidateSettings";
import { categories } from "./model-categories";

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
      {selected && (
        <CandidateSettings
          key={`candidates-${kind}`}
          kind={kind}
          profile={selected}
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
