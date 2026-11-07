export type Format = {
  fps_num: number;
  fps_den: number;
  width: number;
  height: number;
};
export type Shot = {
  id: string;
  scene_id: string;
  order: number;
  title: string;
  action: string;
  emotion: string;
  duration_frames: number;
  camera: { shot_size: string; movement: string };
  locked: boolean;
  status: string;
  active_asset_version_id: string | null;
};
export type Asset = {
  id: string;
  sha256: string;
  media_type: string;
  duration_frames?: number;
  provenance: {
    provider_id: string;
    job_id: string;
    prompt_hash: string;
    is_mock?: boolean;
  };
};
export type AssetVersion = {
  id: string;
  shot_id: string;
  asset_id: string;
  poster_asset_id: string;
  status: string;
  generated_by_job_id: string;
};
export type Project = {
  project_id: string;
  title: string;
  brief: string;
  revision: number;
  format: Format;
  style_bible: Record<string, unknown>;
  shots: Shot[];
  assets: Asset[];
  asset_versions: AssetVersion[];
  timeline: {
    tracks: {
      items: {
        shot_id: string;
        start_frame: number;
        duration_frames: number;
      }[];
    }[];
  };
  render_asset_id: string | null;
  render_status: string;
};
export type Plan = {
  id: string;
  state: string;
  base_revision: number;
  message: string;
  explanation: string;
  shots: Shot[];
  provider_id: string;
  estimated_cost: { status: string; amount: number | null };
};
export type Job = {
  id: string;
  state: string;
  operation: string;
  target_entity_id: string | null;
  progress: number;
  attempt: number;
  provider_id: string;
  elapsed_ms: number | null;
  error: { code: string; message: string } | null;
  request: unknown;
  input_summary: { request_hash: string };
  external_task_id?: string | null;
  remote_notice?: string;
  remote_cancel_supported?: boolean;
};
export type Provider = {
  provider_id: string;
  display_name: string;
  availability: string;
  capabilities: string[];
  pricing: { mode: string };
  adapter_version: string;
  model_id?: string;
};

export type ProviderKind = "llm" | "video" | "image" | "speech";
export type Connection = {
  kind: ProviderKind;
  provider_id: string;
  display_name: string;
  revision: number;
  base_url: string;
  model: string;
  enabled: boolean;
  has_api_key: boolean;
  allow_private_network: boolean;
  timeout_seconds: number;
  json_mode: boolean;
  video_seconds: number;
  video_size: string;
  video_poll_seconds: number;
  video_timeout_seconds: number;
  image_size: string;
  speech_voice: string;
};
export type ModelSettings = {
  providers: Connection[];
  defaults: Record<ProviderKind, string>;
};
export type Revision = {
  revision: number;
  reason: string;
  author: string;
  created_at: number;
};
export type ChangeSet = {
  base_revision: number;
  approved: boolean;
  reason: string;
  operations: {
    op: "set";
    entity_type: "shot" | "project";
    entity_id: string;
    field: string;
    value: unknown;
  }[];
};
export type Preview = {
  changes: { field: string; before: unknown; after: unknown }[];
  affected_shot_ids: string[];
  render_invalidated: boolean;
};
