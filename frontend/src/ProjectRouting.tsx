import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { RouteExplanation } from "./RouteExplanation";
import {
  capabilityLabels,
  type Capability,
  type RouteDecision,
  type RoutingCandidate,
  type RoutingPolicy,
} from "./routing-types";

type RoutingSettings = {
  revision: number;
  policies: Partial<Record<Capability, RoutingPolicy>>;
  candidates: RoutingCandidate[];
};
type Run = (action: () => Promise<unknown>) => Promise<void>;

export function ProjectRouting({
  projectId,
  run,
  busy,
  canManage,
}: {
  projectId: string;
  run: Run;
  busy: boolean;
  canManage: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [capability, setCapability] = useState<Capability>("storyboard");
  const [draftVersion, setDraftVersion] = useState(0);
  const query = useQuery({
    queryKey: ["routing", projectId],
    enabled: open,
    queryFn: () => api<RoutingSettings>(`/projects/${projectId}/routing`),
  });
  return (
    <details
      className="panel project-routing"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>模型路由策略</summary>
      {open && (
        <>
          {query.error && <p role="alert">{query.error.message}</p>}
          <label>
            路由用途
            <select
              aria-label="路由用途"
              value={capability}
              onChange={(event) =>
                setCapability(event.target.value as Capability)
              }
            >
              {Object.entries(capabilityLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {query.data && (
            <RoutingEditor
              key={`${projectId}:${capability}:${draftVersion}`}
              data={query.data}
              capability={capability}
              projectId={projectId}
              run={run}
              busy={busy}
              canManage={canManage}
              reload={() => setDraftVersion((value) => value + 1)}
            />
          )}
        </>
      )}
    </details>
  );
}

function RoutingEditor({
  data,
  capability,
  projectId,
  run,
  busy,
  canManage,
  reload,
}: {
  data: RoutingSettings;
  capability: Capability;
  projectId: string;
  run: Run;
  busy: boolean;
  canManage: boolean;
  reload: () => void;
}) {
  const client = useQueryClient();
  const [baseRevision, setBaseRevision] = useState(data.revision);
  const [draft, setDraft] = useState<RoutingPolicy>(() => ({
    strategy: "preferred",
    ...data.policies[capability],
  }));
  const [units, setUnits] = useState(1);
  const [decision, setDecision] = useState<RouteDecision | null>(null);
  const candidates = data.candidates.filter((value) =>
    value.capabilities.includes(capability),
  );
  function patch(change: Partial<RoutingPolicy>) {
    setDraft((value) => ({ ...value, ...change }));
  }
  return (
    <div>
      {canManage && (
        <>
          {baseRevision !== data.revision && (
            <p role="status">
              项目已更新，当前草稿基于版本 {baseRevision}。
              <button onClick={reload}>重新载入策略</button>
            </p>
          )}
          <div className="editor-grid">
            <label>
              选择策略
              <select
                aria-label="选择策略"
                value={draft.strategy}
                onChange={(event) =>
                  patch({
                    strategy: event.target.value as RoutingPolicy["strategy"],
                  })
                }
              >
                <option value="preferred">优先指定连接</option>
                <option value="lowest_estimated_cost">预估费用最低</option>
                <option value="quality_first">质量优先</option>
                <option value="balanced">质量与延迟约束下排序</option>
              </select>
            </label>
            <label>
              优先连接
              <select
                value={draft.preferred_ids?.[0] || ""}
                onChange={(event) =>
                  patch({
                    preferred_ids: event.target.value
                      ? [event.target.value]
                      : [],
                  })
                }
              >
                <option value="">按候选优先级</option>
                {candidates.map((value) => (
                  <option key={value.id} value={value.id}>
                    {value.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              本次估算费用上限
              <input
                aria-label="本次估算费用上限"
                type="number"
                min="0"
                step="0.000001"
                value={draft.max_estimated_cost ?? ""}
                onChange={(event) =>
                  patch({ max_estimated_cost: event.target.value || null })
                }
              />
            </label>
          </div>
          <fieldset>
            <legend>允许的候选范围</legend>
            {candidates.map((value) => (
              <label className="check-label" key={value.id}>
                <input
                  type="checkbox"
                  aria-label={`允许 ${value.label}`}
                  checked={draft.allowed_ids?.includes(value.id) || false}
                  onChange={(event) =>
                    patch({
                      allowed_ids: event.target.checked
                        ? [...(draft.allowed_ids || []), value.id]
                        : (draft.allowed_ids || []).filter(
                            (identity) => identity !== value.id,
                          ),
                    })
                  }
                />
                {value.label} · {value.model} ·{" "}
                {value.estimated_cost ?? "费用未知"} {value.currency}
                {value.quality &&
                  ` · 质量 ${value.quality.grade}/5（${value.quality.source}）`}
                {value.latency &&
                  ` · p95 ${value.latency.p95_ms}ms（${value.latency.source}）`}
                {!value.enabled && " · 已停用"}
                {value.health === "unhealthy" && " · 连接测试失败"}
              </label>
            ))}
          </fieldset>
          <details>
            <summary>质量、延迟与未知数据</summary>
            <div className="editor-grid">
              <label>
                最低质量等级
                <input
                  type="number"
                  min={0}
                  max={5}
                  value={draft.minimum_quality ?? ""}
                  onChange={(event) =>
                    patch({
                      minimum_quality:
                        event.target.value === ""
                          ? null
                          : Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                延迟上限（毫秒）
                <input
                  type="number"
                  min={0}
                  value={draft.max_latency_ms ?? ""}
                  onChange={(event) =>
                    patch({
                      max_latency_ms:
                        event.target.value === ""
                          ? null
                          : Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                缺少质量依据
                <select
                  value={draft.unknown_quality || "allow"}
                  onChange={(event) =>
                    patch({
                      unknown_quality: event.target.value as
                        "allow" | "exclude",
                    })
                  }
                >
                  <option value="allow">允许参与</option>
                  <option value="exclude">排除</option>
                </select>
              </label>
              <label>
                缺少延迟观测
                <select
                  value={draft.unknown_latency || "allow"}
                  onChange={(event) =>
                    patch({
                      unknown_latency: event.target.value as
                        "allow" | "exclude",
                    })
                  }
                >
                  <option value="allow">允许参与</option>
                  <option value="exclude">排除</option>
                </select>
              </label>
              <label>
                未知费用
                <select
                  value={draft.unknown_cost || "inherit"}
                  onChange={(event) =>
                    patch({
                      unknown_cost: event.target.value as "inherit" | "exclude",
                    })
                  }
                >
                  <option value="inherit">沿用项目预算政策</option>
                  <option value="exclude">排除</option>
                </select>
              </label>
            </div>
          </details>
          <label className="check-label">
            <input
              type="checkbox"
              checked={draft.allow_fallback || false}
              onChange={(event) =>
                patch({ allow_fallback: event.target.checked })
              }
            />
            提交前允许在范围内切换备用连接
          </label>
          <button
            disabled={busy || !draft.allowed_ids?.length}
            onClick={() =>
              void run(async () => {
                const result = await api<{ revision: number }>(
                  `/projects/${projectId}/routing/policy`,
                  {
                    base_revision: baseRevision,
                    policies: { ...data.policies, [capability]: {...draft,
                      allowed_ids: draft.allowed_ids?.length ? draft.allowed_ids : null} },
                  },
                  "PUT",
                );
                setBaseRevision(result.revision);
                await client.invalidateQueries();
              })
            }
          >
            保存路由策略
          </button>
          <button
            disabled={busy || !data.policies[capability]}
            onClick={() =>
              void run(async () => {
                const policies = { ...data.policies };
                delete policies[capability];
                await api(
                  `/projects/${projectId}/routing/policy`,
                  { base_revision: data.revision, policies },
                  "PUT",
                );
                await client.invalidateQueries();
                reload();
              })
            }
          >
            使用工作室默认
          </button>
        </>
      )}
      <div className="editor-grid">
        <label>
          预览请求数
          <input
            type="number"
            min={1}
            max={1000}
            value={units}
            onChange={(event) => setUnits(Number(event.target.value))}
          />
        </label>
        <button
          disabled={busy || units < 1 || units > 1000}
          onClick={() =>
            void run(async () => {
              setDecision(
                await api<RouteDecision>(
                  `/projects/${projectId}/routing/preview`,
                  {
                    base_revision: data.revision,
                    capability,
                    allow_remote: true,
                    requirements: {
                      units,
                      timestamps: capability === "transcription",
                    },
                  },
                ),
              );
            })
          }
        >
          预览已保存策略
        </button>
      </div>
      {decision && <RouteExplanation decision={decision} />}
    </div>
  );
}
