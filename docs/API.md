# AFDS v0.6 API

Base path is `/api/v1`. The development API is available at `http://127.0.0.1:8000`. Interactive schemas are at `/docs` and `/openapi.json`.

Project, scene, shot, asset and job IDs are UUIDs. Provider slots use category identifiers. Project changes require `base_revision`. The server checks revision inside the same SQLite write transaction. Conflicts return HTTP 409. Mutations use JSON, including soft-archive DELETE requests. This release serves a single local user.

## Bounded reads and durable creation

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/projects/{pid}/jobs/page` | Lightweight task summaries with global active counts and operation counts |
| GET | `/projects/{pid}/jobs/{jid}` | Owned full task detail, including request and cleanup receipt |
| GET | `/projects/{pid}/budget/summary` | Exact budget totals and entry count |
| GET | `/projects/{pid}/budget/entries` | Paged reconciliation records |
| GET | `/projects/{pid}/revisions/page` | Paged revision metadata |
| GET | `/projects/{pid}/activity` | Latest 200 events on first read, then bounded incremental events using `after` |
| POST | `/projects/{pid}/planning/storyboard` | Queue storyboard proposal, 202 |
| POST | `/projects/{pid}/planning/semantic` | Queue structured or LLM semantic proposal, 202 |
| POST | `/projects/{pid}/images/import` | Queue rights-confirmed raw image import, 202 |
| GET / POST | `/projects/{pid}/feedback` | Paged human feedback / approved version-bound feedback creation |
| GET | `/system/report?project_id={pid}` | Download sanitized local diagnostic JSON |
| POST | `/projects/{pid}/maintenance/cleanup/preview` | Store bounded file cleanup preview |
| POST | `/projects/{pid}/maintenance/cleanup` | Queue deletion of explicitly approved preview, 202 |

History pages accept `limit` from 1 to 100, default 25, and opaque `cursor`. Responses contain `items`, `total`, `next_cursor` and `has_more`. Jobs additionally accept `state` values `all`, `active`, `completed`, `failed`, `cancelled`, `needs_review`. Counts remain project-wide while browsing old or filtered history. Cursor scope includes project, resource and filter. Activity returns `cursor` and `has_more` and excludes event payload content. Existing array routes remain available.

Planning bodies retain the director or semantic-edit fields and require a UUID `request_id`. Repeating the same ID and body returns the same task. A changed body with that ID returns `REQUEST_ID_CONFLICT`. Completed tasks include `result_plan_id`. The existing plan approval endpoints apply the proposal with revision validation. Unknown remote submission outcomes require explicit recovery authorization through task controls.

Image import uses the raw request body with query fields `base_revision`, `name`, `rights_confirmed=true`. PNG, JPEG and WebP inputs are bounded at 16 MiB and 16 million pixels. APR 0.3 adds optional `shot.reference_images` roles `reference_image`, `start_image`, `end_image`, each pointing to an owned image asset. Changes use the standard changeset preview and commit. APR 0.1/0.2 imports normalize to 0.3. ComfyUI workflow bindings accept the same role names and upload owned image bytes through `/upload/image` before prompt submission.

Feedback creation requires `base_revision`, `approved=true`, `shot_id`, `version_id`, verdict `accepted`, `rejected` or `needs_work`, optional `report_id`, `scores` containing integer values 1–5 for `continuity`, `action`, `overall`, and `note` up to 2000 characters. Records bind the immutable asset hash and return `matches_current_selection` when listed. List requests can filter `shot_id`.

Cleanup preview requires `base_revision`. The returned plan contains exact candidate identities, total bytes, scan/truncation information, a 15-minute expiry and `preview_token`. Queue requests require `plan_id`, `preview_token`, `base_revision`, `approved=true`. The Worker rechecks file registration, identity, age, path containment, revision, cancellation and lease ownership before each deletion. The task retains cumulative `cleanup_result` with deleted, skipped and already-absent counts, including interrupted attempts.

## Projects and revisions

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health`, `/version` | Service version and media dependency availability |
| POST | `/projects` | Create project, 201 |
| GET | `/projects` | List active projects |
| GET | `/projects/{pid}`, `/projects/{pid}/apr` | Full project state |
| POST | `/projects/import` | Validate APR, import structure with fresh IDs, 201 |
| GET | `/projects/{pid}/export` | APR JSON |
| DELETE | `/projects/{pid}` | Approved soft archive, rejects active worker tasks |
| GET | `/projects/{pid}/revisions` | Revision audit metadata |
| POST | `/projects/{pid}/changesets/preview` | Read-only validation and impact preview |
| POST | `/projects/{pid}/changesets/commit` | Atomic change and invalidation |
| POST | `/projects/{pid}/revisions/{rev}/revert` | Approved inverse of the latest revision |

Create a project with the following body.

```json
{
  "title": "Rain at Midnight",
  "brief": "A quiet farewell at a station",
  "format": {"fps_num": 24, "fps_den": 1, "width": 1280, "height": 720}
}
```

An example change preview uses the current project revision and the target shot UUID.

```json
{
  "base_revision": 7,
  "reason": "Change shot 3 to a detail shot",
  "approved": false,
  "operations": [
    {"op": "set", "entity_type": "shot", "entity_id": "00000000-0000-4000-8000-000000000003", "field": "camera.shot_size", "value": "detail"}
  ]
}
```

The preview returns `changes`, `affected_shot_ids`, `render_invalidated` and a known zero Mock cost. Commit accepts the same body. `approved=true` explicitly authorizes protected-shot changes or replacing a selected candidate and creates an approval audit record.

## Scenes, shots and timeline

| Method | Path | Body |
| --- | --- | --- |
| GET | `/projects/{pid}/scenes`, `/projects/{pid}/shots` | None |
| POST | `/projects/{pid}/scenes` | `base_revision`, `scene` |
| POST | `/projects/{pid}/shots` | `base_revision`, `shot` |
| PATCH | `/projects/{pid}/scenes/{sid}` | `base_revision`, `patch`, optional `approved` |
| PATCH | `/projects/{pid}/shots/{sid}` | `base_revision`, `patch`, optional `approved` |
| DELETE | `/projects/{pid}/shots/{sid}` | Revision and approval request, archives the shot |
| DELETE | `/projects/{pid}/scenes/{sid}` | Archives the scene's shots while retaining history |
| POST | `/projects/{pid}/timeline` | `base_revision`, ordered `shot_ids` |

Supported shot patch fields include `title`, `action`, `emotion`, `duration_frames`, `camera.shot_size`, `camera.movement`, `locked`, `status`, `active_asset_version_id`. Status edits are limited to draft, approved and archived. Scene fields are title and description. Project changesets permit title, brief and style_bible.

## Director approval workflow

1. POST `/projects/{pid}/director` with `base_revision` and `message`. It stores a proposed plan without changing APR.
2. GET `/projects/{pid}/plans` to recover proposed plans after refresh.
3. PATCH `/projects/{pid}/plans/{plan_id}` with `base_revision` and edited `shots`. Shot IDs and scene ownership must match the proposal.
4. POST `/projects/{pid}/plans/{plan_id}/execute` with `base_revision` and `approved=true`. Execution creates scenes, shots and timeline in a single revision.

## Jobs, events and media

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/models` | Mock providers and configured remote categories, availability and cost status |
| POST | `/projects/{pid}/generate` | `base_revision`, `shot_ids`, optional `provider_id` and `capability`, returns jobs with 202 |
| POST | `/projects/{pid}/render` | `base_revision`, returns render job with 202 |
| GET | `/projects/{pid}/jobs` | Persistent job state and provenance |
| GET | `/projects/{pid}/jobs/{jid}` | One project-scoped job |
| POST | `/projects/{pid}/jobs/{jid}/cancel` | Idempotent cancel request, worker confirms local termination |
| POST | `/projects/{pid}/jobs/{jid}/retry` | Explicit retry of failed/cancelled task |
| GET | `/events?project_id={pid}&after={cursor}` | Up to 200 ordered durable events, continue from the last event_id |
| GET | `/projects/{pid}/assets`, `/projects/{pid}/manifest` | Media metadata and provenance |
| GET | `/projects/{pid}/assets/{aid}/file` | Safe media response with Range support |
| GET | `/projects/{pid}/assets/{aid}/file?download=true` | Download attachment |

Video providers are `mock-video-local` and a configured `openai-video`. The capability is `text_to_video`. Omitted provider IDs use configured defaults. Local shot duration must be between 1 and 30 seconds. Render requires valid selected media for every timeline placement. The worker normalizes clips, discards their original audio, concatenates video, mixes explicitly placed audio, burns captions and probes final output. Without placed audio, output remains silent.

Job identity hashes the provider, adapter version, shot specification, format and style. Identical requests return the existing job. Completed cache hits report `reused_from_job_id` and verify file hashes before reactivating stale selections. The worker checks current input fingerprints before publishing, so obsolete output becomes a stale candidate and a `needs_review` job.

## Errors

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "Project changed. Refresh and preview again.",
    "details": {"expected": 8, "received": 7},
    "request_id": "generated-request-uuid"
  }
}
```

Common codes include `VALIDATION_ERROR`, `NOT_FOUND`, `REVISION_CONFLICT`, `APPROVAL_REQUIRED`, `SHOT_LOCKED`, `MODEL_UNAVAILABLE`, `CAPABILITY_UNSUPPORTED`, `MISSING_ASSET`, `STALE_ASSET`, `CACHE_MEDIA_INVALID`, `FFMPEG_ERROR`, `MEDIA_TIMEOUT`, `STORAGE_ERROR` and `LEASE_EXHAUSTED`.

Externally supplied filesystem paths, arbitrary patch fields and shell commands have no execution endpoint.

## Model settings

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/settings/models` | Four category profiles and defaults, with `has_api_key` instead of secrets |
| PUT | `/settings/providers/{kind}` | Save `llm`, `video`, `image` or `speech` profile using its current `revision` |
| POST | `/settings/providers/{kind}/test` | Read `/models` using the saved profile, or an unsaved profile body |
| GET | `/settings/providers/{kind}/models` | Discover models using the saved connection |
| PUT | `/settings/defaults` | Change one or more category defaults |

Example profile body follows. Model names and endpoint URLs come from the user's service.

```json
{
  "revision": 0,
  "base_url": "https://provider.example/v1",
  "api_key": "your-service-key",
  "model": "your-model-id",
  "enabled": true,
  "allow_private_network": false,
  "timeout_seconds": 60
}
```

Blank or omitted `api_key` preserves the saved value. `clear_api_key=true` clears the current profile's credential. Existing queued jobs retain encrypted immutable connection revisions. Saving unchanged values preserves the revision. Conflicts return `CONFIG_CONFLICT` with 409.

Advanced fields include `json_mode`, `video_seconds`, `video_size`, `video_poll_seconds`, `video_timeout_seconds`, `image_size`, `speech_voice` and LLM `vision_enabled` (default false). The full request schema is available in OpenAPI. Connection tests verify model-list connectivity and authentication, with `generation_tested=false`.

## Remote generation and recovery

- Director requests accept `provider_id=mock-director|openai-llm`. External proposals require `allow_remote=true`.
- Video generation accepts `provider_id=mock-video-local|openai-video`. External requests require `allow_remote=true`.
- POST `/projects/{pid}/media` accepts `base_revision`, `operation=generate_image|generate_speech`, `prompt`, `provider_id`, `allow_remote=true` and an optional UUID `request_id`. A fresh request ID creates a new intentional auxiliary-media generation.
- Jobs bind immutable provider revision, model and generation parameters. Unknown cost is represented with `amount=null` and `status=unknown`.
- POST `/projects/{pid}/jobs/{jid}/remote` accepts `external_task_id` and `approved=true` to recover an existing remote video from a stopped local job.
- Ordinary `/retry` resumes a stored video ID. To intentionally issue a new remote request, pass `restart_remote=true, allow_remote=true`. The previous remote ID is retained in `remote_attempts`, and an approval audit entry is stored.
- Cancellation stops local work. `remote_cancel_supported=false` and `remote_notice` disclose that the remote task may continue.

Provider errors include `PROVIDER_AUTH_FAILED`, `PROVIDER_URL_BLOCKED`, `PROVIDER_ENDPOINT_UNSUPPORTED`, `PROVIDER_RATE_LIMIT`, `PROVIDER_TIMEOUT`, `PROVIDER_OUTPUT_INVALID`, `PROVIDER_RETIRED`, `REMOTE_CONSENT_REQUIRED` and `REMOTE_SUBMISSION_UNCERTAIN`. Error bodies never echo upstream responses or API keys.

Official OpenAI video generation was retired. The video adapter implements the historical `/videos` contract for explicitly configured third-party compatible services and rejects the retired official host. Other video protocols need a dedicated adapter.

## ComfyUI and provider health

Video/image profiles accept `protocol="comfyui"`, `model` as a template identifier and `workflow={graph,bindings,output_node,output_field,media_type,seed}`. `graph` is a complete runnable API-format workflow exported from ComfyUI (up to 300 nodes/2 MB). `bindings` maps required `prompt` and optional `seed,width,height,frames,fps` to `{node_id,input_name}`. Each maps to a distinct existing literal input. `media_type` must match the category; output fields are `images`, `gifs`, `videos`.

Tests read `/object_info` for ComfyUI, `/models` otherwise. Metadata tests never generate and return `generation_tested=false`, with `provisional_capabilities` for ComfyUI. Settings include revision-specific `health` with `status=not_tested|connected|failed`, check time, sanitized error code and whether actual media was verified. ComfyUI `/models` capabilities stay empty/unverified until a fenced Worker publishes valid media from that revision; first-generation choices are explicitly provisional. The Worker revalidates the graph before submission.

Provider IDs `openai-video`/`openai-image` remain stable for backward compatibility. `protocol` identifies ComfyUI in settings, requests and provenance. Worker uses `/prompt`, `/history/{prompt_id}`, `/view`; `/jobs/{jid}/remote` also restores ComfyUI image/video IDs. Cancellation never calls global `/interrupt` or clears a shared queue.

## Project budget and accounting

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/projects/{pid}/budget` | Policy, revision, totals, unknown count and ledger entries |
| PUT | `/projects/{pid}/budget` | `base_revision`, `budget_limit`, `currency`, `allow_remote`, `allow_unknown_cost` |
| POST | `/projects/{pid}/budget/entries/{entry_id}` | `base_revision`, `approved=true`, `actual_amount`, original `currency`; reconcile one pending entry once |

Profile fields `request_cost` (nullable non-negative Decimal) and `cost_currency` specify a user-supplied per-request estimate. Amounts allow six decimals, are stored as integer millionths, and totals are six-decimal strings. Mock has zero cost and no reservation.

Entries transition `reserved` → `released` if no request was submitted, or → `pending` if submitted/possibly accepted. Explicit billing reconciliation makes an entry `settled`. Intentional resubmission retains previous entries and creates a fresh reservation; cache reuse and remote-ID resume do not reserve twice. Batch reservations and enqueueing are atomic.

LLM submission intent is persisted as pending before the network call. A crash leaves a durable entry; `can_reconcile` becomes true after the request timeout plus 15 seconds (or normal completion). Known remote-ID recovery restores a released obligation even when the cap is exceeded, since the accepted task may already have incurred that cost. Budget currency invariants also apply to revision revert.

Policy edits and reconciliation use engine revision checks and ownership checks. Currency is fixed after the first ledger entry. Actual charges may exceed the cap and are still recorded; `over_budget` flags this. Unknown costs cannot be bounded by the numeric limit; both project permission and per-request remote consent are required. Estimates never become actual charges automatically.

Errors include `BUDGET_EXCEEDED`, `UNKNOWN_COST_BLOCKED`, `PROJECT_REMOTE_BLOCKED`, `BUDGET_CURRENCY_MISMATCH`, `BUDGET_CURRENCY_LOCKED`, `CHARGE_NOT_PENDING`. Real-service acceptance tests are opt-in and explicitly skipped when unconfigured; see README.

## Continuity and controlled semantic edits

APR schema0.5 adds stable backbone clip IDs, video layers and transitions, with legacy0.1–0.4 loading. Structure import creates a new draft with remapped entities, backbone/transition identities and subtitle/marker timing. Media requires regeneration, and video-layer track design is retained with empty placements. Portable ZIP recovery preserves owned media, full composition and workspace.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/projects/{pid}/continuity` | Bibles, world states, shot references and dependency edges |
| POST | `/projects/{pid}/continuity/preview` | `base_revision`, `entity_type`, optional `entity_id`, `delete`, `value`; persists a proposed plan and returns impact |
| POST | `/projects/{pid}/continuity/plans/{plan_id}/execute` | Current `base_revision`, `approved=true`; apply the reviewed plan |
| POST | `/projects/{pid}/semantic-edits` | Selected `shot_ids` (1–20), `mode=structured|llm`, explicit `fields` or a `message`; returns a bounded plan |
| POST | `/projects/{pid}/semantic-edits/{plan_id}/execute` | Current `base_revision`, `approved=true`; execute the semantic plan |

Continuity entity types are `character`, `location`, `world_state`, `shot_refs`, `scene_location` and `link`. All references must belong to the project. World states contain `scene_id`, optional `shot_id`, character costume/pose/emotion, props and environment attributes. Links contain `source_shot_id`, `target_shot_id`, `kind` and `note`. Full value schemas appear in OpenAPI.

Semantic fields are restricted to `action`, `emotion`, `camera` and `duration_frames`. LLM mode requires `allow_remote=true` plus project privacy/budget permission. Output is validated, previewed and explicitly approved; it cannot directly mutate the project or generate media. Plans are revision-bound and cannot be executed through another plan type's route.

Impact includes downstream continuity dependencies. Changed protected shots retain their selected versions and become `needs_review`; unaffected assets remain usable. Generation fingerprints include the reverse-reachable continuity content, even when the displayed prompt context is bounded.

## Audio, captions and timing

| Method | Path | Behavior |
| --- | --- | --- |
| PUT | `/projects/{pid}/timeline/composition` | `base_revision`, `audio_tracks`, `captions`, `markers`; replace composition while retaining the video backbone |
| POST | `/projects/{pid}/timeline/audio/import` | Raw audio bytes; query `base_revision`, `name`, `rights_confirmed=true`; returns a Worker job with 202 |
| POST | `/projects/{pid}/timeline/anchors/preview` | Propose timing from selected boundaries and markers or BPM |
| POST | `/projects/{pid}/timeline/anchors/commit` | Same preview body plus `approved=true` and returned `preview_token` |

There is one contiguous video track and up to 15 audio tracks, with 64 audio placements overall. Positions are project frames. Audio items have `asset_id`, `start_frame`, `source_start_frame`, `duration_frames`, `gain_db`; audio tracks add `id`, `name`, `muted`, `gain_db`, `items`. Source and timeline bounds are validated. Captions have `id`, `start_frame`, `duration_frames`, `text`. Markers have `id`, `frame`, `label`, `kind=beat|dialogue|note`.

Audio import accepts WAV/MP3/FLAC/Ogg, at most 32 MB and one hour. The API stages bytes; the Worker verifies signatures, probes and converts to canonical PCM WAV. Owned immutable asset IDs are required for composition. Audio/caption edits invalidate only the final render. The output uses H.264 and optional 48 kHz stereo AAC, with frame-exact video duration.

Anchor requests contain `base_revision`, `boundary_shot_ids`, optional `marker_ids`, optional `bpm` (20–300), `origin_frame` and `allow_protected` (false by default). Preview lists actual duration changes, all affected shots and protected descendants. Commit requires unchanged revision and preview token. Total duration remains fixed, including transition overlaps. Protected durations are skipped unless explicitly allowed. Markers may be authored, generated from BPM or approved from local audio analysis.

## Evidence reviews

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/projects/{pid}/reviews` | `base_revision`, `shot_ids` (1–20), `mode=deterministic|vision`, `frame_count` (2–5), `allow_remote`; returns Worker jobs with 202 |
| GET | `/projects/{pid}/reviews` | Persistent reports and current stale status |

Deterministic mode probes media/duration and extracts keyframes. Semantic continuity is explicitly `not_run`. Vision mode requires an enabled, vision-capable LLM profile with `vision_enabled=true`, explicit remote consent and normal project budget/privacy authorization. It sends bounded keyframes, neighboring-shot evidence and text context through the configured compatible chat endpoint. Real provider support remains subject to opt-in acceptance.

Reports bind asset/version hashes and source fingerprints, with timestamped frame asset IDs, checks, confidence and evidence indices. Model findings reject unknown fields/codes, non-finite confidence and references outside supplied frames. Publication is lease/attempt-fenced and never replaces selected footage. Reports become stale after relevant source changes. All reports require human judgment; sampled VLM output cannot establish full-duration action, lip sync or audio synchronization and never triggers automatic regeneration.

## Portable exchange exports

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/projects/{pid}/exports` | `base_revision`, `format=zip|otio`; queues an immutable snapshot export, 202 |
| GET | `/projects/{pid}/exports/{job_id}/file` | Download a completed owned artifact after path and hash verification |

Native OpenTimelineIO is a locked dependency. Missing native support returns `EXPORT_DEPENDENCY_MISSING`; the service does not substitute generic JSON. ZIP contains `project.apr.json`, `timeline.otio`, `manifest.json`, `README.txt` and hash-addressed `media/` files. Maximum uncompressed payload is 2 GB. Only project-owned, hash-verified assets are included. Settings, credentials, local paths, agent memory and runtime files are excluded. Public job responses omit internal staging/artifact paths.

Standalone OTIO uses relative media references and does not contain the referenced media; download ZIP to obtain those files. Overlapping audio clips use parallel lanes. Captions, track mute and gain are retained as AFDS metadata/markers and may need manual application in receiving editors. APR remains the richer source of project semantics.

## Portable project recovery

`POST /projects/restore?request_id=<uuid>&title=<new-copy-title>&approved=true` accepts raw ZIP bytes (`Content-Type: application/zip`), not multipart or base64. A 202 public job includes the new destination `project_id`. Keep the request UUID unchanged when retrying an uncertain upload. Identical UUID, archive SHA-256 and title return the same destination/job; different input returns 409 `RESTORE_REQUEST_CONFLICT`.

The API stages at most 2 GB and atomically creates a new empty project and `restore_project` job. Only the independent Worker parses media. It accepts AFDS export formats0.2.0/0.3.0, verifies APR/manifest/workspace snapshot hashes, native OTIO and all owned media, and rejects traversal, links, duplicate names/JSON keys, unexpected members, missing assets and unsupported formats. Limits are2GB compressed and total uncompressed,16MB combined metadata and central directory,10,005ZIP members. Global ZIP64 directories are rejected before allocation, per-member ZIP64 remains supported. Public responses omit staging paths.

Recovery remaps all entity IDs while preserving selected/locked/approved assets, audio composition, captions and markers. It never overwrites the original. A new revision lineage and zero budget are created with remote content sharing and unknown costs disabled. Credentials, task/revision/review history and cost ledger are excluded; historical provider bills are not cancelled. The job receipt identifies the source snapshot and asset counts. No provider call occurs.

Final publication rechecks lease owner, attempt, expiry, cancellation and destination revision. Media rows, APR revision and job completion publish in one short transaction. Editing the destination during recovery causes a revision conflict. Failed/cancelled jobs leave their destination visible and can use normal cancel/retry routes. Completed retries verify media integrity without restoring an obsolete APR state. Interrupted immutable files may remain unregistered; no automatic broad cleanup occurs.

## Local operations

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/system/status` | Local DB/schema, Worker liveness, FFmpeg/ffprobe/native OTIO availability and disk capacity |
| GET | `/projects/{pid}/operations` | Owned project task counts, recent runtime percentiles, registered media totals, budget summary and bounded lineage graph |

Status is `ok` or `degraded`, deployment is `local_single_user`. Worker status is `online`, `offline` or `unknown`, with online count and last-seen age; idle pulses every 5 seconds, active lease heartbeats also pulse, stale after 15 seconds. Missing dependencies and unavailable data are reported explicitly. Responses exclude filesystem paths, owner IDs, prompts, credentials and raw upstream errors; diagnostics make no external calls.

Project counts cover all jobs; elapsed p50/p95 use the most recent 200 jobs with elapsed measurements. Graph nodes cover up to 50 recent jobs and 100 input media, with explicit `truncated`. `media` edges connect observed producers/consumers or existing media; `dependency` edges reflect declared dependencies. `relationship=observed_media_lineage` does not imply queue blockage. Media totals count registered owned files, not a full storage scan. Routes are read-only and follow the existing local project ownership boundary.

## Production skills and approved local scripts

The development branch uses App0.7.0, APR0.5.0 and migration0008. Legacy APR0.1/0.2/0.3 inputs load with empty production guidance. Stage identifiers are story, continuity, shots, image, video, audio, editing, review and delivery.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/skills?stage=shots&offset=0&limit=50` | Stage metadata and bounded package summaries |
| POST | `/skills/import` | JSON files with path/content_base64, stages and optional source |
| POST | `/skills/import-zip?stages=story,shots&source=example` | Bounded raw ZIP with one skill root |
| GET | `/skills/{digest}` | Instructions, metadata and resource/script inventory |
| GET | `/skills/{digest}/resource?path=references/guide.md` | Original resource bytes, plain-text content type and nosniff |
| POST | `/projects/{pid}/skills/guidance/preview` | Preview pinned selected guidance without changing APR |
| POST | `/projects/{pid}/skills/guidance/commit` | Approved Engine transaction with base_revision |
| POST | `/projects/{pid}/skills/checks` | Enqueue a registered deterministic stage check |
| POST | `/projects/{pid}/skills/scripts/preview` | Inspect exact source, runtime, arguments, context and fingerprint |
| POST | `/projects/{pid}/skills/scripts/{plan_id}/execute` | Approve that exact preview and enqueue one script job |
| GET | `/projects/{pid}/skills/runs?stage=story&offset=0&limit=20` | Owned bounded check/script history and result metadata |
| GET | `/projects/{pid}/skills/runs/{job_id}/files/{artifact_id}` | Owned, hash-verified output download |

Guidance selection requests contain base_revision and selections, with stage keys mapping to up to three objects containing package_id and resources. Commit additionally requires approved=true. Each snapshot stores name, version, instructions, explicit text resources and original package identity. Guidance changes configure future requests while preserving selected existing media. Compiled guidance enters model requests and generation fingerprints. Snapshots survive APR import and portable restore, and matching executable packages can be reimported by digest.

Script preview accepts base_revision, package_id, script_path, stage, arguments, timeout_seconds and optional shot_ids. Arguments are literal string arrays and retain whitespace. The preview returns a plan ID, source_code, runtime availability and fingerprint. Execution requires base_revision, approved=true and the same fingerprint. The proposal is immutable. Repeated approval returns its original job, including after completion. Changed source, parameters or a new run require another preview and approval.

Scripts execute with the current local user's filesystem and network permissions. The inherited environment contains essential system variables and AFDS_CONTEXT_FILE. Context JSON is also available through stdin and input.json. The selected skill's ordinary files are present in its working directory. Root input.json and outputs/ are runtime protocol names. Results from outputs/ are copied to immutable, project-owned storage. Child-process lifecycle controls, explicit timeouts and lease fencing apply. External service costs are script-managed and recorded as unknown, while the AFDS cost ledger covers registered model requests.

Script timeout is5–300seconds. Source review supports scripts up to256KiB. Context is bounded512KiB with explicit scope truncation metadata. Logs retain64KiB per stream and terminate at1MiB per stream. Output artifacts allow32files,8MiB per file and32MiB total. Runtime errors retain bounded diagnostics. Recognized credential patterns are redacted from logs and rejected in imported resources, saved arguments and artifact files. Cancellation and crash recovery retain ownership checks. A launch lost with its Worker reports SCRIPT_RUN_UNCERTAIN, and normal job retry reports SCRIPT_APPROVAL_REQUIRED for external scripts.

Import limits are16MiB ZIP,32MiB unpacked,256members and8MiB per resource. YAML uses safe loading. Directory metadata is checked before ZIP allocation, and paths, links, reserved names, collisions and credential directories are validated. The runtime library lives in local data, while authored builtins are packaged source resources. See [the production skill guide](SKILLS.md) for the portable package format and script contract.

## Project workspace

`GET /projects/{project_id}/workspace` returns project_id, workspace_revision, initialized and document. The document stores board v2 positions, asset/note nodes, ordinary edges and hiddenShots. Viewport preferences are browser-scoped.

`PUT /projects/{project_id}/workspace` accepts base_workspace_revision and document. Engine transactions check owned references and atomically advance the workspace revision. WORKSPACE_CONFLICT returns409. Canvas saves preserve the film APR revision and media dependencies. A new workspace starts at revision0 with initialized=false. The browser migrates a legacy board only into that empty workspace and preserves unsynchronized drafts across connection failures.

HTTP project exports pin both the film and workspace revisions in one transaction. Portable ZIP0.3 adds workspace.json with its SHA256 and source revision in manifest.json. Restore validates the document, remaps project-owned references and commits it with the new project. ZIP0.2 remains supported with an empty workspace. An intervening edit to a restore destination's workspace causes a revision conflict, retaining its draft.

## Video composition

APR0.5 retains the video backbone and audio tracks, and adds video_layers and transitions. Backbone clips have stable UUID identities. Each of up to7overlay tracks contains owned image/video placements with source trim, frame timing, normalized rectangle and opacity. Transition types are dissolve, fadeblack and wipeleft, joining neighboring backbone clips with explicit overlap frames.

`POST /projects/{id}/timeline/composition/preview` accepts a full V2 composition, returning its token, total_frames, overflow changes and can_apply. `PUT /projects/{id}/timeline/composition-v2` commits the revision-checked composition. Transition changes and overflow adjustments require the approved preview token. trim_overflow explicitly permits the listed end trims. Ordinary layer edits can be saved directly. The legacy composition endpoint continues editing audio/captions/markers while preserving video layers and transitions.

`POST /projects/{id}/timeline/video/import?base_revision=...&rights_confirmed=true&name=...&extract_audio=true` accepts raw MP4/MOV/MKV/WebM bytes up to256MiB and returns202. The Worker verifies staged hashes, rejects playlists, validates video streams and normalizes H264 to project dimensions/rate. Optional extracted48kHz stereo WAV is a separate owned asset with source provenance. Interrupted uploads remove their partial staging file. Import records rights approval and uses normal cancellation/lease fencing.

`POST /projects/{id}/timeline/preview` accepts base_revision, start_frame, duration_frames and max_width160–640(even). Range is within the film and at most30seconds. The Worker uses the same normalized graph as final rendering, including overlays, transitions, audio and captions. Results are ordinary owned video assets while the selected final film is retained. Cache identity pins composition hashes, range and dimensions. UI displays preview source and current project revisions.

## Audio analysis and timestamped transcription

Model profiles add transcription with provider_id=openai-transcription and transcription_timestamps capability, disabled by default. Connections use the existing revisioned encrypted secret store, budget reservations and project privacy controls. Enable the timestamp capability for services that accept response_format=verbose_json and timestamp_granularities[]=segment and return start/end/text segments. File transcription follows the compatible audio/transcriptions endpoint. Models offering plain text require a timestamp-capable service for this workflow. See [the file transcription contract](https://developers.openai.com/api/docs/guides/speech-to-text).

- POST /projects/{pid}/timeline/analysis accepts base_revision, request_id(UUID), kind(beats|transcription), owned audio asset_id, source_start_frame, duration_frames, optional language/provider_id and allow_remote. A range spans at most30minutes and fits its source. Repeated identical request IDs return the original job. Transcription requires per-request sharing/charge approval.
- GET /projects/{pid}/timeline/analysis?limit=20&offset=0 returns bounded owned history. GET /projects/{pid}/timeline/analysis/{jid} returns a completed integrity-checked result with source hash/range. Analysis publication preserves film state. Results are immutable project-owned JSON, bounded1MiB, with at most500 transcription segments.
- POST /projects/{pid}/timeline/analysis/{jid}/preview accepts base_revision, selected_indices, target_start_frame and optional explicit replace_caption_ids. It returns the exact additions/removals, result hash and preview_token. POST .../commit also requires approved=true and that token. Commit runs through Engine revision checks. Existing subtitles are retained by default, frame-rate changes require another analysis, and token changes cause409.

Worker normalizes the range to16kHz mono. NumPy2.3.5 energy-onset analysis reports beat candidates, estimated BPM and optional periodic consistency. Silence returns no candidates. For large transcription input, local64kbps MP3 compression keeps uploads within25MiB before remote submission. Transcription validates finite, ordered, non-overlapping timed segments against the selected range. Missing timestamps and excessive segments require a compatible model or shorter range. Paid submissions pin the profile revision and record intent before HTTP. Unknown outcomes retain the charge obligation and prevent automatic resubmission. The UI retains request identity across a lost response and background revisions, and preserves candidate drafts on conflicts. Real transcription quality/billing acceptance awaits configured credentials.

Native OTIO exports represent overlapping transition handles explicitly. A D-frame overlap uses floor(D/2) incoming and remaining outgoing offsets, trimming the nominal clip ranges while retaining full available media handles. Two48frame clips therefore produce84frames with a12frame overlap and85frames with11. Overlay tracks are native Stacks of gap/clip lanes, retaining placement/coverage order and hidden state. Rectangle/opacity use versioned AFDS.Transform effect metadata. Receiving editors apply that effect mapping and custom black-fade/wipe metadata. ZIP recovery uses APR as the full composition source and remaps backbone/layer/transition identities, versions and workspace references. Editing/delivery checks and render job errors identify affected clips. System diagnostics verify required FFmpeg composition filters, cached by binary modification time.
