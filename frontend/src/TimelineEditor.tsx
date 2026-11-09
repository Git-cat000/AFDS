import { useState } from "react";
import { AudioLines, Plus, Subtitles, Upload, VolumeX } from "lucide-react";
import { api, mediaUrl } from "./api";
import "./timeline.css";

type AudioItem = {
  asset_id: string;
  start_frame: number;
  source_start_frame: number;
  duration_frames: number;
  gain_db: number;
};
type VideoItem = {
  shot_id: string;
  start_frame: number;
  duration_frames: number;
};
type AudioTrack = {
  id: string;
  type: "audio";
  name?: string;
  muted?: boolean;
  gain_db?: number;
  items: AudioItem[];
};
type Caption = {
  id: string;
  start_frame: number;
  duration_frames: number;
  text: string;
};
type Marker = {
  id: string;
  frame: number;
  label: string;
  kind: "beat" | "dialogue" | "note";
};
type Composition = {
  tracks: ({ id: string; type: "video"; items: VideoItem[] } | AudioTrack)[];
  captions?: Caption[];
  markers?: Marker[];
};
type EditorProject = {
  project_id: string;
  revision: number;
  format: { fps_num: number; fps_den: number };
  shots: {
    id: string;
    title: string;
    duration_frames: number;
    locked?: boolean;
    status?: string;
  }[];
  assets: {
    id: string;
    media_type: string;
    duration_seconds?: number;
    provenance?: { provider_id?: string };
  }[];
  timeline: unknown;
};
type AnchorBody = {
  base_revision: number;
  boundary_shot_ids: string[];
  bpm?: number;
  allow_protected: boolean;
};
type TimingPlan = {
  base_revision: number;
  preview_token: string;
  changes: { shot_id: string; before: number; after: number }[];
  affected_shot_ids: string[];
  protected_shot_ids: string[];
};

export function TimelineEditor({
  project,
  run,
  busy,
}: {
  project: EditorProject;
  run: (action: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const timeline = project.timeline as Composition;
  const audioTracks = timeline.tracks.filter(
    (track): track is AudioTrack => track.type === "audio",
  );
  const videoItems =
    timeline.tracks[0]?.type === "video" ? timeline.tracks[0].items : [];
  const captions = timeline.captions || [];
  const markers = timeline.markers || [];
  const totalFrames = videoItems.reduce(
    (sum, item) => sum + item.duration_frames,
    0,
  );
  const fps = project.format.fps_num / project.format.fps_den;
  const audioAssets = project.assets.filter(
    (asset) => asset.media_type === "audio",
  );
  const shotName = (id: string) =>
    project.shots.find((shot) => shot.id === id)?.title || "镜头";
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [trackId, setTrackId] = useState("");
  const [assetId, setAssetId] = useState("");
  const [audioStart, setAudioStart] = useState(0);
  const [sourceStart, setSourceStart] = useState(0);
  const [audioDuration, setAudioDuration] = useState(24);
  const [gain, setGain] = useState(0);
  const [editingAudio, setEditingAudio] = useState<number | null>(null);
  const [captionId, setCaptionId] = useState<string | null>(null);
  const [captionText, setCaptionText] = useState("");
  const [captionStart, setCaptionStart] = useState(0);
  const [captionDuration, setCaptionDuration] = useState(24);
  const [markerLabel, setMarkerLabel] = useState("");
  const [markerFrame, setMarkerFrame] = useState(0);
  const [markerKind, setMarkerKind] = useState<Marker["kind"]>("beat");
  const [bpm, setBpm] = useState("");
  const [boundaries, setBoundaries] = useState<string[]>([]);
  const [allowProtected, setAllowProtected] = useState(false);
  const [timing, setTiming] = useState<{
    plan: TimingPlan;
    body: AnchorBody;
  } | null>(null);
  const selectedTrack =
    audioTracks.find((track) => track.id === trackId) || audioTracks[0];
  const selectedAssetId = assetId || audioAssets[0]?.id || "";
  const selectedAsset = audioAssets.find(
    (asset) => asset.id === selectedAssetId,
  );
  const uploadTooLarge = !!file && file.size > 32 * 1024 * 1024;

  async function save(
    values: Partial<{
      audio_tracks: AudioTrack[];
      captions: Caption[];
      markers: Marker[];
    }>,
  ) {
    await api(
      `/projects/${project.project_id}/timeline/composition`,
      {
        base_revision: project.revision,
        audio_tracks: audioTracks,
        captions,
        markers,
        ...values,
      },
      "PUT",
    );
    setTiming(null);
  }

  function editAudio(track: AudioTrack, item: AudioItem, index: number) {
    setTrackId(track.id);
    setAssetId(item.asset_id);
    setAudioStart(item.start_frame);
    setSourceStart(item.source_start_frame);
    setAudioDuration(item.duration_frames);
    setGain(item.gain_db);
    setEditingAudio(index);
  }

  return (
    <section className="timeline-editor" aria-label="音频与字幕时间轴">
      <div className="timeline-heading">
        <div>
          <h2>音频与字幕</h2>
          <p className="muted">
            一个连续画面主轨，多条音乐与对白音轨。所有位置以项目帧为单位。
          </p>
        </div>
        <span className="timeline-duration">
          {totalFrames} 帧 · {(totalFrames / fps).toFixed(2)} 秒
        </span>
      </div>
      <div className="panel timeline-lanes" aria-label="时间轴轨道预览">
        <div className="timeline-lane">
          <strong>画面主轨</strong>
          <div className="timeline-lane-content">
            {videoItems.map((item) => (
              <span
                className="timeline-clip video"
                key={item.shot_id}
                style={{
                  left: `${(item.start_frame / Math.max(totalFrames, 1)) * 100}%`,
                  width: `${(item.duration_frames / Math.max(totalFrames, 1)) * 100}%`,
                }}
                title={`${shotName(item.shot_id)} · ${item.duration_frames} 帧`}
              >
                {shotName(item.shot_id)}
              </span>
            ))}
          </div>
        </div>
        {audioTracks.map((track) => (
          <div className="timeline-lane" key={track.id}>
            <strong>
              {track.muted && <VolumeX size={13} />} {track.name || track.id}
            </strong>
            <div className="timeline-lane-content">
              {track.items.map((item, index) => (
                <button
                  className={`timeline-clip audio ${track.muted ? "muted-clip" : ""}`}
                  key={index}
                  style={{
                    left: `${(item.start_frame / Math.max(totalFrames, 1)) * 100}%`,
                    width: `${(item.duration_frames / Math.max(totalFrames, 1)) * 100}%`,
                  }}
                  onClick={() => editAudio(track, item, index)}
                  title={`编辑音频 · ${item.start_frame}–${item.start_frame + item.duration_frames} 帧`}
                >
                  {item.gain_db} dB
                </button>
              ))}
            </div>
          </div>
        ))}
        <div className="timeline-lane">
          <strong>字幕</strong>
          <div className="timeline-lane-content">
            {captions.map((caption) => (
              <span
                className="timeline-clip caption"
                key={caption.id}
                style={{
                  left: `${(caption.start_frame / Math.max(totalFrames, 1)) * 100}%`,
                  width: `${(caption.duration_frames / Math.max(totalFrames, 1)) * 100}%`,
                }}
                title={caption.text}
              >
                {caption.text}
              </span>
            ))}
            {markers.map((marker) => (
              <span
                className="timeline-anchor"
                key={marker.id}
                style={{
                  left: `${(marker.frame / Math.max(totalFrames, 1)) * 100}%`,
                }}
                title={`${marker.label} · ${marker.frame} 帧`}
              >
                ◆
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="timeline-panels">
        <section className="panel timeline-form">
          <h3>
            <AudioLines size={18} /> 音乐与对白
          </h3>
          <label>
            选择音频文件
            <input
              type="file"
              accept=".wav,.mp3,.flac,.ogg,audio/wav,audio/mpeg,audio/flac,audio/ogg"
              onChange={(event) => {
                setFile(event.target.files?.[0] || null);
                setRights(false);
              }}
            />
          </label>
          <small className="muted">
            WAV、MP3、FLAC、Ogg；最大 32 MB。由独立任务验证并导入。
          </small>
          {uploadTooLarge && <p role="alert">文件超过 32 MB，请裁剪后重试。</p>}
          <label className="check-label">
            <input
              type="checkbox"
              checked={rights}
              onChange={(event) => setRights(event.target.checked)}
            />
            我确认拥有该音频的使用权或已获得授权
          </label>
          <button
            disabled={busy || !file || !rights || uploadTooLarge}
            onClick={() =>
              void run(async () => {
                if (!file) return;
                const query = new URLSearchParams({
                  base_revision: String(project.revision),
                  name: file.name,
                  rights_confirmed: "true",
                });
                const response = await fetch(
                  `/api/v1/projects/${project.project_id}/timeline/audio/import?${query}`,
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/octet-stream" },
                    body: file,
                  },
                );
                const data = await response.json();
                if (!response.ok)
                  throw new Error(data.error?.message || "音频导入失败");
                setRights(false);
                setFile(null);
              })
            }
          >
            <Upload size={14} /> 导入音频
          </button>
          <div className="timeline-divider" />
          <div className="button-row timeline-track-heading">
            <strong>音轨</strong>
            <button
              disabled={busy || audioTracks.length >= 15}
              onClick={() =>
                void run(async () => {
                  const id = `a-${crypto.randomUUID()}`;
                  await save({
                    audio_tracks: [
                      ...audioTracks,
                      {
                        id,
                        type: "audio",
                        name: `音轨 ${audioTracks.length + 1}`,
                        muted: false,
                        gain_db: 0,
                        items: [],
                      },
                    ],
                  });
                  setTrackId(id);
                  setEditingAudio(null);
                })
              }
            >
              <Plus size={14} /> 新增音轨
            </button>
          </div>
          <label>
            目标音轨
            <select
              value={selectedTrack?.id || ""}
              onChange={(event) => {
                setTrackId(event.target.value);
                setEditingAudio(null);
              }}
            >
              <option value="" disabled>
                请先新增音轨
              </option>
              {audioTracks.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.name || track.id}
                </option>
              ))}
            </select>
          </label>
          {selectedTrack && (
            <div className="button-row">
              <button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    save({
                      audio_tracks: audioTracks.map((track) =>
                        track.id === selectedTrack.id
                          ? { ...track, muted: !track.muted }
                          : track,
                      ),
                    }),
                  )
                }
              >
                {selectedTrack.muted ? "取消音轨静音" : "音轨静音"}
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await save({
                      audio_tracks: audioTracks.filter(
                        (track) => track.id !== selectedTrack.id,
                      ),
                    });
                    setTrackId("");
                    setEditingAudio(null);
                  })
                }
              >
                删除当前音轨
              </button>
            </div>
          )}
          <label>
            音频素材
            <select
              value={selectedAssetId}
              onChange={(event) => setAssetId(event.target.value)}
            >
              <option value="" disabled>
                请先导入或生成音频
              </option>
              {audioAssets.map((asset, index) => (
                <option key={asset.id} value={asset.id}>
                  音频 {index + 1} · {asset.duration_seconds?.toFixed(2) || "?"}{" "}
                  秒 · {asset.provenance?.provider_id || "素材"}
                </option>
              ))}
            </select>
          </label>
          {selectedAsset && (
            <audio
              controls
              preload="none"
              src={mediaUrl(project.project_id, selectedAsset.id)}
              aria-label="音频素材试听"
            />
          )}
          <div className="timeline-fields">
            <label>
              音频开始帧
              <input
                type="number"
                min={0}
                max={totalFrames}
                step={1}
                value={audioStart}
                onChange={(event) => setAudioStart(Number(event.target.value))}
              />
            </label>
            <label>
              源音频起始帧
              <input
                type="number"
                min={0}
                step={1}
                value={sourceStart}
                onChange={(event) => setSourceStart(Number(event.target.value))}
              />
            </label>
            <label>
              音频持续帧
              <input
                type="number"
                min={1}
                max={totalFrames}
                step={1}
                value={audioDuration}
                onChange={(event) =>
                  setAudioDuration(Number(event.target.value))
                }
              />
            </label>
            <label>
              片段增益 dB
              <input
                type="number"
                min={-96}
                max={24}
                step={0.5}
                value={gain}
                onChange={(event) => setGain(Number(event.target.value))}
              />
            </label>
          </div>
          <button
            className="primary"
            disabled={
              busy ||
              !selectedTrack ||
              !selectedAssetId ||
              audioDuration <= 0 ||
              audioStart + audioDuration > totalFrames
            }
            onClick={() =>
              void run(async () => {
                const item = {
                  asset_id: selectedAssetId,
                  start_frame: audioStart,
                  source_start_frame: sourceStart,
                  duration_frames: audioDuration,
                  gain_db: gain,
                };
                await save({
                  audio_tracks: audioTracks.map((track) =>
                    track.id !== selectedTrack?.id
                      ? track
                      : {
                          ...track,
                          items:
                            editingAudio === null
                              ? [...track.items, item]
                              : track.items.map((old, index) =>
                                  index === editingAudio ? item : old,
                                ),
                        },
                  ),
                });
                setEditingAudio(null);
              })
            }
          >
            {editingAudio === null ? "添加音频片段" : "保存音频片段"}
          </button>
          {editingAudio !== null && (
            <button disabled={busy} onClick={() => setEditingAudio(null)}>
              取消片段编辑
            </button>
          )}
          <div className="timeline-item-list">
            {selectedTrack?.items.map((item, index) => (
              <div key={index}>
                <span>
                  {item.start_frame}–{item.start_frame + item.duration_frames}{" "}
                  帧 · 源 {item.source_start_frame} 帧 · {item.gain_db} dB
                </span>
                <button
                  disabled={busy}
                  onClick={() => editAudio(selectedTrack, item, index)}
                >
                  编辑片段 {index + 1}
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await save({
                        audio_tracks: audioTracks.map((track) =>
                          track.id === selectedTrack.id
                            ? {
                                ...track,
                                items: track.items.filter(
                                  (_, n) => n !== index,
                                ),
                              }
                            : track,
                        ),
                      });
                      setEditingAudio(null);
                    })
                  }
                >
                  删除片段 {index + 1}
                </button>
              </div>
            ))}
          </div>
        </section>

        <div className="timeline-side-panels">
          <section className="panel timeline-form">
            <h3>
              <Subtitles size={18} /> 字幕
            </h3>
            <label>
              字幕内容
              <textarea
                rows={3}
                maxLength={2000}
                value={captionText}
                onChange={(event) => setCaptionText(event.target.value)}
              />
            </label>
            <div className="timeline-fields">
              <label>
                字幕开始帧
                <input
                  type="number"
                  min={0}
                  value={captionStart}
                  onChange={(event) =>
                    setCaptionStart(Number(event.target.value))
                  }
                />
              </label>
              <label>
                字幕持续帧
                <input
                  type="number"
                  min={1}
                  value={captionDuration}
                  onChange={(event) =>
                    setCaptionDuration(Number(event.target.value))
                  }
                />
              </label>
            </div>
            <button
              disabled={
                busy ||
                !captionText.trim() ||
                captionDuration <= 0 ||
                captionStart + captionDuration > totalFrames
              }
              onClick={() =>
                void run(async () => {
                  const caption = {
                    id: captionId || crypto.randomUUID(),
                    text: captionText,
                    start_frame: captionStart,
                    duration_frames: captionDuration,
                  };
                  await save({
                    captions: captionId
                      ? captions.map((old) =>
                          old.id === captionId ? caption : old,
                        )
                      : [...captions, caption],
                  });
                  setCaptionId(null);
                  setCaptionText("");
                })
              }
            >
              {captionId ? "保存字幕" : "添加字幕"}
            </button>
            <div className="timeline-item-list">
              {captions.map((caption) => (
                <div key={caption.id}>
                  <span>
                    {caption.start_frame}–
                    {caption.start_frame + caption.duration_frames} 帧 ·{" "}
                    {caption.text}
                  </span>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setCaptionId(caption.id);
                      setCaptionText(caption.text);
                      setCaptionStart(caption.start_frame);
                      setCaptionDuration(caption.duration_frames);
                    }}
                  >
                    编辑字幕
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        save({
                          captions: captions.filter(
                            (old) => old.id !== caption.id,
                          ),
                        }),
                      )
                    }
                  >
                    删除字幕
                  </button>
                </div>
              ))}
            </div>
          </section>
          <section className="panel timeline-form">
            <h3>节拍与对白锚点</h3>
            <div className="timeline-fields">
              <label>
                锚点类型
                <select
                  value={markerKind}
                  onChange={(event) =>
                    setMarkerKind(event.target.value as Marker["kind"])
                  }
                >
                  <option value="beat">节拍</option>
                  <option value="dialogue">对白</option>
                  <option value="note">备注</option>
                </select>
              </label>
              <label>
                锚点位置帧
                <input
                  type="number"
                  min={0}
                  max={totalFrames}
                  value={markerFrame}
                  onChange={(event) =>
                    setMarkerFrame(Number(event.target.value))
                  }
                />
              </label>
            </div>
            <label>
              锚点名称
              <input
                maxLength={200}
                value={markerLabel}
                onChange={(event) => setMarkerLabel(event.target.value)}
              />
            </label>
            <button
              disabled={busy || !totalFrames}
              onClick={() =>
                void run(async () => {
                  await save({
                    markers: [
                      ...markers,
                      {
                        id: crypto.randomUUID(),
                        frame: markerFrame,
                        label: markerLabel,
                        kind: markerKind,
                      },
                    ],
                  });
                  setMarkerLabel("");
                })
              }
            >
              添加锚点
            </button>
            <div className="timeline-item-list">
              {markers.map((marker) => (
                <div key={marker.id}>
                  <span>
                    {marker.kind === "beat"
                      ? "节拍"
                      : marker.kind === "dialogue"
                        ? "对白"
                        : "备注"}{" "}
                    · {marker.frame} 帧 · {marker.label}
                  </span>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        save({
                          markers: markers.filter(
                            (old) => old.id !== marker.id,
                          ),
                        }),
                      )
                    }
                  >
                    删除锚点
                  </button>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>

      <section className="panel timeline-form timeline-timing">
        <h3>按音频锚点规划镜头时长</h3>
        <p className="muted">
          选择需要对齐的镜头边界，预览后再批准。总时长保持不变；改变时长的镜头素材将需要重新生成。
        </p>
        <div className="timeline-fields">
          <label>
            BPM（可选）
            <input
              type="number"
              min={20}
              max={300}
              step={0.1}
              placeholder="留空则只使用节拍/对白标记"
              value={bpm}
              onChange={(event) => {
                setBpm(event.target.value);
                setTiming(null);
              }}
            />
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={allowProtected}
              onChange={(event) => {
                setAllowProtected(event.target.checked);
                setTiming(null);
              }}
            />
            允许调整锁定或已批准镜头的时长
          </label>
        </div>
        <div className="timeline-boundaries">
          {videoItems.slice(0, -1).map((item) => (
            <label className="check-label" key={item.shot_id}>
              <input
                type="checkbox"
                checked={boundaries.includes(item.shot_id)}
                onChange={(event) => {
                  setBoundaries(
                    event.target.checked
                      ? [...boundaries, item.shot_id]
                      : boundaries.filter((id) => id !== item.shot_id),
                  );
                  setTiming(null);
                }}
              />
              {shotName(item.shot_id)} 之后 ·{" "}
              {item.start_frame + item.duration_frames} 帧
            </label>
          ))}
        </div>
        <button
          disabled={
            busy ||
            !boundaries.length ||
            (!bpm && !markers.some((marker) => marker.kind !== "note"))
          }
          onClick={() =>
            void run(async () => {
              const body = {
                base_revision: project.revision,
                boundary_shot_ids: boundaries,
                ...(bpm ? { bpm: Number(bpm) } : {}),
                allow_protected: allowProtected,
              };
              const plan = await api<TimingPlan>(
                `/projects/${project.project_id}/timeline/anchors/preview`,
                body,
              );
              setTiming({ plan, body });
            })
          }
        >
          预览锚点对齐
        </button>
        {timing && (
          <div className="timeline-timing-preview" role="status">
            <strong>时长调整预览</strong>
            <p>
              受影响镜头 {timing.plan.affected_shot_ids.length} 个；涉及保护镜头{" "}
              {timing.plan.protected_shot_ids.length} 个。
              {!timing.body.allow_protected && "锁定和已批准镜头将保留时长。"}
            </p>
            <ul>
              {timing.plan.changes.map((change) => (
                <li key={change.shot_id}>
                  {shotName(change.shot_id)}：{change.before} → {change.after}{" "}
                  帧
                </li>
              ))}
            </ul>
            {!timing.plan.changes.length && <p>没有可应用的时长变化。</p>}
            {timing.plan.base_revision !== project.revision && (
              <p>项目已有更新，请重新预览。</p>
            )}
            <button
              className="primary"
              disabled={
                busy ||
                !timing.plan.changes.length ||
                timing.plan.base_revision !== project.revision
              }
              onClick={() =>
                void run(async () => {
                  await api(
                    `/projects/${project.project_id}/timeline/anchors/commit`,
                    {
                      ...timing.body,
                      approved: true,
                      preview_token: timing.plan.preview_token,
                    },
                  );
                  setTiming(null);
                })
              }
            >
              批准时长调整
            </button>
          </div>
        )}
      </section>
    </section>
  );
}
