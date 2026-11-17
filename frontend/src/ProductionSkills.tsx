import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, sessionFetch } from "./api";
import { useSession } from "./session";
import { importSkillFiles } from "./skill-import";
import { SkillRunner } from "./SkillRunner";
import { SkillRuns } from "./SkillRuns";
import {
  productionStages,
  type GuidanceSelections,
  type GuideSelection,
  type ProductionStage,
  type SkillAction,
  type SkillPackage,
} from "./skill-types";
import type { Preview, Project } from "./types";

function projectSelections(project?: Project): GuidanceSelections {
  return Object.fromEntries(
    Object.entries(project?.production_guidance || {}).map(
      ([stage, guides]) => [
        stage,
        guides.map((guide) => ({
          package_id: guide.package_id,
          resources: guide.resources.map((value) => value.path),
        })),
      ],
    ),
  );
}

export function ProductionSkills({
  project,
  initialStage = "story",
  shotId,
  busy,
  run,
}: {
  project?: Project;
  initialStage?: ProductionStage;
  shotId?: string;
  busy: boolean;
  run: SkillAction;
}) {
  const queries = useQueryClient();
  const { session } = useSession();
  const libraryRoot = project
    ? `/projects/${project.project_id}/skills`
    : "/skills";
  const [stage, setStage] = useState<ProductionStage>(initialStage);
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [importStages, setImportStages] = useState<ProductionStage[]>([
    initialStage,
  ]);
  const [source, setSource] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [view, setView] = useState<"guide" | "scripts">("guide");
  const [resourceText, setResourceText] = useState<{
    packageId: string;
    path: string;
    text: string;
  }>();
  const [draft, setDraft] = useState<{
    revision: number;
    selections: GuidanceSelections;
  }>();
  const [proposal, setProposal] = useState<{
    body: { base_revision: number; selections: GuidanceSelections };
    preview: Preview;
  }>();
  const library = useQuery({
    queryKey: ["skill-library", project?.project_id, stage, offset],
    queryFn: () =>
      api<{ items: SkillPackage[]; total: number }>(
        `${libraryRoot}?stage=${stage}&offset=${offset}&limit=20`,
      ),
  });
  const identifier = selectedId || library.data?.items[0]?.id;
  const detail = useQuery({
    queryKey: ["skill-detail", project?.project_id, identifier],
    enabled: !!identifier,
    queryFn: () => api<SkillPackage>(`${libraryRoot}/${identifier}`),
  });
  const selections = draft?.selections || projectSelections(project);
  const chosen = selections[stage] || [];
  const stale = !!draft && draft.revision !== project?.revision;
  const skill = detail.data;
  const selected = chosen.find((value) => value.package_id === skill?.id);

  async function perform(action: () => Promise<unknown>) {
    await run(async () => {
      setError("");
      try {
        await action();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "技能操作失败");
      }
    });
  }
  function update(values: GuideSelection[]) {
    if (!project) return;
    setDraft({
      revision: draft?.revision || project.revision,
      selections: { ...selections, [stage]: values },
    });
    setProposal(undefined);
  }
  function toggle(value: SkillPackage) {
    const active = chosen.some((item) => item.package_id === value.id);
    update(
      active
        ? chosen.filter((item) => item.package_id !== value.id)
        : [...chosen, { package_id: value.id, resources: [] }],
    );
  }
  async function imported(files: File[]) {
    if (session.mode === "team" && !project)
      throw new Error("请先选择项目再导入技能");
    const value = await importSkillFiles(
      files,
      importStages,
      source,
      project?.project_id,
    );
    setStage(importStages[0]);
    setOffset(0);
    setSelectedId(value.id);
    setResourceText(undefined);
    setView("guide");
    setMessage(`已导入 ${value.name}，可选择用于本阶段。`);
    await queries.invalidateQueries({ queryKey: ["skill-library"] });
  }

  return (
    <section className="production-skills" aria-label="分层制作技能">
      <div className="skills-heading">
        <label>
          制作阶段
          <select
            aria-label="制作阶段"
            value={stage}
            disabled={busy}
            onChange={(event) => {
              setStage(event.target.value as ProductionStage);
              setSelectedId("");
              setResourceText(undefined);
              setOffset(0);
              setView("guide");
            }}
          >
            {Object.entries(productionStages).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {project && (
          <p>
            {project.title} · 修订 {project.revision}
          </p>
        )}
      </div>
      <details className="skill-import">
        <summary>导入其它项目的技能</summary>
        <p>
          选择 SKILL.md、完整技能目录或单个技能的 ZIP，保留 references、scripts
          和 assets 资源。
        </p>
        <label>
          来源说明
          <input
            value={source}
            maxLength={1000}
            disabled={busy}
            placeholder="项目名称或来源地址"
            onChange={(event) => setSource(event.target.value)}
          />
        </label>
        <fieldset>
          <legend>适用阶段</legend>
          {Object.entries(productionStages).map(([value, label]) => (
            <label className="check-label" key={value}>
              <input
                type="checkbox"
                disabled={busy}
                checked={importStages.includes(value as ProductionStage)}
                onChange={(event) =>
                  setImportStages(
                    event.target.checked
                      ? [...importStages, value as ProductionStage]
                      : importStages.filter((item) => item !== value),
                  )
                }
              />
              {label}
            </label>
          ))}
        </fieldset>
        <div className="skill-import-inputs">
          <label>
            导入 SKILL.md 或 ZIP
            <input
              aria-label="导入 SKILL.md 或 ZIP"
              type="file"
              accept=".md,.zip"
              disabled={busy}
              onChange={(event) => {
                const files = Array.from(event.target.files || []);
                if (files.length) void perform(() => imported(files));
                event.target.value = "";
              }}
            />
          </label>
          <label>
            导入技能目录
            <input
              aria-label="导入技能目录"
              type="file"
              multiple
              {...{ webkitdirectory: "" }}
              disabled={busy}
              onChange={(event) => {
                const files = Array.from(event.target.files || []);
                if (files.length) void perform(() => imported(files));
                event.target.value = "";
              }}
            />
          </label>
        </div>
      </details>
      {message && <p role="status">{message}</p>}
      {(error || library.error || detail.error) && (
        <p role="alert">
          {error || library.error?.message || detail.error?.message}
        </p>
      )}
      {!project && (
        <p className="muted">选择项目后可启用指导、运行检查和批准脚本。</p>
      )}
      <div className="skills-layout">
        <section className="skills-list" aria-label="阶段技能列表">
          {library.isPending && <p>正在读取技能…</p>}
          {!library.isPending && !library.data?.items.length && (
            <p className="muted">该阶段尚无技能，可导入其它项目的技能包。</p>
          )}
          {library.data?.items.map((value) => (
            <article
              key={value.id}
              className={
                identifier === value.id ? "skill-card selected" : "skill-card"
              }
            >
              <button
                className="skill-card-title"
                disabled={busy}
                onClick={() => {
                  setSelectedId(value.id);
                  setResourceText(undefined);
                  setView("guide");
                }}
              >
                <strong>{value.name}</strong>
                <small>
                  {value.builtin ? "AFDS 内置" : value.source || "本机导入"} ·{" "}
                  {value.version}
                </small>
              </button>
              <p>{value.description}</p>
              <label className="check-label">
                <input
                  type="checkbox"
                  aria-label={`启用 ${value.name}`}
                  checked={chosen.some((item) => item.package_id === value.id)}
                  disabled={
                    busy ||
                    !project ||
                    stale ||
                    (!chosen.some((item) => item.package_id === value.id) &&
                      chosen.length >= 3)
                  }
                  onChange={() => toggle(value)}
                />
                用于{productionStages[stage]}
              </label>
            </article>
          ))}
          {!!library.data && library.data.total > 20 && (
            <div className="button-row">
              <button
                disabled={!offset}
                onClick={() => {
                  setOffset(Math.max(0, offset - 20));
                  setSelectedId("");
                }}
              >
                上一页
              </button>
              <button
                disabled={offset + 20 >= library.data.total}
                onClick={() => {
                  setOffset(offset + 20);
                  setSelectedId("");
                }}
              >
                下一页
              </button>
            </div>
          )}
        </section>
        <section className="skill-inspector" aria-label="技能内容与脚本">
          {skill && (
            <>
              <header>
                <h3>{skill.name}</h3>
                <small>{skill.license && `许可 ${skill.license}`}</small>
              </header>
              {skill.compatibility && (
                <p className="muted">{skill.compatibility}</p>
              )}
              <div className="segmented">
                <button
                  aria-pressed={view === "guide"}
                  onClick={() => setView("guide")}
                >
                  指导与资料
                </button>
                <button
                  aria-pressed={view === "scripts"}
                  onClick={() => setView("scripts")}
                >
                  脚本与运行
                </button>
              </div>
              {view === "guide" ? (
                <>
                  <pre className="skill-instructions">{skill.instructions}</pre>
                  <details>
                    <summary>资源与来源</summary>
                    <small>包 SHA256 {skill.id}</small>
                    <ul>
                      {skill.resources?.map((file) => (
                        <li key={file.path}>
                          <span>
                            {file.path} · {file.size} B
                          </span>
                          {file.size <= 128 * 1024 &&
                            /\.(md|txt|json|yaml|yml|csv|py|js|mjs|cjs|ps1|sh)$/i.test(
                              file.path,
                            ) && (
                              <button
                                onClick={() =>
                                  void perform(async () => {
                                    const response = await sessionFetch(
                                      `/api/v1${libraryRoot}/${skill.id}/resource?${new URLSearchParams({ path: file.path })}`,
                                    );
                                    if (!response.ok)
                                      throw new Error("无法读取技能资源");
                                    setResourceText({
                                      packageId: skill.id,
                                      path: file.path,
                                      text: await response.text(),
                                    });
                                  })
                                }
                              >
                                查看 {file.path}
                              </button>
                            )}
                          <a
                            href={`/api/v1/skills/${skill.id}/resource?${new URLSearchParams({ path: file.path })}`}
                            download={file.path.split("/").at(-1)}
                          >
                            下载
                          </a>
                          {selected &&
                            file.path !== "SKILL.md" &&
                            !file.path.startsWith("scripts/") &&
                            /\.(md|txt|json|yaml|yml|csv)$/i.test(
                              file.path,
                            ) && (
                              <label className="check-label">
                                <input
                                  type="checkbox"
                                  aria-label={`加入指导 ${file.path}`}
                                  disabled={busy || stale}
                                  checked={selected.resources.includes(
                                    file.path,
                                  )}
                                  onChange={(event) =>
                                    update(
                                      chosen.map((value) =>
                                        value.package_id === skill.id
                                          ? {
                                              ...value,
                                              resources: event.target.checked
                                                ? [
                                                    ...value.resources,
                                                    file.path,
                                                  ]
                                                : value.resources.filter(
                                                    (path) =>
                                                      path !== file.path,
                                                  ),
                                            }
                                          : value,
                                      ),
                                    )
                                  }
                                />
                                加入模型指导
                              </label>
                            )}
                        </li>
                      ))}
                    </ul>
                  </details>
                  {resourceText?.packageId === skill.id && (
                    <section>
                      <h4>{resourceText.path}</h4>
                      <pre>{resourceText.text}</pre>
                    </section>
                  )}
                </>
              ) : (
                project && (
                  <SkillRunner
                    key={`${project.project_id}:${skill.id}:${stage}`}
                    project={project}
                    skill={skill}
                    stage={stage}
                    shotId={shotId}
                    busy={busy}
                    perform={perform}
                  />
                )
              )}
            </>
          )}
        </section>
      </div>
      {project && (
        <section className="guidance-approval" aria-label="项目技能配置">
          <p>
            {productionStages[stage]}已选 {chosen.length}{" "}
            项指导。配置用于后续制作请求，既有选片可继续使用。
          </p>
          {chosen
            .filter(
              (value) =>
                !library.data?.items.some(
                  (item) => item.id === value.package_id,
                ),
            )
            .map((value) => (
              <div key={value.package_id}>
                <small>
                  已固定技能{" "}
                  {project.production_guidance?.[stage]?.find(
                    (item) => item.package_id === value.package_id,
                  )?.name || value.package_id}
                  ，可按相同包哈希重新导入脚本资源。
                </small>
                <button
                  disabled={busy || stale}
                  onClick={() =>
                    update(
                      chosen.filter(
                        (item) => item.package_id !== value.package_id,
                      ),
                    )
                  }
                >
                  移除此项指导
                </button>
              </div>
            ))}
          {stale && (
            <p role="alert">工程已有新修订，请载入最新技能配置后重新选择。</p>
          )}
          {(draft || stale) && (
            <button
              disabled={busy}
              onClick={() => {
                setDraft(undefined);
                setProposal(undefined);
              }}
            >
              载入最新技能配置
            </button>
          )}
          {!proposal ? (
            <button
              disabled={busy || !draft || stale}
              onClick={() =>
                void perform(async () => {
                  const body = {
                    base_revision: draft!.revision,
                    selections: draft!.selections,
                  };
                  const preview = await api<Preview>(
                    `/projects/${project.project_id}/skills/guidance/preview`,
                    body,
                  );
                  setProposal({ body, preview });
                })
              }
            >
              预览技能配置
            </button>
          ) : (
            <>
              <pre>{JSON.stringify(proposal.preview.changes, null, 2)}</pre>
              <button
                className="primary"
                disabled={busy || stale}
                onClick={() =>
                  void perform(async () => {
                    await api(
                      `/projects/${project.project_id}/skills/guidance/commit`,
                      { ...proposal.body, approved: true },
                    );
                    setDraft(undefined);
                    setProposal(undefined);
                    setMessage("技能配置已保存，后续制作请求将使用所选指导。");
                  })
                }
              >
                批准技能配置
              </button>
              <button disabled={busy} onClick={() => setProposal(undefined)}>
                返回调整
              </button>
            </>
          )}
          <button
            disabled={busy}
            onClick={() =>
              void perform(() =>
                api(`/projects/${project.project_id}/skills/checks`, {
                  base_revision: project.revision,
                  stage,
                }),
              )
            }
          >
            运行本阶段制作检查
          </button>
        </section>
      )}
      {project && (
        <SkillRuns
          key={`${project.project_id}:${stage}`}
          project={project}
          stage={stage}
          busy={busy}
          perform={perform}
        />
      )}
    </section>
  );
}
