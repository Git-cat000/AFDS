import { useRef, useState, type DragEvent } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  MiniMap,
  MarkerType,
  SelectionMode,
  type Connection,
  type Edge,
  type NodeChange,
  type EdgeChange,
  type Viewport,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  Plus,
  Minus,
  Undo2,
  Redo2,
  Trash2,
  StickyNote,
  Images,
  Maximize2,
} from "lucide-react";
import { CanvasNodeCard, type MaterialFlowNode } from "./CanvasNodes";
import { CanvasActionContext } from "./canvas-context";
import {
  addMaterial,
  addNote,
  connectItems,
  removeItems,
  visibleItems,
  itemLabel,
  worldPoint,
  type Point,
} from "./canvas-board";
import { useCanvasBoard } from "./useCanvasBoard";
import type { Project, Shot } from "./types";
import "@xyflow/react/dist/style.css";

type ReferenceRole = "reference_image" | "start_image" | "end_image";
type Props = {
  project: Project;
  selectedShotId?: string;
  selectShot: (id: string) => void;
  edit: (shot: Shot) => void;
  mode: "canvas" | "grid";
  onUseImage?: (
    assetId: string,
    shotId: string,
    role?: ReferenceRole,
    remove?: boolean,
  ) => void;
  openMedia?: () => void;
};
type CanvasEdge = Edge<{
  assetId?: string;
  shotId?: string;
  role?: ReferenceRole;
  actual?: boolean;
}>;
const nodeTypes = { material: CanvasNodeCard };
const assetDragType = "application/x-afds-asset";

export function MaterialCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasSurface {...props} />
    </ReactFlowProvider>
  );
}

function CanvasSurface({
  project,
  selectedShotId,
  selectShot,
  edit,
  mode,
  onUseImage,
  openMedia,
}: Props) {
  const state = useCanvasBoard(project);
  const { board, change } = state;
  const items = visibleItems(board, project);
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [pendingFrom, setPendingFrom] = useState<string | null>(null);
  const [trayOpen, setTrayOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const flow = useRef<ReactFlowInstance<MaterialFlowNode, CanvasEdge> | null>(
    null,
  );
  const [measurements, setMeasurements] = useState<
    Record<string, { width: number; height: number }>
  >({});
  const nodes: MaterialFlowNode[] = items.map((item) => ({
    id: item.id,
    type: "material",
    position: item.position,
    data: { item, project },
    dragHandle: ".node-drag-handle",
    selected: selectedNodes.includes(item.id),
    measured: measurements[item.id],
    width: 270,
  }));
  const nodeIds = new Set(items.map((item) => item.id));
  const edges: CanvasEdge[] = board.edges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge) => ({
      ...edge,
      type: "default",
      sourceHandle: "out",
      targetHandle: "in",
      selected: selectedEdge === edge.id,
      markerEnd: { type: MarkerType.ArrowClosed },
      ariaLabel: `${itemLabel(items.find((item) => item.id === edge.source)!)} → ${itemLabel(items.find((item) => item.id === edge.target)!)} · ${edge.label}`,
    }));
  for (const item of items) {
    if (item.kind !== "shot") continue;
    for (const [role, assetId] of Object.entries(
      item.shot.reference_images || {},
    )) {
      for (const source of items.filter(
        (value) => value.kind === "asset" && value.asset.id === assetId,
      )) {
        edges.push({
          id: `reference:${item.id}:${role}:${source.id}`,
          source: source.id,
          target: item.id,
          sourceHandle: "out",
          targetHandle: "in",
          label: (
            {
              reference_image: "参考图",
              start_image: "首帧图",
              end_image: "尾帧图",
            } as Record<string, string>
          )[role],
          data: {
            assetId,
            shotId: item.id,
            role: role as ReferenceRole,
            actual: true,
          },
          deletable: false,
          selected:
            selectedEdge === `reference:${item.id}:${role}:${source.id}`,
          className: "effective-input-edge",
          markerEnd: { type: MarkerType.ArrowClosed },
        });
      }
    }
  }
  const effectivePairs = new Set(
    edges
      .filter((edge) => edge.data?.actual)
      .map((edge) => JSON.stringify([edge.source, edge.target])),
  );
  const visibleEdges = edges.filter(
    (edge) =>
      edge.data?.actual ||
      !effectivePairs.has(JSON.stringify([edge.source, edge.target])),
  );
  const rawSelection = edges.find((edge) => edge.id === selectedEdge);
  const edge =
    visibleEdges.find((edge) => edge.id === selectedEdge) ||
    visibleEdges.find(
      (edge) =>
        edge.data?.actual &&
        edge.source === rawSelection?.source &&
        edge.target === rawSelection?.target,
    );
  const source = items.find((item) => item.id === edge?.source);
  const target = items.find((item) => item.id === edge?.target);
  const imageToShot =
    source?.kind === "asset" &&
    source.asset.media_type === "image" &&
    target?.kind === "shot";
  const draggedAssets = project.assets.filter(
    (asset) =>
      ["image", "video", "audio"].includes(asset.media_type) &&
      `${asset.provenance.name || ""} ${asset.media_type} ${asset.id}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );

  function connect(connection: Pick<Connection, "source" | "target">) {
    change((value) =>
      connectItems(value, project, connection.source, connection.target),
    );
    setPendingFrom(null);
  }
  function port(id: string, type: "source" | "target") {
    if (type === "source") {
      setPendingFrom(id);
      setNotice("选择目标节点的输入端口，或按 Esc 取消连线。");
    } else if (pendingFrom) {
      connect({ source: pendingFrom, target: id });
      setNotice("");
    }
  }
  function centeredPoint(): Point {
    const rect = root.current?.getBoundingClientRect();
    return worldPoint(
      { x: (rect?.width || 800) / 2 - 130, y: (rect?.height || 600) / 2 - 100 },
      state.current.current.present,
    );
  }
  function drop(event: DragEvent) {
    event.preventDefault();
    const raw = event.dataTransfer.getData(assetDragType);
    if (!raw) {
      setNotice("请先从素材面板导入文件，再将素材拖入画布。");
      return;
    }
    try {
      const value = JSON.parse(raw);
      if (value.projectId !== project.project_id) {
        setNotice("请选择当前项目中的素材。");
        return;
      }
      const rect = root.current!.getBoundingClientRect();
      const point = worldPoint(
        { x: event.clientX - rect.left, y: event.clientY - rect.top },
        state.current.current.present,
      );
      change((current) => addMaterial(current, project, value.assetId, point));
    } catch {
      setNotice("拖入的素材信息无效，请重新选择。");
    }
  }
  function removeSelection() {
    change((value) =>
      removeItems(
        value,
        project,
        selectedNodes,
        selectedEdge && !edge?.data?.actual ? [selectedEdge] : [],
      ),
    );
    setSelectedNodes([]);
    setSelectedEdge(null);
  }
  function nodeChanges(changes: NodeChange<MaterialFlowNode>[]) {
    const selection = changes.filter((value) => value.type === "select");
    if (selection.length)
      setSelectedNodes((previous) => {
        const ids = new Set(previous);
        for (const value of selection)
          if (value.type === "select") {
            if (value.selected) ids.add(value.id);
            else ids.delete(value.id);
          }
        return [...ids];
      });
    const positions = changes.filter(
      (value) => value.type === "position" && value.position,
    );
    if (positions.some((value) => value.type === "position" && value.dragging))
      state.startGesture();
    if (positions.length)
      change(
        (value) => {
          const next = { ...value.positions };
          for (const position of positions)
            if (position.type === "position" && position.position)
              next[position.id] = position.position;
          return { ...value, positions: next };
        },
        !state.hasGesture(),
        !state.hasGesture(),
      );
    const dimensions = changes.filter(
      (value) => value.type === "dimensions" && value.dimensions,
    );
    if (dimensions.length)
      setMeasurements((previous) => {
        const updated = { ...previous };
        let changed = false;
        for (const value of dimensions)
          if (
            value.type === "dimensions" &&
            value.dimensions &&
            (previous[value.id]?.width !== value.dimensions.width ||
              previous[value.id]?.height !== value.dimensions.height)
          ) {
            updated[value.id] = value.dimensions;
            changed = true;
          }
        return changed ? updated : previous;
      });
    const removed = changes
      .filter((value) => value.type === "remove")
      .map((value) => value.id);
    if (removed.length)
      change((value) => removeItems(value, project, removed, []));
    if (
      positions.some(
        (value) => value.type === "position" && value.dragging === false,
      )
    )
      state.finishGesture();
  }
  function edgeChanges(changes: EdgeChange<CanvasEdge>[]) {
    for (const value of changes) {
      if (value.type === "select")
        setSelectedEdge((current) => {
          if (value.selected) return value.id;
          return current === value.id ? null : current;
        });
      if (
        value.type === "remove" &&
        !edges.find((item) => item.id === value.id)?.data?.actual
      )
        change((current) => removeItems(current, project, [], [value.id]));
    }
  }
  function arrange() {
    change((value) => ({
      ...value,
      positions: Object.fromEntries(
        visibleItems(value, project).map((item, index) => [
          item.id,
          { x: (index % 3) * 310, y: Math.floor(index / 3) * 390 },
        ]),
      ),
      x: 36,
      y: 42,
      zoom: 1,
    }));
  }
  function zoom(delta: number) {
    change(
      (value) => ({
        ...value,
        zoom: Math.max(0.05, Math.min(4, +(value.zoom + delta).toFixed(2))),
      }),
      false,
    );
  }
  const actions = {
    selectShot,
    editShot: edit,
    pendingFrom,
    port,
    moveNode: (id: string, point: Point) =>
      change((value) => ({
        ...value,
        positions: { ...value.positions, [id]: point },
      })),
    updateNote: (id: string, text: string) =>
      change((value) => ({
        ...value,
        nodes: value.nodes.map((node) =>
          node.id === id && node.kind === "note" ? { ...node, text } : node,
        ),
      })),
  };

  return (
    <div className="canvas-column material-board">
      <div className="board-tools">
        <button aria-expanded={trayOpen} onClick={() => setTrayOpen(!trayOpen)}>
          <Images size={15} />
          素材抽屉
        </button>
        <button
          onClick={() => change((value) => addNote(value, centeredPoint()))}
        >
          <StickyNote size={15} />
          添加文字
        </button>
        <button
          disabled={!state.history.past.length}
          aria-label="撤销画布操作"
          onClick={state.undo}
        >
          <Undo2 size={15} />
        </button>
        <button
          disabled={!state.history.future.length}
          aria-label="重做画布操作"
          onClick={state.redo}
        >
          <Redo2 size={15} />
        </button>
        <button
          disabled={
            !selectedNodes.length && (!selectedEdge || !!edge?.data?.actual)
          }
          onClick={removeSelection}
        >
          <Trash2 size={15} />
          移出画布
        </button>
      </div>
      {trayOpen && (
        <section className="material-tray" aria-label="画布素材抽屉">
          <div>
            <input
              aria-label="搜索画布素材"
              placeholder="搜索项目素材"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <button onClick={openMedia}>导入或生成素材</button>
          </div>
          <div className="material-tray-items">
            {draggedAssets.map((asset) => (
              <div
                key={asset.id}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData(
                    assetDragType,
                    JSON.stringify({
                      projectId: project.project_id,
                      assetId: asset.id,
                    }),
                  );
                  event.dataTransfer.effectAllowed = "copy";
                }}
              >
                <strong>
                  {asset.provenance.name ||
                    `${asset.media_type} ${asset.id.slice(0, 8)}`}
                </strong>
                <button
                  aria-label={`放入画布 ${asset.provenance.name || asset.id}`}
                  onClick={() =>
                    change((value) =>
                      addMaterial(value, project, asset.id, centeredPoint()),
                    )
                  }
                >
                  放入画布
                </button>
              </div>
            ))}
            {!draggedAssets.length && (
              <p className="muted">项目素材会出现在这里，可拖入画布。</p>
            )}
          </div>
          {board.hiddenShots.length > 0 && (
            <button
              onClick={() => change((value) => ({ ...value, hiddenShots: [] }))}
            >
              显示全部镜头
            </button>
          )}
        </section>
      )}
      <CanvasActionContext.Provider value={actions}>
        <div
          className={`shot-canvas ${mode}`}
          ref={root}
          aria-label="镜头创作画布"
          onDrop={drop}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setPendingFrom(null);
              setNotice("");
            }
            if (
              event.target instanceof HTMLElement &&
              event.target.closest("input,textarea,select")
            )
              return;
            if (
              (event.ctrlKey || event.metaKey) &&
              event.key.toLowerCase() === "z"
            ) {
              event.preventDefault();
              if (event.shiftKey) state.redo();
              else state.undo();
            }
          }}
        >
          {mode === "canvas" ? (
            <ReactFlow<MaterialFlowNode, CanvasEdge>
              nodes={nodes}
              edges={visibleEdges}
              nodeTypes={nodeTypes}
              viewport={{ x: board.x, y: board.y, zoom: board.zoom }}
              onViewportChange={(view: Viewport) =>
                change((value) => ({ ...value, ...view }), false, false)
              }
              onMoveEnd={state.persist}
              onInit={(instance) => {
                flow.current = instance;
              }}
              onNodesChange={nodeChanges}
              onEdgesChange={edgeChanges}
              onNodeDragStart={state.startGesture}
              onNodeDragStop={state.finishGesture}
              onSelectionDragStart={state.startGesture}
              onSelectionDragStop={state.finishGesture}
              onNodeClick={(_, node) => {
                if (node.data.item.kind === "shot")
                  selectShot(node.data.item.shot.id);
                setSelectedEdge(null);
              }}
              onConnect={connect}
              isValidConnection={(connection) =>
                connection.source !== connection.target &&
                !board.edges.some(
                  (value) =>
                    value.source === connection.source &&
                    value.target === connection.target,
                )
              }
              onEdgeClick={(_, value) => {
                setSelectedEdge(value.id);
                setSelectedNodes([]);
              }}
              onPaneClick={() => {
                setSelectedEdge(null);
                setPendingFrom(null);
              }}
              minZoom={0.05}
              maxZoom={4}
              selectionMode={SelectionMode.Partial}
              multiSelectionKeyCode={["Shift", "Meta", "Control"]}
              deleteKeyCode={["Backspace", "Delete"]}
              colorMode="dark"
              nodesConnectable
              ariaLabelConfig={{
                "node.a11yDescription.default":
                  "按 Enter 选择节点，使用方向键移动，按 Delete 移出画布。",
                "node.a11yDescription.keyboardDisabled": "按 Enter 选择节点。",
                "node.a11yDescription.ariaLiveMessage": ({ x, y }) =>
                  `节点位置 ${Math.round(x)}, ${Math.round(y)}`,
                "edge.a11yDescription.default":
                  "按 Enter 选择连线，按 Delete 删除画布关联。",
                "minimap.ariaLabel": "画布缩略图",
                "handle.ariaLabel": "连线端口",
              }}
              defaultEdgeOptions={{ interactionWidth: 20 }}
            >
              <Background gap={22} size={1} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(node) => {
                  const kind = items.find((item) => item.id === node.id)?.kind;
                  return kind === "note"
                    ? "#d4b67c"
                    : kind === "shot"
                      ? "#a9a0f1"
                      : "#7ca6a1";
                }}
              />
            </ReactFlow>
          ) : (
            <div className="board-grid">
              {nodes.map((node) => (
                <CanvasNodeCard
                  key={node.id}
                  id={node.id}
                  data={node.data}
                  type="material"
                  selected={node.selected || node.id === selectedShotId}
                  dragging={false}
                  zIndex={0}
                  isConnectable={false}
                  selectable
                  deletable
                  draggable={false}
                  positionAbsoluteX={node.position.x}
                  positionAbsoluteY={node.position.y}
                />
              ))}
            </div>
          )}
          {!items.length && (
            <div className="canvas-empty">
              <h3>将素材拖入画布</h3>
              <p>添加图片、视频、音频或文字，再从节点端口创建连线。</p>
            </div>
          )}
        </div>
      </CanvasActionContext.Provider>
      {edge && (
        <section className="canvas-edge-editor" aria-label="连线设置">
          <strong>
            {source ? itemLabel(source) : "素材"} →{" "}
            {target ? itemLabel(target) : "镜头"}
          </strong>
          {edge.data?.actual ? (
            <>
              <p>镜头输入 · {String(edge.label)}</p>
              <button
                onClick={() =>
                  onUseImage?.(
                    edge.data!.assetId!,
                    edge.data!.shotId!,
                    edge.data!.role,
                    true,
                  )
                }
              >
                预览移除镜头输入
              </button>
            </>
          ) : (
            <>
              <label>
                连线名称
                <input
                  aria-label="连线名称"
                  value={String(edge.label || "")}
                  onChange={(event) =>
                    change((value) => ({
                      ...value,
                      edges: value.edges.map((item) =>
                        item.id === edge.id
                          ? { ...item, label: event.target.value }
                          : item,
                      ),
                    }))
                  }
                />
              </label>
              <p className="muted">画布关联</p>
              {imageToShot && onUseImage && (
                <button
                  onClick={() => {
                    if (source?.kind === "asset" && target?.kind === "shot")
                      onUseImage(source.asset.id, target.shot.id);
                  }}
                >
                  用作镜头输入
                </button>
              )}
              <button
                onClick={() => {
                  change((value) => removeItems(value, project, [], [edge.id]));
                  setSelectedEdge(null);
                }}
              >
                删除连线
              </button>
            </>
          )}
        </section>
      )}
      {(notice || state.storageError) && (
        <p role="status" className="canvas-notice">
          {state.storageError || notice}
        </p>
      )}
      <div className="canvas-toolbar">
        <small>拖动标题移动 · Shift 框选 · 从端口连线</small>
        <button aria-label="缩小画布" onClick={() => zoom(-0.1)}>
          <Minus size={15} />
        </button>
        <span aria-live="polite">{Math.round(board.zoom * 100)}%</span>
        <button aria-label="放大画布" onClick={() => zoom(0.1)}>
          <Plus size={15} />
        </button>
        <button
          aria-label="适应全部节点"
          onClick={() => void flow.current?.fitView({ padding: 0.15 })}
        >
          <Maximize2 size={15} />
        </button>
        <button onClick={arrange}>自动排列</button>
      </div>
      <label className="shot-jump">
        定位镜头
        <select
          value={selectedShotId || ""}
          onChange={(event) => {
            const id = event.target.value;
            selectShot(id);
            change((value) => {
              const restored = {
                ...value,
                hiddenShots: value.hiddenShots.filter((value) => value !== id),
              };
              const item = visibleItems(restored, project).find(
                (value) => value.id === id,
              );
              return item
                ? {
                    ...restored,
                    x: 36 - item.position.x * value.zoom,
                    y: 42 - item.position.y * value.zoom,
                  }
                : restored;
            }, false);
          }}
        >
          {project.shots
            .filter((shot) => shot.status !== "archived")
            .map((shot) => (
              <option key={shot.id} value={shot.id}>
                {shot.order + 1} · {shot.title}
              </option>
            ))}
        </select>
      </label>
    </div>
  );
}
