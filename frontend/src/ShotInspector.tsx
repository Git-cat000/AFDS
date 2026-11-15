import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, mediaUrl } from "./api";
import { useCursorPage } from "./history";
import { ImageUpload } from "./ImageUpload";
import type { AssetVersion, ChangeSet, Preview, Project, Shot } from "./types";

export type ShotInspectorProps = {
  project: Project;
  shot: Shot;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
  edit: (shot: Shot) => void;
  generate: (ids: string[]) => void;
  canGenerate: boolean;
  openTool?: (tool: "assets" | "review" | "timeline") => void;
};

export function ShotInspector({
  project,
  shot,
  busy,
  run,
  edit,
  generate,
  canGenerate,
  openTool,
}: ShotInspectorProps) {
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
  const [referenceDraft, setReferenceDraft] = useState<{
    images: NonNullable<Shot["reference_images"]>;
    revision: number;
  } | null>(null);
  const referenceImages = referenceDraft?.images || shot.reference_images || {};
  const staleReferences =
    referenceDraft !== null && referenceDraft.revision !== project.revision;
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
  async function propose(
    field: string,
    value: unknown,
    baseRevision = project.revision,
  ) {
    const changes: ChangeSet = {
      base_revision: baseRevision,
      // The Engine validates protected edits during read-only preview too.
      // Only the explicit approval button below sends the commit request.
      approved: true,
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
        {openTool && (
          <div className="shot-handoffs">
            <button onClick={() => openTool("assets")}>打开参考素材</button>
            <button onClick={() => openTool("review")}>审看所选镜头</button>
            <button onClick={() => openTool("timeline")}>编辑镜头音画</button>
          </div>
        )}
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
                  setReferenceDraft({
                    images: value,
                    revision: referenceDraft?.revision ?? project.revision,
                  });
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
        {staleReferences && (
          <p role="alert">参考图草稿基于旧版本，请载入当前设置后重新编辑。</p>
        )}
        {referenceDraft && (
          <button
            onClick={() => {
              setReferenceDraft(null);
              setPreview(undefined);
            }}
          >
            载入当前参考图设置
          </button>
        )}
        <button
          disabled={busy || staleReferences}
          onClick={() =>
            void run(() =>
              propose(
                "reference_images",
                referenceImages,
                referenceDraft?.revision ?? project.revision,
              ),
            )
          }
        >
          预览参考图修改
        </button>
        <ImageUpload project={project} run={run} busy={busy} />
      </details>
      {preview && body && (
        <section className="change-preview" aria-label="创作修改预览">
          <h3>修改影响预览</h3>
          {preview.changes.map((change, index) => (
            <div className="diff" key={index}>
              <strong>{change.field}</strong>
              <pre>
                {JSON.stringify(change.before, null, 2)} →{" "}
                {JSON.stringify(change.after, null, 2)}
              </pre>
            </div>
          ))}
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
                setReferenceDraft(null);
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
