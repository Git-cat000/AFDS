import { useState } from "react";
import { api } from "./api";
import "./continuity.css";

type Bible = {
  id: string;
  name: string;
  invariant_traits?: string[];
  spatial_notes?: string;
  reference_asset_ids: string[];
};
type CharacterState = {
  character_id: string;
  costume: string;
  pose: string;
  emotion: string;
};
type PropState = {
  name: string;
  description: string;
  holder_character_id: string | null;
};
type WorldState = {
  id: string;
  scene_id: string;
  shot_id?: string | null;
  characters: CharacterState[];
  props: PropState[];
  environment: Record<string, string>;
};
type Link = {
  id: string;
  source_shot_id: string;
  target_shot_id: string;
  kind: string;
  note: string;
};
type Shot = {
  id: string;
  scene_id: string;
  title: string;
  action: string;
  emotion: string;
  duration_frames: number;
  camera: { shot_size: string; movement: string };
  character_ids?: string[];
  input_state_ref?: string | null;
  output_state_ref?: string | null;
};
type Project = {
  project_id: string;
  revision: number;
  shots: Shot[];
  scenes?: { id: string; title: string; location_id?: string | null }[];
  characters?: Bible[];
  locations?: Bible[];
  world_states?: WorldState[];
  continuity_links?: Link[];
  assets: { id: string; media_type: string }[];
};
type Change = {
  entity_type: string;
  entity_id?: string;
  delete?: boolean;
  value?: unknown;
};
type Plan = {
  id: string;
  type: string;
  base_revision: number;
  state?: string;
  affected_shot_ids: string[];
  protected_shot_ids: string[];
  explanation?: string;
  changes: {
    entity_id?: string;
    field: string;
    before: unknown;
    after: unknown;
  }[];
};
type PreviewChange = (change: Change) => Promise<void>;
const lines = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

function describe(value: unknown): string {
  if (value === null || value === undefined) return "无";
  if (Array.isArray(value)) return value.map(describe).join("；") || "无";
  if (typeof value === "object")
    return Object.entries(value)
      .filter(([key]) => !key.endsWith("_id") && key !== "id")
      .map(([key, entry]) => `${key}: ${describe(entry)}`)
      .join("；");
  return String(value);
}

export function ContinuityPanel({
  project,
  run,
  busy,
}: {
  project: Project;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const [entityType, setEntityType] = useState<"character" | "location">(
    "character",
  );
  const [entityId, setEntityId] = useState("");
  const [shotId, setShotId] = useState("");
  const [stateId, setStateId] = useState("");
  const [linkId, setLinkId] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [history, setHistory] = useState<Plan[]>([]);
  const root = `/projects/${project.project_id}`;
  const entities =
    (entityType === "character" ? project.characters : project.locations) || [];
  const shot = project.shots.find((s) => s.id === shotId) || project.shots[0];
  const preview: PreviewChange = async (change) =>
    run(async () =>
      setPlan(
        await api<Plan>(`${root}/continuity/preview`, {
          base_revision: project.revision,
          ...change,
        }),
      ),
    );
  const title = (id: string) =>
    project.shots.find((s) => s.id === id)?.title || id;
  const stale = plan !== null && plan.base_revision !== project.revision;
  return (
    <section className="continuity-panel">
      <h2>设定与连续性</h2>
      <p className="muted">
        先检查影响范围，再批准变更。已锁定或批准的镜头保留选中素材并标记待审。
      </p>
      <div className="continuity-columns">
        <article className="panel">
          <h3>角色与地点设定</h3>
          <div className="editor-grid">
            <label>
              设定类型
              <select
                value={entityType}
                onChange={(e) => {
                  setEntityType(e.target.value as "character" | "location");
                  setEntityId("");
                }}
              >
                <option value="character">角色</option>
                <option value="location">地点</option>
              </select>
            </label>
            <label>
              选择设定
              <select
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
              >
                <option value="">新建设定</option>
                {entities.map((entity) => (
                  <option value={entity.id} key={entity.id}>
                    {entity.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <BibleEditor
            key={`${entityType}-${entityId}-${project.revision}`}
            type={entityType}
            entity={entities.find((e) => e.id === entityId)}
            project={project}
            preview={preview}
            busy={busy}
          />
        </article>
        <article className="panel">
          <h3>镜头引用</h3>
          <label>
            选择连续性镜头
            <select
              value={shot?.id || ""}
              onChange={(e) => {
                setShotId(e.target.value);
                setStateId("");
              }}
            >
              <option value="" disabled>
                请选择镜头
              </option>
              {project.shots.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
          {shot ? (
            <ShotReferences
              key={`${shot.id}-${project.revision}`}
              project={project}
              shot={shot}
              preview={preview}
              busy={busy}
            />
          ) : (
            <p className="muted">请先创建分镜。</p>
          )}
        </article>
        <article className="panel">
          <h3>世界状态</h3>
          <label>
            编辑状态
            <select
              value={stateId}
              onChange={(e) => setStateId(e.target.value)}
            >
              <option value="">新建状态</option>
              {(project.world_states || []).map((state, index) => (
                <option value={state.id} key={state.id}>
                  状态 {index + 1} ·{" "}
                  {state.shot_id ? title(state.shot_id) : "场景共享"}
                </option>
              ))}
            </select>
          </label>
          {(project.scenes || []).length > 0 ? (
            <WorldStateEditor
              key={`${stateId}-${shot?.id}-${project.revision}`}
              project={project}
              shot={shot}
              state={project.world_states?.find((s) => s.id === stateId)}
              preview={preview}
              busy={busy}
            />
          ) : (
            <p className="muted">请先创建场景。</p>
          )}
        </article>
        <article className="panel">
          <h3>跨镜头连续性</h3>
          <label>
            选择连续性关系
            <select value={linkId} onChange={(e) => setLinkId(e.target.value)}>
              <option value="">新建关系</option>
              {(project.continuity_links || []).map((link) => (
                <option value={link.id} key={link.id}>
                  {title(link.source_shot_id)} → {title(link.target_shot_id)}
                </option>
              ))}
            </select>
          </label>
          <LinkEditor
            key={`${linkId}-${project.revision}`}
            project={project}
            link={project.continuity_links?.find((l) => l.id === linkId)}
            preview={preview}
            busy={busy}
          />
        </article>
      </div>
      <SemanticEditor
        key={project.project_id}
        project={project}
        busy={busy}
        submit={(body) =>
          run(async () =>
            setPlan(
              await api<Plan>(`${root}/semantic-edits`, {
                base_revision: project.revision,
                ...body,
              }),
            ),
          )
        }
      />
      <div className="button-row continuity-history">
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const plans = await api<Plan[]>(`${root}/plans`);
              setHistory(
                plans.filter(
                  (p) =>
                    ["continuity", "semantic_edit"].includes(p.type) &&
                    p.state === "proposed",
                ),
              );
            })
          }
        >
          恢复待批准方案
        </button>
        {history.map((p) => (
          <button key={p.id} onClick={() => setPlan(p)}>
            {p.type === "semantic_edit" ? "镜头修改" : "连续性修改"} · 版本{" "}
            {p.base_revision}
          </button>
        ))}
      </div>
      {plan && (
        <article className="panel continuity-preview" aria-label="变更影响预览">
          <h3>变更影响预览</h3>
          {plan.explanation && <p>{plan.explanation}</p>}
          <p>
            影响镜头：
            {plan.affected_shot_ids.map(title).join("、") || "无媒体变更"}
          </p>
          <p>
            保留已选素材并标记待审：
            {plan.protected_shot_ids.map(title).join("、") || "无"}
          </p>
          <ul>
            {plan.changes.map((change, index) => (
              <li key={index}>
                <strong>
                  {change.entity_id ? title(change.entity_id) : "设定"} ·{" "}
                  {change.field}
                </strong>
                <div className="continuity-diff">
                  {describe(change.before)} → {describe(change.after)}
                </div>
              </li>
            ))}
          </ul>
          {stale && <p role="alert">项目已有更新，请重新预览。</p>}
          <div className="button-row">
            <button
              className="primary"
              disabled={busy || stale}
              onClick={() =>
                void run(async () => {
                  const endpoint =
                    plan.type === "semantic_edit"
                      ? `semantic-edits/${plan.id}/execute`
                      : `continuity/plans/${plan.id}/execute`;
                  await api(`${root}/${endpoint}`, {
                    base_revision: project.revision,
                    approved: true,
                  });
                  setPlan(null);
                  setHistory((items) => items.filter((p) => p.id !== plan.id));
                })
              }
            >
              批准并应用
            </button>
            <button disabled={busy} onClick={() => setPlan(null)}>
              关闭预览
            </button>
          </div>
        </article>
      )}
    </section>
  );
}

function BibleEditor({
  type,
  entity,
  project,
  preview,
  busy,
}: {
  type: "character" | "location";
  entity?: Bible;
  project: Project;
  preview: PreviewChange;
  busy: boolean;
}) {
  const [name, setName] = useState(entity?.name || "");
  const [traits, setTraits] = useState(
    (entity?.invariant_traits || []).join("\n"),
  );
  const [notes, setNotes] = useState(entity?.spatial_notes || "");
  const [refs, setRefs] = useState(entity?.reference_asset_ids || []);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void preview({
          entity_type: type,
          entity_id: entity?.id,
          value: {
            name,
            reference_asset_ids: refs,
            ...(type === "character"
              ? { invariant_traits: lines(traits) }
              : { spatial_notes: notes }),
          },
        });
      }}
    >
      <label>
        名称
        <input
          maxLength={200}
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      {type === "character" ? (
        <label>
          不变特征（每行一项）
          <textarea
            maxLength={12000}
            value={traits}
            onChange={(e) => setTraits(e.target.value)}
          />
        </label>
      ) : (
        <label>
          空间与环境设定
          <textarea
            maxLength={5000}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </label>
      )}
      <label>
        参考素材
        <select
          multiple
          aria-label="设定参考素材"
          value={refs}
          onChange={(e) =>
            setRefs(
              Array.from(e.target.selectedOptions, (option) => option.value),
            )
          }
        >
          {project.assets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.media_type} · {asset.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      <div className="button-row">
        <button disabled={busy || !name.trim()}>预览设定变更</button>
        {entity && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void preview({
                entity_type: type,
                entity_id: entity.id,
                delete: true,
              })
            }
          >
            预览删除设定
          </button>
        )}
      </div>
    </form>
  );
}

function ShotReferences({
  project,
  shot,
  preview,
  busy,
}: {
  project: Project;
  shot: Shot;
  preview: PreviewChange;
  busy: boolean;
}) {
  const [characters, setCharacters] = useState(shot.character_ids || []);
  const [input, setInput] = useState(shot.input_state_ref || "");
  const [output, setOutput] = useState(shot.output_state_ref || "");
  const scene = project.scenes?.find((s) => s.id === shot.scene_id);
  const [location, setLocation] = useState(scene?.location_id || "");
  const states = (project.world_states || []).filter(
    (s) => s.scene_id === shot.scene_id,
  );
  return (
    <>
      <fieldset className="continuity-choices">
        <legend>出场角色</legend>
        {(project.characters || []).map((character) => (
          <label className="check-label" key={character.id}>
            <input
              type="checkbox"
              checked={characters.includes(character.id)}
              onChange={(e) =>
                setCharacters(
                  e.target.checked
                    ? [...characters, character.id]
                    : characters.filter((id) => id !== character.id),
                )
              }
            />
            {character.name}
          </label>
        ))}
      </fieldset>
      <div className="editor-grid">
        {(
          [
            ["输入状态", input, setInput, states],
            [
              "输出状态",
              output,
              setOutput,
              states.filter((s) => !s.shot_id || s.shot_id === shot.id),
            ],
          ] as const
        ).map(([label, value, setter, options]) => (
          <label key={label}>
            {label}
            <select value={value} onChange={(e) => setter(e.target.value)}>
              <option value="">无</option>
              {options.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.shot_id
                    ? project.shots.find((item) => item.id === s.shot_id)?.title
                    : "场景共享"}{" "}
                  · {s.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <button
        disabled={busy}
        onClick={() =>
          void preview({
            entity_type: "shot_refs",
            entity_id: shot.id,
            value: {
              character_ids: characters,
              input_state_ref: input || null,
              output_state_ref: output || null,
            },
          })
        }
      >
        预览镜头引用
      </button>
      <label>
        场景地点
        <select value={location} onChange={(e) => setLocation(e.target.value)}>
          <option value="">无地点</option>
          {(project.locations || []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={busy || !scene}
        onClick={() =>
          void preview({
            entity_type: "scene_location",
            entity_id: shot.scene_id,
            value: { location_id: location || null },
          })
        }
      >
        预览场景地点
      </button>
    </>
  );
}

function WorldStateEditor({
  project,
  state,
  shot,
  preview,
  busy,
}: {
  project: Project;
  state?: WorldState;
  shot?: Shot;
  preview: PreviewChange;
  busy: boolean;
}) {
  const [sceneId, setSceneId] = useState(
    state?.scene_id || shot?.scene_id || project.scenes?.[0]?.id || "",
  );
  const [owner, setOwner] = useState(state?.shot_id || "");
  const [characters, setCharacters] = useState<CharacterState[]>(
    state?.characters || [],
  );
  const [props, setProps] = useState<PropState[]>(state?.props || []);
  const [environment, setEnvironment] = useState(
    Object.entries(state?.environment || {}),
  );
  const updateCharacter = (id: string, field: string, value: string) =>
    setCharacters((items) =>
      items.map((c) => (c.character_id === id ? { ...c, [field]: value } : c)),
    );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void preview({
          entity_type: "world_state",
          entity_id: state?.id,
          value: {
            scene_id: sceneId,
            shot_id: owner || null,
            characters,
            props,
            environment: Object.fromEntries(
              environment.filter(([key]) => key.trim()),
            ),
          },
        });
      }}
    >
      <div className="editor-grid">
        <label>
          所属场景
          <select
            value={sceneId}
            onChange={(e) => {
              setSceneId(e.target.value);
              setOwner("");
            }}
          >
            {project.scenes?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          所属镜头
          <select value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">场景共享状态</option>
            {project.shots
              .filter((s) => s.scene_id === sceneId)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
          </select>
        </label>
      </div>
      <fieldset className="continuity-choices">
        <legend>角色状态</legend>
        {(project.characters || []).map((character) => {
          const current = characters.find(
            (c) => c.character_id === character.id,
          );
          return (
            <div key={character.id}>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={!!current}
                  onChange={(e) =>
                    setCharacters(
                      e.target.checked
                        ? [
                            ...characters,
                            {
                              character_id: character.id,
                              costume: "",
                              pose: "",
                              emotion: "",
                            },
                          ]
                        : characters.filter(
                            (c) => c.character_id !== character.id,
                          ),
                    )
                  }
                />
                {character.name}
              </label>
              {current && (
                <div className="continuity-fields">
                  {(
                    [
                      ["costume", "服装"],
                      ["pose", "姿态"],
                      ["emotion", "情绪"],
                    ] as const
                  ).map(([field, label]) => (
                    <label key={field}>
                      {character.name} · {label}
                      <input
                        maxLength={1000}
                        value={current[field] || ""}
                        onChange={(e) =>
                          updateCharacter(character.id, field, e.target.value)
                        }
                      />
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </fieldset>
      <fieldset className="continuity-choices">
        <legend>道具</legend>
        {props.map((prop, index) => (
          <div className="continuity-row" key={index}>
            <label>
              道具名称
              <input
                required
                maxLength={200}
                value={prop.name}
                onChange={(e) =>
                  setProps((items) =>
                    items.map((p, i) =>
                      i === index ? { ...p, name: e.target.value } : p,
                    ),
                  )
                }
              />
            </label>
            <label>
              道具状态
              <input
                maxLength={1000}
                value={prop.description}
                onChange={(e) =>
                  setProps((items) =>
                    items.map((p, i) =>
                      i === index ? { ...p, description: e.target.value } : p,
                    ),
                  )
                }
              />
            </label>
            <label>
              持有者
              <select
                value={prop.holder_character_id || ""}
                onChange={(e) =>
                  setProps((items) =>
                    items.map((p, i) =>
                      i === index
                        ? { ...p, holder_character_id: e.target.value || null }
                        : p,
                    ),
                  )
                }
              >
                <option value="">无</option>
                {project.characters?.map((c) => (
                  <option value={c.id} key={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() =>
                setProps((items) => items.filter((_, i) => i !== index))
              }
            >
              移除
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={props.length >= 40}
          onClick={() =>
            setProps((items) => [
              ...items,
              { name: "", description: "", holder_character_id: null },
            ])
          }
        >
          添加道具
        </button>
      </fieldset>
      <fieldset className="continuity-choices">
        <legend>环境状态</legend>
        {environment.map(([key, value], index) => (
          <div className="continuity-row" key={index}>
            <label>
              环境属性
              <input
                required
                maxLength={100}
                value={key}
                onChange={(e) =>
                  setEnvironment((items) =>
                    items.map((item, i) =>
                      i === index ? [e.target.value, item[1]] : item,
                    ),
                  )
                }
              />
            </label>
            <label>
              环境值
              <input
                maxLength={1000}
                value={value}
                onChange={(e) =>
                  setEnvironment((items) =>
                    items.map((item, i) =>
                      i === index ? [item[0], e.target.value] : item,
                    ),
                  )
                }
              />
            </label>
            <button
              type="button"
              onClick={() =>
                setEnvironment((items) => items.filter((_, i) => i !== index))
              }
            >
              移除
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={environment.length >= 30}
          onClick={() => setEnvironment((items) => [...items, ["", ""]])}
        >
          添加环境属性
        </button>
      </fieldset>
      <div className="button-row">
        <button disabled={busy || !sceneId}>预览世界状态</button>
        {state && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void preview({
                entity_type: "world_state",
                entity_id: state.id,
                delete: true,
              })
            }
          >
            预览删除状态
          </button>
        )}
      </div>
    </form>
  );
}

function LinkEditor({
  project,
  link,
  preview,
  busy,
}: {
  project: Project;
  link?: Link;
  preview: PreviewChange;
  busy: boolean;
}) {
  const [source, setSource] = useState(
    link?.source_shot_id || project.shots[0]?.id || "",
  );
  const [target, setTarget] = useState(
    link?.target_shot_id || project.shots[1]?.id || "",
  );
  const [kind, setKind] = useState(link?.kind || "continuity");
  const [note, setNote] = useState(link?.note || "");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void preview({
          entity_type: "link",
          entity_id: link?.id,
          value: { source_shot_id: source, target_shot_id: target, kind, note },
        });
      }}
    >
      <div className="editor-grid">
        {(
          [
            ["来源镜头", source, setSource],
            ["延续镜头", target, setTarget],
          ] as const
        ).map(([label, value, setter]) => (
          <label key={label}>
            {label}
            <select
              required
              value={value}
              onChange={(e) => setter(e.target.value)}
            >
              <option value="" disabled>
                请选择
              </option>
              {project.shots.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <label>
        关系类型
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {[
            ["continuity", "综合连续性"],
            ["character", "角色"],
            ["prop", "道具"],
            ["environment", "环境"],
            ["action", "动作"],
          ].map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        连续性说明
        <textarea
          maxLength={1000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="button-row">
        <button disabled={busy || !source || !target || source === target}>
          预览连续性关系
        </button>
        {link && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void preview({
                entity_type: "link",
                entity_id: link.id,
                delete: true,
              })
            }
          >
            预览删除关系
          </button>
        )}
      </div>
    </form>
  );
}

function SemanticEditor({
  project,
  busy,
  submit,
}: {
  project: Project;
  busy: boolean;
  submit: (body: Record<string, unknown>) => Promise<void>;
}) {
  const [selected, setSelected] = useState(
    project.shots[0] ? [project.shots[0].id] : [],
  );
  const [mode, setMode] = useState("structured");
  const [field, setField] = useState("emotion");
  const [text, setText] = useState("");
  const [frames, setFrames] = useState(120);
  const [size, setSize] = useState("medium");
  const [movement, setMovement] = useState("static");
  const [message, setMessage] = useState("");
  const [consent, setConsent] = useState(false);
  return (
    <article className="panel continuity-semantic">
      <h3>受控镜头修改</h3>
      <p className="muted">
        确定性字段编辑直接使用填写值；自然语言指令需要已配置的
        LLM，生成后仍须批准。
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value =
            field === "camera"
              ? { shot_size: size, movement }
              : field === "duration_frames"
                ? frames
                : text;
          void submit({
            shot_ids: selected,
            mode,
            ...(mode === "structured"
              ? { fields: { [field]: value } }
              : { message, allow_remote: consent }),
          });
        }}
      >
        <fieldset className="continuity-choices">
          <legend>仅修改所选镜头（最多20个）</legend>
          {project.shots.map((shot) => (
            <label key={shot.id} className="check-label">
              <input
                type="checkbox"
                checked={selected.includes(shot.id)}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, shot.id]
                      : selected.filter((id) => id !== shot.id),
                  )
                }
              />
              {shot.title}
            </label>
          ))}
        </fieldset>
        <label>
          编辑方式
          <select
            value={mode}
            onChange={(e) => {
              setMode(e.target.value);
              setConsent(false);
            }}
          >
            <option value="structured">确定性字段编辑</option>
            <option value="llm">LLM 指令提案</option>
          </select>
        </label>
        {mode === "structured" ? (
          <>
            <label>
              修改字段
              <select
                value={field}
                onChange={(e) => {
                  setField(e.target.value);
                  setText("");
                }}
              >
                {[
                  ["emotion", "情绪"],
                  ["action", "动作"],
                  ["camera", "摄影机"],
                  ["duration_frames", "时长（帧）"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {field === "camera" ? (
              <div className="editor-grid">
                <label>
                  景别
                  <select
                    value={size}
                    onChange={(e) => setSize(e.target.value)}
                  >
                    {[
                      ["wide", "全景"],
                      ["medium", "中景"],
                      ["close_up", "近景"],
                      ["detail", "特写"],
                    ].map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  运镜
                  <input
                    maxLength={200}
                    value={movement}
                    onChange={(e) => setMovement(e.target.value)}
                  />
                </label>
              </div>
            ) : field === "duration_frames" ? (
              <label>
                新时长（帧）
                <input
                  type="number"
                  min={1}
                  max={1800}
                  required
                  value={frames}
                  onChange={(e) => setFrames(Number(e.target.value))}
                />
              </label>
            ) : (
              <label>
                新的字段值
                <textarea
                  maxLength={field === "emotion" ? 500 : 5000}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </label>
            )}
          </>
        ) : (
          <>
            <label>
              修改指令
              <textarea
                maxLength={5000}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
              />
            </label>
            <label className="check-label">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              允许发送所选镜头与相关连续性设定，并承担服务商费用
            </label>
          </>
        )}
        <button
          disabled={
            busy ||
            selected.length === 0 ||
            selected.length > 20 ||
            (mode === "llm" && (!consent || !message.trim()))
          }
        >
          预览镜头修改
        </button>
      </form>
    </article>
  );
}
