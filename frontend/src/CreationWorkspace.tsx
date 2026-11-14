import { useRef, useState, type PointerEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Film, GripHorizontal, Maximize2, Minus, Plus } from "lucide-react";
import { api, mediaUrl } from "./api";
import { framesToTime, statusLabel } from "./format";
import { useCursorPage } from "./history";
import { ImageUpload } from "./ImageUpload";
import type { AssetVersion, ChangeSet, Preview, Project, Shot } from "./types";

type Point = { x: number; y: number };
type Layout = {
  positions: Record<string, Point>;
  x: number;
  y: number;
  zoom: number;
};
type Props = {
  project: Project;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
  edit: (shot: Shot) => void;
  generate: (ids: string[]) => void;
  canGenerate: boolean;
};
const initial: Layout = { positions: {}, x: 36, y: 42, zoom: 1 };
function loadLayout(projectId: string): Layout {
  try {
    const value = JSON.parse(
      localStorage.getItem(`afds-canvas-${projectId}`) || "null",
    );
    if (
      value &&
      typeof value.positions === "object" &&
      value.positions &&
      [value.x, value.y, value.zoom].every(Number.isFinite) &&
      value.zoom >= 0.35 &&
      value.zoom <= 1.5
    ) {
      const positions: Record<string, Point> = {};
      for (const [id, point] of Object.entries(value.positions) as [
        string,
        Point,
      ][]) {
        if (point && Number.isFinite(point.x) && Number.isFinite(point.y))
          positions[id] = point;
      }
      return { ...value, positions };
    }
  } catch {
    /* A damaged layout can be rebuilt from the project. */
  }
  return initial;
}
export function CreationWorkspace(props: Props) {
  const { project, busy, generate, canGenerate } = props;
  const shots = project.shots.filter((shot) => shot.status !== "archived");
  const [selected, setSelected] = useState(shots[0]?.id || "");
  const shot = shots.find((s) => s.id === selected) || shots[0];
  const [mode, setMode] = useState<"canvas" | "grid">("canvas");
  const [layout, setLayout] = useState(() => loadLayout(project.project_id));
  const canvas = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id?: string; client: Point; origin: Point } | null>(
    null,
  );
  function save(value: Layout) {
    setLayout(value);
    try {
      localStorage.setItem(
        `afds-canvas-${project.project_id}`,
        JSON.stringify(value),
      );
    } catch {
      /* Layout remains usable for this session. */
    }
  }
  const position = (s: Shot, i: number) =>
    layout.positions[s.id] || { x: (i % 3) * 300, y: Math.floor(i / 3) * 360 };
  function begin(
    e: PointerEvent,
    id?: string,
    point = { x: layout.x, y: layout.y },
  ) {
    if (e.button !== 0 || mode !== "canvas") return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = {
      id,
      client: { x: e.clientX, y: e.clientY },
      origin: point,
    };
  }
  function move(e: PointerEvent) {
    if (!drag.current) return;
    const { id, client, origin } = drag.current;
    const scale = id ? layout.zoom : 1;
    const point = {
      x: origin.x + (e.clientX - client.x) / scale,
      y: origin.y + (e.clientY - client.y) / scale,
    };
    if (id)
      setLayout((previous) => ({
        ...previous,
        positions: { ...previous.positions, [id]: point },
      }));
    else setLayout((previous) => ({ ...previous, ...point }));
  }
  function end() {
    if (drag.current) {
      drag.current = null;
      save(layout);
    }
  }
  function fit() {
    const i = shots.findIndex((s) => s.id === shot?.id);
    const point = shot ? position(shot, i) : { x: 0, y: 0 };
    save({
      ...layout,
      x: 36 - point.x * layout.zoom,
      y: 42 - point.y * layout.zoom,
    });
  }
  return (
    <section className="creation-workspace">
      <div className="section-heading canvas-heading">
        <div>
          <h2>
            故事分镜 <span>{shots.length}</span>
          </h2>
          <p className="muted">组织镜头、参考与候选素材</p>
        </div>
        <div className="canvas-actions">
          <div className="segmented" aria-label="分镜视图">
            <button
              aria-pressed={mode === "canvas"}
              onClick={() => setMode("canvas")}
            >
              画布
            </button>
            <button
              aria-pressed={mode === "grid"}
              onClick={() => setMode("grid")}
            >
              网格
            </button>
          </div>
          <button
            disabled={busy || !shots.length || !canGenerate}
            onClick={() =>
              generate(shots.filter((s) => !s.locked).map((s) => s.id))
            }
          >
            <Plus size={15} />
            生成全部镜头
          </button>
        </div>
      </div>
      <div className={`creation-columns ${mode}`}>
        <div className="canvas-column">
          <div
            className={`shot-canvas ${mode}`}
            ref={canvas}
            aria-label="镜头创作画布"
            onPointerDown={(e) => {
              if (e.target === e.currentTarget) begin(e);
            }}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
          >
            {!shots.length && (
              <div className="canvas-empty">
                <Film size={32} />
                <h3>建立你的第一个镜头</h3>
                <p>展开导演助手，生成并批准分镜建议。</p>
              </div>
            )}
            <div
              className="canvas-world"
              style={
                mode === "canvas"
                  ? {
                      transform: `translate(${layout.x}px, ${layout.y}px) scale(${layout.zoom})`,
                    }
                  : undefined
              }
            >
              {shots.map((s, index) => {
                const version = project.asset_versions.find(
                  (v) => v.id === s.active_asset_version_id,
                );
                const point = position(s, index);
                return (
                  <article
                    data-testid="shot-card"
                    key={s.id}
                    className={`canvas-shot shot-card ${s.id === shot?.id ? "selected" : ""}`}
                    style={
                      mode === "canvas"
                        ? { left: point.x, top: point.y }
                        : undefined
                    }
                  >
                    <button
                      className="node-handle"
                      aria-label={`移动 ${s.title}`}
                      onPointerDown={(e) => begin(e, s.id, point)}
                      onKeyDown={(e) => {
                        const steps: Record<string, Point> = {
                          ArrowLeft: { x: -20, y: 0 },
                          ArrowRight: { x: 20, y: 0 },
                          ArrowUp: { x: 0, y: -20 },
                          ArrowDown: { x: 0, y: 20 },
                        };
                        if (mode === "canvas" && steps[e.key]) {
                          e.preventDefault();
                          save({
                            ...layout,
                            positions: {
                              ...layout.positions,
                              [s.id]: {
                                x: point.x + steps[e.key].x,
                                y: point.y + steps[e.key].y,
                              },
                            },
                          });
                        }
                      }}
                    >
                      <span>镜头 {String(s.order + 1).padStart(2, "0")}</span>
                      <GripHorizontal size={15} />
                    </button>
                    <button
                      className="node-select"
                      aria-label={`选择镜头 ${s.title}`}
                      aria-pressed={shot?.id === s.id}
                      onClick={() => setSelected(s.id)}
                    >
                      <div className="node-poster">
                        {version ? (
                          <img
                            draggable={false}
                            loading="lazy"
                            src={mediaUrl(
                              project.project_id,
                              version.poster_asset_id,
                            )}
                            alt={`${s.title} 素材预览`}
                          />
                        ) : (
                          <div className="node-placeholder">
                            <Film size={28} />
                            <span>等待生成素材</span>
                          </div>
                        )}
                        <span className="duration">
                          {framesToTime(s.duration_frames, project.format)}
                        </span>
                      </div>
                      <div className="node-description">
                        <strong>{s.title}</strong>
                        <span className={`status status-${s.status}`}>
                          {statusLabel[s.status] || s.status}
                        </span>
                        <p>{s.action}</p>
                      </div>
                    </button>
                    <footer className="node-footer">
                      <span>
                        {s.camera.shot_size} · {s.camera.movement}
                      </span>
                      <button
                        className="text-button"
                        aria-label="编辑镜头"
                        onClick={() => props.edit(s)}
                      >
                        编辑
                      </button>
                    </footer>
                    {!!Object.keys(s.reference_images || {}).length && (
                      <div className="node-references">
                        {Object.entries(s.reference_images || {}).map(
                          ([role, id]) => (
                            <img
                              key={role}
                              src={mediaUrl(project.project_id, id)}
                              alt={`${s.title} ${role}`}
                            />
                          ),
                        )}
                        <small>已关联参考图</small>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </div>
          {mode === "canvas" && (
            <div className="canvas-toolbar">
              <small>拖动空白平移 · 拖动卡片标题编排</small>
              <button
                aria-label="缩小画布"
                disabled={layout.zoom <= 0.35}
                onClick={() =>
                  save({
                    ...layout,
                    zoom: Math.max(0.35, +(layout.zoom - 0.1).toFixed(2)),
                  })
                }
              >
                <Minus size={15} />
              </button>
              <span aria-live="polite">{Math.round(layout.zoom * 100)}%</span>
              <button
                aria-label="放大画布"
                disabled={layout.zoom >= 1.5}
                onClick={() =>
                  save({
                    ...layout,
                    zoom: Math.min(1.5, +(layout.zoom + 0.1).toFixed(2)),
                  })
                }
              >
                <Plus size={15} />
              </button>
              <button aria-label="定位所选镜头" onClick={fit}>
                <Maximize2 size={15} />
              </button>
              <button onClick={() => save(initial)}>自动排列</button>
            </div>
          )}
          <label className="shot-jump">
            定位镜头
            <select
              value={shot?.id || ""}
              onChange={(e) => {
                setSelected(e.target.value);
                const index = shots.findIndex((s) => s.id === e.target.value);
                const point = position(shots[index], index);
                save({
                  ...layout,
                  x: 36 - point.x * layout.zoom,
                  y: 42 - point.y * layout.zoom,
                });
              }}
            >
              {shots.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.order + 1} · {s.title}
                </option>
              ))}
            </select>
          </label>
        </div>
        {shot && <ShotInspector key={shot.id} {...props} shot={shot} />}
      </div>
    </section>
  );
}

function ShotInspector({
  project,
  shot,
  busy,
  run,
  edit,
  generate,
  canGenerate,
}: Props & { shot: Shot }) {
  const versions = project.asset_versions.filter((v) => v.shot_id === shot.id);
  const [first, setFirst] = useState(
    shot.active_asset_version_id || versions.at(-1)?.id || "",
  );
  const [second, setSecond] = useState(
    versions.find((v) => v.id !== first)?.id || "",
  );
  const left = versions.find((v) => v.id === first) || versions.at(-1);
  const right = versions.find((v) => v.id === second);
  const videos = useRef<(HTMLVideoElement | null)[]>([]);
  const [syncError, setSyncError] = useState("");
  const [referenceImages, setReferenceImages] = useState(
    shot.reference_images || {},
  );
  const [body, setBody] = useState<ChangeSet>();
  const [preview, setPreview] = useState<Preview>();
  const [verdict, setVerdict] = useState("needs_work");
  const [scores, setScores] = useState({
    continuity: 3,
    action: 3,
    overall: 3,
  });
  const [note, setNote] = useState("");
  const [reportId, setReportId] = useState("");
  const root = `/projects/${project.project_id}`;
  const reports = useQuery({
    queryKey: ["reviews", project.project_id],
    queryFn: () =>
      api<
        {
          id: string;
          shot_id: string;
          asset_version_id: string;
          stale: boolean;
        }[]
      >(`${root}/reviews`),
  });
  const feedback = useCursorPage<{
    id: string;
    verdict: string;
    note: string;
    version_id: string;
    matches_current_selection: boolean;
    created_at: number;
  }>("feedback", project.project_id, `feedback?shot_id=${shot.id}`);
  async function propose(field: string, value: unknown) {
    const changes: ChangeSet = {
      base_revision: project.revision,
      approved: false,
      reason: "Creation workspace selection",
      operations: [
        { op: "set", entity_type: "shot", entity_id: shot.id, field, value },
      ],
    };
    const impact = await api<Preview>(`${root}/changesets/preview`, changes);
    setBody(changes);
    setPreview(impact);
  }
  function candidate(version: AssetVersion | undefined, index: number) {
    const value = index === 0 ? left?.id : right?.id;
    const asset = project.assets.find((a) => a.id === version?.asset_id);
    return (
      <div className="candidate">
        <label>
          候选 {index === 0 ? "A" : "B"}
          <select
            aria-label={`候选 ${index === 0 ? "A" : "B"}`}
            value={value || ""}
            onChange={(e) => {
              (index === 0 ? setFirst : setSecond)(e.target.value);
              setReportId("");
            }}
          >
            <option value="">请选择</option>
            {versions.map((v, i) => (
              <option key={v.id} value={v.id}>
                版本 {i + 1}
                {v.id === shot.active_asset_version_id
                  ? " · 当前选中"
                  : ""} · {v.status}
              </option>
            ))}
          </select>
        </label>
        {version ? (
          <>
            <video
              ref={(element) => {
                videos.current[index] = element;
              }}
              controls
              preload="metadata"
              playsInline
              src={mediaUrl(project.project_id, version.asset_id)}
              poster={mediaUrl(project.project_id, version.poster_asset_id)}
            />
            <small>
              {asset?.provenance.is_mock || asset?.provenance.contains_mock
                ? "DEMO / MOCK"
                : asset?.provenance.provider_id || "导入素材"}
            </small>
            <button
              disabled={
                busy ||
                shot.locked ||
                version.id === shot.active_asset_version_id ||
                version.status !== "ready"
              }
              onClick={() =>
                void run(() => propose("active_asset_version_id", version.id))
              }
            >
              预览选用候选 {index === 0 ? "A" : "B"}
            </button>
          </>
        ) : (
          <p className="muted">生成更多候选后可比较</p>
        )}
      </div>
    );
  }
  return (
    <aside className="shot-inspector panel" aria-label="镜头检查器">
      <header>
        <div>
          <small>
            镜头 {shot.order + 1}
            {shot.locked ? " · 已锁定" : ""}
          </small>
          <h2>{shot.title}</h2>
        </div>
        <button onClick={() => edit(shot)}>编辑镜头详情</button>
      </header>
      <div className="inspector-actions">
        <button
          className="primary"
          disabled={busy || shot.locked || !canGenerate}
          onClick={() => generate([shot.id])}
        >
          生成所选镜头
        </button>
        <p className="muted">
          修改镜头描述后可生成新候选。相同输入会复用已有结果。
        </p>
      </div>
      <h3>候选比较</h3>
      <div className="candidate-pair">
        {candidate(left, 0)}
        {candidate(right, 1)}
      </div>
      <div className="button-row">
        <button
          disabled={!left || !right}
          onClick={() =>
            void (async () => {
              setSyncError("");
              try {
                for (const video of videos.current)
                  if (video) video.currentTime = 0;
                await Promise.all(videos.current.map((video) => video?.play()));
              } catch {
                for (const video of videos.current) video?.pause();
                setSyncError("同步播放未启动，请使用播放器控件播放。");
              }
            })()
          }
        >
          从头同步播放
        </button>
        <button onClick={() => videos.current.forEach((v) => v?.pause())}>
          暂停比较
        </button>
      </div>
      {syncError && <p role="status">{syncError}</p>}
      <details className="reference-editor">
        <summary>参考图与首尾帧</summary>
        <p className="muted">
          生成时使用 ComfyUI 已映射的图像输入。修改会显示受影响镜头。
        </p>
        {(["reference_image", "start_image", "end_image"] as const).map(
          (role) => (
            <label key={role}>
              {
                {
                  reference_image: "参考图",
                  start_image: "首帧图",
                  end_image: "尾帧图",
                }[role]
              }
              <select
                aria-label={role}
                value={referenceImages[role] || ""}
                onChange={(e) => {
                  const value = { ...referenceImages };
                  if (e.target.value) value[role] = e.target.value;
                  else delete value[role];
                  setReferenceImages(value);
                  setPreview(undefined);
                }}
              >
                <option value="">未设置</option>
                {project.assets
                  .filter((a) => a.media_type === "image")
                  .map((a, i) => (
                    <option key={a.id} value={a.id}>
                      {a.provenance.name || `图像 ${i + 1}`}
                    </option>
                  ))}
              </select>
              {referenceImages[role] && (
                <img
                  className="reference-preview"
                  src={mediaUrl(project.project_id, referenceImages[role]!)}
                  alt={role}
                />
              )}
            </label>
          ),
        )}
        <button
          disabled={busy}
          onClick={() =>
            void run(() => propose("reference_images", referenceImages))
          }
        >
          预览参考图修改
        </button>
        <ImageUpload project={project} run={run} busy={busy} />
      </details>
      {preview && body && (
        <section className="change-preview" aria-label="创作修改预览">
          <h3>修改影响预览</h3>
          <p>
            受影响镜头 {preview.affected_shot_ids.length} 个 · 受保护镜头{" "}
            {preview.protected_shot_ids?.length || 0} 个
          </p>
          <p>
            {preview.render_invalidated
              ? "应用后需要重新渲染成片"
              : "成片状态有效"}
          </p>
          {body.base_revision !== project.revision && (
            <p role="alert">项目已有更新，请重新预览。</p>
          )}
          <button
            disabled={busy || body.base_revision !== project.revision}
            onClick={() =>
              void run(async () => {
                await api(`${root}/changesets/commit`, {
                  ...body,
                  approved: true,
                });
                setBody(undefined);
                setPreview(undefined);
              })
            }
          >
            批准创作修改
          </button>
          <button onClick={() => setPreview(undefined)}>关闭预览</button>
        </section>
      )}
      <details className="human-feedback">
        <summary>人工评价与迭代记录</summary>
        <p className="muted">评价绑定候选 A 的素材版本。</p>
        <label>
          评价结论
          <select
            aria-label="评价结论"
            value={verdict}
            onChange={(e) => setVerdict(e.target.value)}
          >
            <option value="needs_work">继续完善</option>
            <option value="accepted">认可</option>
            <option value="rejected">弃用</option>
          </select>
        </label>
        <div className="score-inputs">
          {(["continuity", "action", "overall"] as const).map((key) => (
            <label key={key}>
              {{ continuity: "连续性", action: "动作", overall: "综合" }[key]}
              <select
                aria-label={`${key} 评分`}
                value={scores[key]}
                onChange={(e) =>
                  setScores({ ...scores, [key]: Number(e.target.value) })
                }
              >
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
        <label>
          关联审片报告
          <select
            value={reportId}
            onChange={(e) => setReportId(e.target.value)}
          >
            <option value="">不关联</option>
            {reports.data
              ?.filter(
                (r) => r.shot_id === shot.id && r.asset_version_id === left?.id,
              )
              .map((r) => (
                <option value={r.id} key={r.id}>
                  {r.id.slice(0, 8)}
                  {r.stale ? " · 历史报告" : ""}
                </option>
              ))}
          </select>
        </label>
        <label>
          评价与改进建议
          <textarea
            aria-label="评价与改进建议"
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <button
          disabled={busy || !left}
          onClick={() =>
            void run(async () => {
              await api(`${root}/feedback`, {
                base_revision: project.revision,
                approved: true,
                shot_id: shot.id,
                version_id: left!.id,
                report_id: reportId || null,
                verdict,
                scores,
                note,
              });
              setNote("");
            })
          }
        >
          确认记录人工评价
        </button>
        {feedback.error && <p role="alert">{feedback.error.message}</p>}
        {feedback.data?.items.map((f) => (
          <article key={f.id} className="feedback-record">
            <strong>
              {{ accepted: "认可", rejected: "弃用", needs_work: "继续完善" }[
                f.verdict
              ] || f.verdict}
            </strong>
            <small>
              {f.matches_current_selection
                ? "对应当前选中素材"
                : "历史候选评价"}{" "}
              · {new Date(f.created_at * 1000).toLocaleString()}
            </small>
            <p>{f.note}</p>
          </article>
        ))}
        {feedback.pagination}
      </details>
    </aside>
  );
}
