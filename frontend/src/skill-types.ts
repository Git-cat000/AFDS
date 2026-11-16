import type { Job } from "./types";

export const productionStages = {
  story: "剧本编排",
  continuity: "角色与连续性",
  shots: "镜头设计",
  image: "图像与视觉",
  video: "视频生成",
  audio: "对白、配音与音乐",
  editing: "剪辑与节奏",
  review: "审片",
  delivery: "成片交付",
};
export type ProductionStage = keyof typeof productionStages;
export type SkillResource = {
  path: string;
  sha256: string;
  size: number;
  media_type?: string;
};
export type SkillPackage = {
  id: string;
  name: string;
  description: string;
  version: string;
  source: string;
  license: string;
  compatibility: string;
  builtin: boolean;
  stages: ProductionStage[];
  scripts: SkillResource[];
  resources?: SkillResource[];
  instructions?: string;
};
export type GuidanceSnapshot = {
  package_id: string;
  name: string;
  description: string;
  version: string;
  source: string;
  instructions: string;
  resources: { path: string; text: string }[];
};
export type GuideSelection = { package_id: string; resources: string[] };
export type GuidanceSelections = Partial<
  Record<ProductionStage, GuideSelection[]>
>;
export type SkillResult = {
  kind: "script" | "check";
  stage: ProductionStage;
  source_revision: number;
  stale?: boolean;
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  logs_truncated?: boolean;
  truncated?: boolean;
  checked_shots?: number;
  checks?: {
    code: string;
    status: string;
    message: string;
    entity_id?: string;
    evidence?: unknown;
  }[];
  artifacts?: { id: string; name: string; sha256: string; size: number }[];
};
export type SkillRun = Job & {
  request?: { skill_name?: string; script_path?: string };
  source_revision: number;
  skill_result?: SkillResult;
};
export type ScriptPlan = {
  id: string;
  base_revision: number;
  fingerprint: string;
  source_code: string;
  runtime: { name: string; available: boolean };
  request: {
    package_id: string;
    script_path: string;
    arguments: string[];
    stage: ProductionStage;
    timeout_seconds: number;
    context: unknown;
    script_sha256?: string;
  };
};
export type SkillAction = (action: () => Promise<unknown>) => Promise<void>;
