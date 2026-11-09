export type Format = {
  fps_num: number;
  fps_den: number;
  width: number;
  height: number;
};
export type AudioTrack = {
  id: string;
  type: "audio";
  name?: string;
  muted: boolean;
  gain_db: number;
  items: {
    asset_id: string;
    start_frame: number;
    source_start_frame: number;
    duration_frames: number;
    gain_db: number;
  }[];
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
  character_ids?: string[];
  input_state_ref?: string | null;
  output_state_ref?: string | null;
};
export type Asset = {
  id: string;
  sha256: string;
  media_type: string;
  duration_frames?: number;
  duration_seconds?: number;
  provenance: {
    provider_id: string;
    job_id: string;
    prompt_hash: string;
    is_mock?: boolean;
    contains_mock?: boolean;
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
  scenes?: { id: string; title: string; location_id?: string | null }[];
  characters?: {
    id: string;
    name: string;
    invariant_traits: string[];
    reference_asset_ids: string[];
  }[];
  locations?: {
    id: string;
    name: string;
    spatial_notes: string;
    reference_asset_ids: string[];
  }[];
  world_states?: {
    id: string;
    scene_id: string;
    shot_id?: string | null;
    characters: {
      character_id: string;
      costume: string;
      pose: string;
      emotion: string;
    }[];
    props: {
      name: string;
      description: string;
      holder_character_id: string | null;
    }[];
    environment: Record<string, string>;
  }[];
  continuity_links?: {
    id: string;
    source_shot_id: string;
    target_shot_id: string;
    kind: string;
    note: string;
  }[];
  assets: Asset[];
  asset_versions: AssetVersion[];
  timeline: {
    version: number;
    tracks: [
      {
        id: string;
        type: "video";
        items: {
          shot_id: string;
          start_frame: number;
          duration_frames: number;
        }[];
      },
      ...AudioTrack[],
    ];
    captions: {
      id: string;
      start_frame: number;
      duration_frames: number;
      text: string;
    }[];
    markers: {
      id: string;
      frame: number;
      label: string;
      kind: "beat" | "dialogue" | "note";
    }[];
  };
  render_asset_id: string | null;
  render_status: string;
};
export type Plan = {
  type?: string;
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
  artifact?: {
    filename: string;
    media_type: string;
    sha256: string;
    size_bytes?: number;
  };
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
  provisional_capabilities?: string[];
  pricing: { mode: string };
  adapter_version: string;
  model_id?: string;
};

export type ProviderKind = "llm" | "video" | "image" | "speech";
export type Connection = {
  vision_enabled?: boolean;
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
  protocol?: "openai-compatible" | "comfyui";
  workflow?: WorkflowTemplate | null;
  request_cost?: string | null;
  cost_currency?: string;
  health?: {
    status: string;
    checked_at?: number;
    error_code?: string;
    generation_tested: boolean;
  };
};
export type WorkflowTemplate = {
  graph: Record<
    string,
    {
      class_type: string;
      inputs: Record<string, unknown>;
      _meta?: { title?: string };
    }
  >;
  bindings: Partial<
    Record<
      "prompt" | "seed" | "width" | "height" | "frames" | "fps",
      { node_id: string; input_name: string }
    >
  >;
  output_node: string;
  output_field: "images" | "gifs" | "videos";
  media_type: "image" | "video";
  seed: number;
};
export type ProjectBudgetState = {
  revision: number;
  budget_limit: string;
  currency: string;
  spent: string;
  reserved: string;
  allow_remote: boolean;
  allow_unknown_cost: boolean;
  unknown_count: number;
  over_budget: boolean;
  entries: {
    id: string;
    reference: string;
    provider_id: string;
    state: string;
    estimate: string | null;
    actual: string | null;
    currency: string;
    can_reconcile?: boolean;
  }[];
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
  protected_shot_ids?: string[];
  render_invalidated: boolean;
};
