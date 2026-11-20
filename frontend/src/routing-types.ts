import type { ProviderKind } from "./types";

export type Capability =
  | "storyboard"
  | "text_to_video"
  | "text_to_image"
  | "text_to_speech"
  | "transcription"
  | "visual_review";
export type RoutingPolicy = {
  strategy:
    "preferred" | "lowest_estimated_cost" | "quality_first" | "balanced";
  allowed_ids?: string[] | null;
  preferred_ids?: string[];
  minimum_quality?: number | null;
  max_latency_ms?: number | null;
  unknown_quality?: "allow" | "exclude";
  unknown_latency?: "allow" | "exclude";
  unknown_cost?: "inherit" | "exclude";
  max_estimated_cost?: string | null;
  allow_fallback?: boolean;
};
export type RoutingCandidate = {
  id: string;
  label: string;
  kind: ProviderKind;
  capabilities: Capability[];
  config_revision: number;
  model: string;
  estimated_cost: string | null;
  currency: string;
  enabled: boolean;
  health: string;
  is_mock?: boolean;
  timestamps?: boolean;
  quality?: {
    grade: number;
    source_type: "manual" | "benchmark";
    source: string;
  } | null;
  latency?: { p95_ms: number; source: string } | null;
};
export type RouteDecision = {
  id: string;
  selected_id: string;
  capability: Capability;
  source_revision: number;
  eligible_ids: string[];
  budget_entry_id?: string;
  fallback_from?: string;
  fallback_reason?: string;
  evaluations: {
    candidate_id: string;
    config_revision: number;
    model: string;
    estimated_cost: string | null;
    currency: string;
    rejections: string[];
  }[];
};
export const capabilityLabels: Record<Capability, string> = {
  storyboard: "剧本与分镜",
  text_to_video: "视频生成",
  text_to_image: "图像生成",
  text_to_speech: "语音合成",
  transcription: "语音转写",
  visual_review: "视觉审片",
};

export const rejectionLabels: Record<string, string> = {
  CANDIDATE_DISABLED: "连接已停用",
  CANDIDATE_UNHEALTHY: "连接测试失败",
  CAPABILITY_UNSUPPORTED: "能力不匹配",
  CANDIDATE_OUTSIDE_SCOPE: "超出本次候选范围",
  CANDIDATE_NOT_AUTHORIZED: "项目未获授权",
  TIMESTAMPS_UNSUPPORTED: "缺少分段时间戳",
  REFERENCE_ROLE_UNSUPPORTED: "参考图输入不匹配",
  QUALITY_UNKNOWN: "缺少质量依据",
  QUALITY_BELOW_MINIMUM: "质量等级低于要求",
  LATENCY_UNKNOWN: "缺少延迟观测",
  LATENCY_ABOVE_MAXIMUM: "延迟超过上限",
  PROJECT_REMOTE_BLOCKED: "外部模型授权未通过",
  BUDGET_CURRENCY_MISMATCH: "币种与项目不一致",
  UNKNOWN_COST_BLOCKED: "政策禁止未知费用",
  BUDGET_EXCEEDED: "超过剩余预算",
  ROUTE_COST_BOUND_EXCEEDED: "超过本次费用上限",
};
