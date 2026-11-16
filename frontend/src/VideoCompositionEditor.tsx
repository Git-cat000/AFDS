import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { CompositionPreview } from "./CompositionPreview";
import { VideoImport } from "./VideoImport";
import type { Composition, EditorProject } from "./TimelineEditor";
import type { VideoLayerClip, VideoLayerTrack, VideoTransition } from "./types";

type VideoDraft = {
  video_layers: VideoLayerTrack[];
  transitions: VideoTransition[];
};
type EditPreview = {
  preview_token: string;
  total_frames: number;
  previous_total_frames: number;
  can_apply: boolean;
  overflows: {
    kind: string;
    track_id?: string;
    index?: number;
    before_frames?: number;
    after_frames?: number;
  }[];
};
function draftOf(project: EditorProject): VideoDraft {
  const timeline = project.timeline as Composition;
  return structuredClone({
    video_layers: timeline.video_layers || [],
    transitions: timeline.transitions || [],
  });
}

export function VideoCompositionEditor({
  project,
  run,
  busy,
}: {
  project: EditorProject;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const timeline = project.timeline as Composition;
  const clips =
    timeline.tracks[0]?.type === "video" ? timeline.tracks[0].items : [];
  const totalFrames = clips.reduce(
    (end, clip) => Math.max(end, clip.start_frame + clip.duration_frames),
    0,
  );
  const [draft, setDraft] = useState(() => draftOf(project));
  const [base, setBase] = useState(project.revision);
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<EditPreview | null>(null);
  const [trim, setTrim] = useState(false);
  const [trackId, setTrackId] = useState(draft.video_layers[0]?.id || "");
  const materials = project.assets.filter((asset) =>
    ["image", "video"].includes(asset.media_type),
  );
  const [assetId, setAssetId] = useState(materials[0]?.id || "");
  const loaded = useRef(project.revision);
  useEffect(() => {
    if (!dirty && project.revision > loaded.current) {
      loaded.current = project.revision;
      setDraft(draftOf(project));
      setBase(project.revision);
      setPreview(null);
    }
  }, [dirty, project]);
  const selectedTrack =
    draft.video_layers.find((track) => track.id === trackId) ||
    draft.video_layers[0];
  const selectedAsset =
    materials.find((asset) => asset.id === assetId) || materials[0];
  const transitionsChanged =
    JSON.stringify(draft.transitions) !==
    JSON.stringify(timeline.transitions || []);

  function edit(operation: (value: VideoDraft) => VideoDraft) {
    if (!dirty) setBase(project.revision);
    setDraft(operation);
    setDirty(true);
    setPreview(null);
  }
  function updateTrack(id: string, patch: Partial<VideoLayerTrack>) {
    edit((value) => ({
      ...value,
      video_layers: value.video_layers.map((track) =>
        track.id === id ? { ...track, ...patch } : track,
      ),
    }));
  }
  function updateClip(
    track: VideoLayerTrack,
    id: string,
    patch: Partial<VideoLayerClip>,
  ) {
    updateTrack(track.id, {
      items: track.items.map((item) =>
        item.id === id ? { ...item, ...patch } : item,
      ),
    });
  }
  function addClip(
    track: VideoLayerTrack,
    asset: EditorProject["assets"][number],
  ) {
    const sourceFrames = asset.duration_seconds
      ? Math.floor(
          (asset.duration_seconds * project.format.fps_num) /
            project.format.fps_den,
        )
      : totalFrames;
    updateTrack(track.id, {
      items: [
        ...track.items,
        {
          id: crypto.randomUUID(),
          asset_id: asset.id,
          start_frame: 0,
          source_start_frame: 0,
          duration_frames: Math.max(1, Math.min(48, totalFrames, sourceFrames)),
          x: 0.65,
          y: 0.65,
          width: 0.3,
          height: 0.3,
          opacity: 1,
        },
      ],
    });
  }
  function request() {
    return {
      base_revision: base,
      ...draft,
      audio_tracks: timeline.tracks.filter((track) => track.type === "audio"),
      captions: timeline.captions || [],
      markers: timeline.markers || [],
      trim_overflow: trim,
    };
  }
  async function save(approved = false) {
    const saved = await api<EditorProject>(
      `/projects/${project.project_id}/timeline/composition-v2`,
      {
        ...request(),
        approved,
        preview_token: approved ? preview?.preview_token : undefined,
      },
      "PUT",
    );
    setDraft(draftOf(saved));
    setBase(saved.revision);
    loaded.current = saved.revision;
    setDirty(false);
    setPreview(null);
  }
  function transition(index: number, kind: string) {
    const first = clips[index],
      next = clips[index + 1];
    if (!first.id || !next.id) return;
    const previous = draft.transitions.find(
      (item) => item.from_clip_id === first.id && item.to_clip_id === next.id,
    );
    edit((value) => ({
      ...value,
      transitions: [
        ...value.transitions.filter(
          (item) =>
            !(item.from_clip_id === first.id && item.to_clip_id === next.id),
        ),
        ...(kind
          ? [
              {
                id: previous?.id || crypto.randomUUID(),
                from_clip_id: first.id!,
                to_clip_id: next.id!,
                kind: kind as VideoTransition["kind"],
                duration_frames:
                  previous?.duration_frames ||
                  Math.min(
                    12,
                    Math.floor(
                      Math.min(first.duration_frames, next.duration_frames) / 2,
                    ),
                  ),
              },
            ]
          : []),
      ],
    }));
  }
  return (
    <section className="video-composition" aria-label="视频合成编辑器">
      <h2>视频合成</h2>
      <VideoImport project={project} run={run} busy={busy} />
      <p className="muted">叠加轨按列表顺序覆盖。位置与尺寸使用画幅百分比。</p>
      {dirty && project.revision > base && (
        <p role="status">
          工程已更新，当前草稿基于修订 {base}。可载入最新剪辑后继续编辑。
        </p>
      )}
      <div className="composition-actions">
        <button
          disabled={busy || draft.video_layers.length >= 7}
          onClick={() => {
            const id = `video-${crypto.randomUUID()}`;
            edit((value) => ({
              ...value,
              video_layers: [
                ...value.video_layers,
                {
                  id,
                  name: `叠加轨 ${value.video_layers.length + 1}`,
                  hidden: false,
                  items: [],
                },
              ],
            }));
            setTrackId(id);
          }}
        >
          添加视频轨
        </button>
        <button
          disabled={busy || !dirty || transitionsChanged}
          onClick={() => void run(() => save())}
        >
          保存视频轨
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const latest = await api<EditorProject>(
                `/projects/${project.project_id}/apr`,
              );
              setDraft(draftOf(latest));
              setBase(latest.revision);
              loaded.current = latest.revision;
              setDirty(false);
              setPreview(null);
            })
          }
        >
          载入最新剪辑
        </button>
      </div>
      {draft.video_layers.length > 0 && (
        <div className="composition-fields">
          <label>
            视频目标轨
            <select
              value={selectedTrack?.id || ""}
              onChange={(event) => setTrackId(event.target.value)}
            >
              {draft.video_layers.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.name || track.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            叠加素材
            <select
              value={selectedAsset?.id || ""}
              onChange={(event) => setAssetId(event.target.value)}
            >
              {materials.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.provenance?.name ||
                    `${asset.media_type} ${asset.id.slice(0, 8)}`}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={busy || !selectedTrack || !selectedAsset || !totalFrames}
            onClick={() => addClip(selectedTrack, selectedAsset)}
          >
            添加叠加片段
          </button>
        </div>
      )}
      {draft.video_layers.map((track, trackIndex) => (
        <fieldset
          key={track.id}
          className="video-layer-track"
          onDragOver={(event) => {
            if (
              !busy &&
              event.dataTransfer.types.includes("application/x-afds-asset")
            ) {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
            }
          }}
          onDrop={(event) => {
            event.preventDefault();
            if (busy || !totalFrames || track.items.length >= 100) return;
            try {
              const value = JSON.parse(
                event.dataTransfer.getData("application/x-afds-asset"),
              );
              const asset = materials.find((item) => item.id === value.assetId);
              if (value.projectId === project.project_id && asset)
                addClip(track, asset);
            } catch {
              return;
            }
          }}
        >
          <legend>{track.name || `叠加轨 ${trackIndex + 1}`}</legend>
          <p className="muted">可将素材抽屉中的图片或视频拖入此轨。</p>
          <div className="composition-fields">
            <label>
              视频轨名称
              <input
                value={track.name}
                onChange={(event) =>
                  updateTrack(track.id, { name: event.target.value })
                }
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={track.hidden}
                onChange={(event) =>
                  updateTrack(track.id, { hidden: event.target.checked })
                }
              />
              隐藏视频轨
            </label>
            <button
              disabled={trackIndex === 0 || busy}
              onClick={() =>
                edit((value) => {
                  const tracks = [...value.video_layers];
                  [tracks[trackIndex - 1], tracks[trackIndex]] = [
                    tracks[trackIndex],
                    tracks[trackIndex - 1],
                  ];
                  return { ...value, video_layers: tracks };
                })
              }
            >
              下移覆盖顺序
            </button>
            <button
              disabled={trackIndex === draft.video_layers.length - 1 || busy}
              onClick={() =>
                edit((value) => {
                  const tracks = [...value.video_layers];
                  [tracks[trackIndex], tracks[trackIndex + 1]] = [
                    tracks[trackIndex + 1],
                    tracks[trackIndex],
                  ];
                  return { ...value, video_layers: tracks };
                })
              }
            >
              上移覆盖顺序
            </button>
            <button
              disabled={busy}
              onClick={() =>
                edit((value) => ({
                  ...value,
                  video_layers: value.video_layers.filter(
                    (item) => item.id !== track.id,
                  ),
                }))
              }
            >
              删除视频轨
            </button>
          </div>
          {track.items.map((item, index) => (
            <details key={item.id} open className="video-layer-clip">
              <summary>
                叠加片段 {index + 1} · {item.duration_frames} 帧
              </summary>
              <div className="composition-fields">
                <label>
                  片段开始帧
                  <input
                    type="number"
                    min={0}
                    value={item.start_frame}
                    onChange={(event) =>
                      updateClip(track, item.id, {
                        start_frame: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  源裁切开始帧
                  <input
                    type="number"
                    min={0}
                    disabled={
                      project.assets.find((asset) => asset.id === item.asset_id)
                        ?.media_type === "image"
                    }
                    value={item.source_start_frame}
                    onChange={(event) =>
                      updateClip(track, item.id, {
                        source_start_frame: Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  片段时长帧
                  <input
                    type="number"
                    min={1}
                    value={item.duration_frames}
                    onChange={(event) =>
                      updateClip(track, item.id, {
                        duration_frames: Number(event.target.value),
                      })
                    }
                  />
                </label>
                {(
                  [
                    ["x", "水平位置"],
                    ["y", "垂直位置"],
                    ["width", "宽度"],
                    ["height", "高度"],
                    ["opacity", "不透明度"],
                  ] as const
                ).map(([field, label]) => (
                  <label key={field}>
                    {label}
                    <input
                      aria-label={label}
                      type="number"
                      min={field === "width" || field === "height" ? 5 : 0}
                      max={100}
                      step={1}
                      value={Math.round(item[field] * 100)}
                      onChange={(event) =>
                        updateClip(track, item.id, {
                          [field]: Number(event.target.value) / 100,
                        })
                      }
                    />
                  </label>
                ))}
                <button
                  disabled={busy}
                  onClick={() =>
                    updateTrack(track.id, {
                      items: track.items.filter((clip) => clip.id !== item.id),
                    })
                  }
                >
                  删除叠加片段 {index + 1}
                </button>
              </div>
            </details>
          ))}
        </fieldset>
      ))}
      {clips.length > 1 && (
        <section className="composition-transitions" aria-label="主轨转场">
          <h3>主轨转场</h3>
          {clips.slice(0, -1).map((clip, index) => {
            const next = clips[index + 1];
            const current = draft.transitions.find(
              (item) =>
                item.from_clip_id === clip.id && item.to_clip_id === next.id,
            );
            return (
              <div key={clip.id || clip.shot_id} className="composition-fields">
                <span>
                  {
                    project.shots.find((shot) => shot.id === clip.shot_id)
                      ?.title
                  }{" "}
                  →{" "}
                  {
                    project.shots.find((shot) => shot.id === next.shot_id)
                      ?.title
                  }
                </span>
                <label>
                  转场 {index + 1}
                  <select
                    disabled={busy || !clip.id || !next.id}
                    value={current?.kind || ""}
                    onChange={(event) => transition(index, event.target.value)}
                  >
                    <option value="">直接切换</option>
                    <option value="dissolve">交叉溶解</option>
                    <option value="fadeblack">淡入淡出黑场</option>
                    <option value="wipeleft">向左擦除</option>
                  </select>
                </label>
                {current && (
                  <label>
                    转场时长帧
                    <input
                      type="number"
                      min={1}
                      max={Math.floor(
                        Math.min(clip.duration_frames, next.duration_frames) /
                          2,
                      )}
                      value={current.duration_frames}
                      onChange={(event) =>
                        edit((value) => ({
                          ...value,
                          transitions: value.transitions.map((item) =>
                            item.id === current.id
                              ? {
                                  ...item,
                                  duration_frames: Number(event.target.value),
                                }
                              : item,
                          ),
                        }))
                      }
                    />
                  </label>
                )}
              </div>
            );
          })}
          <label className="check">
            <input
              type="checkbox"
              checked={trim}
              onChange={(event) => {
                setTrim(event.target.checked);
                setPreview(null);
              }}
            />
            批准时裁切预览列出的越界片段、字幕与标记
          </label>
          <button
            disabled={busy || !dirty}
            onClick={() =>
              void run(async () =>
                setPreview(
                  await api<EditPreview>(
                    `/projects/${project.project_id}/timeline/composition/preview`,
                    request(),
                  ),
                ),
              )
            }
          >
            预览转场变更
          </button>
          {preview && (
            <div className="composition-plan" role="status">
              <p>
                成片 {preview.total_frames} 帧，原长度{" "}
                {preview.previous_total_frames} 帧。
              </p>
              {preview.overflows.map((change, index) => (
                <p key={index}>
                  {change.kind} {change.track_id || ""}{" "}
                  {change.before_frames ?? ""} → {change.after_frames ?? "移除"}
                </p>
              ))}
              {!preview.can_apply && (
                <p>请调整越界内容，或选择裁切后重新预览。</p>
              )}
              <button
                disabled={busy || !preview.can_apply}
                onClick={() => void run(() => save(true))}
              >
                批准并保存剪辑
              </button>
            </div>
          )}
        </section>
      )}
      <CompositionPreview
        project={project}
        run={run}
        busy={busy || dirty}
        totalFrames={totalFrames}
      />
    </section>
  );
}
