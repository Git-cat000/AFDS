import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { AudioLines, Film, GripHorizontal } from "lucide-react";
import { mediaUrl } from "./api";
import { framesToTime, statusLabel } from "./format";
import { itemLabel, type CanvasItem } from "./canvas-board";
import { useCanvasActions } from "./canvas-context";
import type { Project } from "./types";

export type MaterialFlowNode = Node<
  { item: CanvasItem; project: Project },
  "material"
>;

export function CanvasNodeCard({
  data,
  selected,
  isConnectable,
}: NodeProps<MaterialFlowNode>) {
  const { item, project } = data;
  const actions = useCanvasActions();
  const label = itemLabel(item);
  const moveKeys: Record<string, { x: number; y: number }> = {
    ArrowLeft: { x: -20, y: 0 },
    ArrowRight: { x: 20, y: 0 },
    ArrowUp: { x: 0, y: -20 },
    ArrowDown: { x: 0, y: 20 },
  };
  const shot = item.kind === "shot" ? item.shot : undefined;
  const version = project.asset_versions.find(
    (value) => value.id === shot?.active_asset_version_id,
  );
  const asset = item.kind === "asset" ? item.asset : undefined;
  const poster = project.asset_versions.find(
    (value) => value.asset_id === asset?.id,
  )?.poster_asset_id;
  return (
    <article
      className={`canvas-shot material-node ${selected ? "selected" : ""} ${item.kind === "note" ? "note-node" : ""}`}
      data-testid={shot ? "shot-card" : "material-node"}
      data-canvas-id={item.id}
    >
      {isConnectable && (
        <Handle
          id="in"
          type="target"
          position={Position.Left}
          role="button"
          tabIndex={0}
          aria-label={`连接到 ${label}`}
          onKeyDown={(e) => {
            if (["Enter", " "].includes(e.key)) {
              e.preventDefault();
              e.stopPropagation();
              actions.port(item.id, "target");
            }
          }}
        />
      )}
      {isConnectable && (
        <Handle
          id="out"
          type="source"
          position={Position.Right}
          role="button"
          tabIndex={0}
          aria-label={`从 ${label} 连线`}
          aria-pressed={actions.pendingFrom === item.id}
          onKeyDown={(e) => {
            if (["Enter", " "].includes(e.key)) {
              e.preventDefault();
              e.stopPropagation();
              actions.port(item.id, "source");
            }
          }}
        />
      )}
      <div
        className="node-handle node-drag-handle"
        role="button"
        tabIndex={0}
        aria-label={`移动 ${label}`}
        onKeyDown={(e) => {
          const step = moveKeys[e.key];
          if (step) {
            e.preventDefault();
            e.stopPropagation();
            actions.moveNode(item.id, {
              x: item.position.x + step.x,
              y: item.position.y + step.y,
            });
          }
        }}
      >
        <span>
          {shot
            ? `镜头 ${String(shot.order + 1).padStart(2, "0")}`
            : item.kind === "note"
              ? "文字便签"
              : label}
        </span>
        <GripHorizontal size={15} />
      </div>
      {shot && (
        <>
          <button
            className="node-select nodrag"
            aria-label={`选择镜头 ${shot.title}`}
            onClick={() => actions.selectShot(shot.id)}
          >
            <div className="node-poster">
              {version ? (
                <img
                  draggable={false}
                  loading="lazy"
                  src={mediaUrl(project.project_id, version.poster_asset_id)}
                  alt={`${shot.title} 素材预览`}
                />
              ) : (
                <div className="node-placeholder">
                  <Film size={28} />
                  <span>等待生成素材</span>
                </div>
              )}
              <span className="duration">
                {framesToTime(shot.duration_frames, project.format)}
              </span>
            </div>
            <div className="node-description">
              <strong>{shot.title}</strong>
              <span className={`status status-${shot.status}`}>
                {statusLabel[shot.status] || shot.status}
              </span>
              <p>{shot.action}</p>
            </div>
          </button>
          <footer className="node-footer">
            <span>
              {shot.camera.shot_size} · {shot.camera.movement}
            </span>
            <button
              className="text-button nodrag"
              aria-label="编辑镜头"
              onClick={() => actions.editShot(shot)}
            >
              编辑
            </button>
          </footer>
          {!!Object.keys(shot.reference_images || {}).length && (
            <div className="node-references">
              {Object.entries(shot.reference_images || {}).map(([role, id]) => (
                <img
                  key={role}
                  src={mediaUrl(project.project_id, id)}
                  alt={`${shot.title} ${role}`}
                />
              ))}
              <small>已关联参考图</small>
            </div>
          )}
        </>
      )}
      {asset && (
        <div className="asset-node-body nodrag nowheel">
          {asset.media_type === "image" && (
            <img
              draggable={false}
              src={mediaUrl(project.project_id, asset.id)}
              alt={label}
            />
          )}
          {asset.media_type === "video" && (
            <video
              controls
              playsInline
              preload="none"
              poster={poster ? mediaUrl(project.project_id, poster) : undefined}
              src={mediaUrl(project.project_id, asset.id)}
            />
          )}
          {asset.media_type === "audio" && (
            <>
              <AudioLines size={34} />
              <audio
                controls
                preload="none"
                src={mediaUrl(project.project_id, asset.id)}
              />
            </>
          )}
          <strong>{label}</strong>
          {(asset.provenance.is_mock || asset.provenance.contains_mock) && (
            <small>DEMO / MOCK</small>
          )}
        </div>
      )}
      {item.kind === "note" && (
        <textarea
          className="note-content nodrag nowheel"
          aria-label={`文字内容 ${item.id}`}
          placeholder="写下想法、提示词或制作说明"
          value={item.text}
          onChange={(e) => actions.updateNote(item.id, e.target.value)}
        />
      )}
    </article>
  );
}
