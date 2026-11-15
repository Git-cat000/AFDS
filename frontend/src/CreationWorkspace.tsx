import { lazy, Suspense, useState, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { ShotInspector } from "./ShotInspector";
import type { Project, Shot } from "./types";

const MaterialCanvas = lazy(() =>
  import("./MaterialCanvas").then((module) => ({
    default: module.MaterialCanvas,
  })),
);

type Props = {
  project: Project;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
  edit: (shot: Shot) => void;
  generate: (ids: string[]) => void;
  canGenerate: boolean;
  dock?: ReactNode;
  selectedShotId?: string;
  onSelectShot?: (id: string) => void;
  openTool?: (tool: "assets" | "review" | "timeline") => void;
  onUseImage?: (
    assetId: string,
    shotId: string,
    role?: "reference_image" | "start_image" | "end_image",
    remove?: boolean,
  ) => void;
};
export function CreationWorkspace(props: Props) {
  const { project, busy, generate, canGenerate } = props;
  const shots = project.shots.filter((shot) => shot.status !== "archived");
  const [selected, setSelected] = useState(shots[0]?.id || "");
  const shot =
    shots.find((item) => item.id === (props.selectedShotId || selected)) ||
    shots[0];
  const [mode, setMode] = useState<"canvas" | "grid">("canvas");
  function selectShot(id: string) {
    setSelected(id);
    props.onSelectShot?.(id);
  }
  return (
    <section className="creation-workspace">
      <div className="section-heading canvas-heading">
        <div>
          <h2>
            素材画布 <span>{shots.length} 个镜头</span>
          </h2>
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
              generate(
                shots.filter((item) => !item.locked).map((item) => item.id),
              )
            }
          >
            <Plus size={15} />
            生成全部镜头
          </button>
        </div>
      </div>
      <div
        className={`creation-columns ${mode} ${props.dock ? "with-tool" : ""}`}
      >
        <Suspense
          fallback={
            <div className="canvas-column">
              <p role="status">正在载入素材画布…</p>
            </div>
          }
        >
          <MaterialCanvas
            project={project}
            selectedShotId={shot?.id}
            selectShot={selectShot}
            edit={props.edit}
            mode={mode}
            onUseImage={props.onUseImage}
            openMedia={() => props.openTool?.("assets")}
          />
        </Suspense>
        {props.dock ||
          (shot && <ShotInspector key={shot.id} {...props} shot={shot} />)}
      </div>
    </section>
  );
}
