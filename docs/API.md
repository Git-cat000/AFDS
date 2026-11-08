# AFDS v0.3 API

Base path is `/api/v1`. The development API is available at `http://127.0.0.1:8000`. Interactive schemas are at `/docs` and `/openapi.json`.

Project, scene, shot, asset and job IDs are UUIDs. Provider slots use category identifiers. Project changes require `base_revision`. The server checks revision inside the same SQLite write transaction. Conflicts return HTTP 409. Mutations use JSON, including soft-archive DELETE requests. This release serves a single local user.

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

Video providers are `mock-video-local` and a configured `openai-video`. The capability is `text_to_video`. Omitted provider IDs use configured defaults. Local shot duration must be between 1 and 30 seconds. Render requires valid selected media for every timeline placement. The worker normalizes clips, discards audio, concatenates and probes final output.

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

Advanced fields include `json_mode`, `video_seconds`, `video_size`, `video_poll_seconds`, `video_timeout_seconds`, `image_size` and `speech_voice`. The full request schema is available in OpenAPI. Connection tests verify model-list connectivity and authentication, with `generation_tested=false`.

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
