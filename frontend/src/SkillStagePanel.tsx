import { api } from "./api";
import {
  productionStages,
  type ProductionStage,
  type SkillAction,
} from "./skill-types";
import type { Project } from "./types";

const toolStages: Record<string, ProductionStage[]> = {
  script: ["story"],
  continuity: ["continuity"],
  storyboard: ["shots", "video"],
  assets: ["image", "audio"],
  timeline: ["editing", "audio"],
  review: ["review"],
  jobs: ["delivery"],
};

export function SkillStagePanel({
  project,
  tool,
  shotId,
  busy,
  run,
  open,
}: {
  project: Project;
  tool: string;
  shotId?: string;
  busy: boolean;
  run: SkillAction;
  open: (stage: ProductionStage) => void;
}) {
  return (
    <section className="stage-skills" aria-label="当前制作层技能">
      {(toolStages[tool] || []).map((stage) => (
        <div key={stage}>
          <button onClick={() => open(stage)}>
            {productionStages[stage]}技能{" "}
            <span>{project.production_guidance?.[stage]?.length || 0}</span>
          </button>
          <button
            disabled={busy}
            aria-label={`检查${productionStages[stage]}`}
            onClick={() =>
              void run(async () => {
                await api(`/projects/${project.project_id}/skills/checks`, {
                  base_revision: project.revision,
                  stage,
                  shot_ids:
                    shotId &&
                    ["shots", "video", "review", "continuity"].includes(stage)
                      ? [shotId]
                      : [],
                });
                open(stage);
              })
            }
          >
            检查
          </button>
        </div>
      ))}
    </section>
  );
}
