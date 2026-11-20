import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { ConnectionForm } from "./ModelConnectionForm";
import type { Connection, ProviderKind } from "./types";
import type { RoutingCandidate } from "./routing-types";

type Candidate = Omit<Connection, "health"> & {
  id: string;
  label: string;
  all_projects: boolean;
  project_ids: string[];
  priority: number;
  quality?: RoutingCandidate["quality"];
  latency?: RoutingCandidate["latency"];
  health?: string;
};

function asConnection(profile: Candidate): Connection {
  return {
    ...profile,
    display_name: profile.label,
    provider_id: `openai-${profile.kind}`,
    health: {
      status:
        profile.health === "healthy"
          ? "connected"
          : profile.health === "unhealthy"
            ? "failed"
            : "untested",
      generation_tested: false,
    },
  };
}

export function CandidateSettings({
  kind,
  profile,
}: {
  kind: ProviderKind;
  profile: Connection;
}) {
  const [open, setOpen] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["model-candidates"],
    enabled: open,
    queryFn: () => api<Candidate[]>("/settings/candidates"),
  });
  const candidates = Array.isArray(query.data)
    ? query.data.filter((value) => value.kind === kind)
    : [];
  const selected = candidates.find((value) => value.id === selectedId);
  return (
    <details
      className="panel candidate-settings"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>管理候选连接</summary>
      {open && (
        <>
          {query.error && <p role="alert">{query.error.message}</p>}
          <label>
            编辑连接
            <select
              aria-label="编辑候选连接"
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
            >
              <option value="">新增连接</option>
              {candidates.map((value) => (
                <option key={value.id} value={value.id}>
                  {value.label || value.id} · v{value.revision}
                </option>
              ))}
            </select>
          </label>
          <CandidateForm
            key={`${kind}:${selectedId}:${selected?.revision || 0}`}
            profile={
              selected
                ? asConnection(selected)
                : { ...profile, revision: 0, has_api_key: false }
            }
            selected={selected}
            saved={async (identity) => {
              setSelectedId(identity);
              await client.invalidateQueries();
            }}
          />
        </>
      )}
    </details>
  );
}

function CandidateForm({
  profile,
  selected,
  saved,
}: {
  profile: Connection;
  selected?: Candidate;
  saved: (identity: string) => Promise<void>;
}) {
  const [identity, setIdentity] = useState(selected?.id || "");
  const [label, setLabel] = useState(selected?.label || "");
  const [allProjects, setAllProjects] = useState(
    selected?.all_projects ?? true,
  );
  const [projectIds, setProjectIds] = useState(
    selected?.project_ids?.join("\n") || "",
  );
  const [priority, setPriority] = useState(selected?.priority || 0);
  const [grade, setGrade] = useState(selected?.quality?.grade.toString() || "");
  const [qualitySource, setQualitySource] = useState(
    selected?.quality?.source || "",
  );
  const [sourceType, setSourceType] = useState<"manual" | "benchmark">(
    selected?.quality?.source_type || "manual",
  );
  const [latency, setLatency] = useState(
    selected?.latency?.p95_ms.toString() || "",
  );
  const [latencySource, setLatencySource] = useState(
    selected?.latency?.source || "",
  );
  return (
    <>
      <div className="editor-grid">
        <label>
          连接 ID
          <input
            aria-label="连接 ID"
            value={identity}
            disabled={!!selected}
            maxLength={100}
            onChange={(event) => setIdentity(event.target.value)}
          />
        </label>
        <label>
          连接名称
          <input
            aria-label="连接名称"
            value={label}
            maxLength={120}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
      </div>
      <details>
        <summary>质量依据与项目授权</summary>
        <div className="editor-grid">
          <label>
            优先级
            <input
              type="number"
              min={0}
              max={10000}
              value={priority}
              onChange={(event) => setPriority(Number(event.target.value))}
            />
          </label>
          <label>
            质量等级
            <input
              type="number"
              min={0}
              max={5}
              value={grade}
              onChange={(event) => setGrade(event.target.value)}
            />
          </label>
          <label>
            质量依据类型
            <select
              value={sourceType}
              onChange={(event) =>
                setSourceType(event.target.value as typeof sourceType)
              }
            >
              <option value="manual">人工评定</option>
              <option value="benchmark">测评记录</option>
            </select>
          </label>
          <label>
            质量依据
            <input
              value={qualitySource}
              maxLength={500}
              onChange={(event) => setQualitySource(event.target.value)}
            />
          </label>
          <label>
            观测延迟 p95（毫秒）
            <input
              type="number"
              min={0}
              value={latency}
              onChange={(event) => setLatency(event.target.value)}
            />
          </label>
          <label>
            延迟依据
            <input
              value={latencySource}
              maxLength={500}
              onChange={(event) => setLatencySource(event.target.value)}
            />
          </label>
        </div>
        <label className="check-label">
          <input
            type="checkbox"
            checked={allProjects}
            onChange={(event) => setAllProjects(event.target.checked)}
          />
          允许所有项目使用
        </label>
        {!allProjects && (
          <label>
            允许的项目 ID
            <textarea
              value={projectIds}
              onChange={(event) => setProjectIds(event.target.value)}
            />
          </label>
        )}
      </details>
      <ConnectionForm
        profile={profile}
        canTest={!!selected}
        testPath={
          selected ? `/settings/candidates/${selected.id}/test` : undefined
        }
        saved={async () => saved(identity)}
        onSave={async (body) => {
          if (
            !/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$/.test(identity) ||
            !label.trim()
          )
            throw new Error("填写连接 ID 和名称");
          if (grade !== "" && !qualitySource.trim())
            throw new Error("填写质量依据");
          if (latency !== "" && !latencySource.trim())
            throw new Error("填写延迟依据");
          const result = await api<Candidate>(
            `/settings/candidates/${identity}`,
            {
              kind: profile.kind,
              connection: {
                ...body,
                label,
                all_projects: allProjects,
                project_ids: projectIds.split(/[\s,，]+/).filter(Boolean),
                priority,
                quality:
                  grade === ""
                    ? null
                    : {
                        grade: Number(grade),
                        source_type: sourceType,
                        source: qualitySource,
                      },
                latency:
                  latency === ""
                    ? null
                    : { p95_ms: Number(latency), source: latencySource },
              },
            },
            "PUT",
          );
          return asConnection(result);
        }}
      />
    </>
  );
}
