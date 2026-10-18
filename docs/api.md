<!-- Purpose: Define the concrete FastAPI/Pydantic contract between the frontend Chatbot page and the agentic backend. This is the specification the backend is implemented against. -->

# API

## 1. Ground Rules

- **FastAPI** is the HTTP layer. **Pydantic** validates every request and response.
- Route handlers stay thin. Agent reasoning, orchestration and tool access live in
  service / graph modules (`skills.md` §8, §23).
- No API key, token, internal prompt or stack trace ever appears in a response.
- Base path: **`/api/v1`**. All JSON, UTF-8.

### 1.1 Authentication

**Decided: Supabase Auth, multi-user.** `docs/database.md` §1.3 owns this
decision; this section owns how it appears over HTTP.

**Status: IMPLEMENTED 2026-10-09.** Every `/projects` route requires a verified
bearer token and is scoped to the caller. `backend/api/deps.py` owns the
dependency; `backend/auth/jwt.py` owns verification.

#### The contract

- The frontend authenticates against **Supabase Auth** and receives a JWT.
- It sends that JWT to this backend as `Authorization: Bearer <token>`.
- The backend **verifies** the token and derives the user id from it. It never
  reads an identity from a path, a query parameter or a request body: a client
  that can name its own user can claim anyone's project
  (`docs/database.md` §1.3.5).
- Every `/projects` route becomes a protected route taking that identity as a
  FastAPI dependency. **Route shapes and response models do not change** — what
  changes is which rows they can reach.
- `GET /projects` returns **only the caller's** projects.
- A project belonging to another user answers **`404 project_not_found`**, not
  `403`. `403` would confirm the id exists, which is a membership oracle; `404`
  tells an attacker nothing. A caller who genuinely owns nothing and a caller
  probing ids get the same answer.
- Two new codes join §5: `unauthenticated` (401) for a missing, malformed or
  expired token, and `forbidden` (403) reserved for a verified user attempting
  something their account is not permitted to do — never for mere ownership,
  which is `404`.

#### Network posture, still unchanged

Auth is implemented, but the loopback binding stays until it is reviewed as a
whole. These remain requirements:

- The server binds to `127.0.0.1` only.
- CORS is restricted to the Vite origin (`http://localhost:5173`) via an
  explicit allowlist from config.
- **The server must not be exposed publicly.** Knowing a `project_id` is
  currently sufficient to read and mutate that project.

The loopback constraint exists *because* there is no auth yet. It is lifted by
implementing this section — and by resolving `docs/database.md` §5.2 #7, since
a backend that connects as a superuser bypasses RLS and would make the database
half of the boundary decorative.

## 2. Endpoint Contract

> **Implementation status (verified 2026-10-09).** This section is the target
> contract, and part of it does not exist yet. Implemented in
> `backend/api/projects.py` and `backend/api/preview.py`: health; `POST`/`GET
> /projects`; `GET /projects/{id}`; `/status`; `/preview`; `POST
> /reference-images`; `GET`/`POST /hitl`; `/hitl/history`; `DELETE`; and the
> static `/preview/{id}/…` router.
>
> **Specified below but absent:** the SSE `/events` stream, `POST /messages`,
> `/build`, `/files`, `/evaluation`, `/evaluation/fixes`, reference-image
> list/fetch, and all three `/external-actions` routes. Clients must not assume
> them; `frontend/src/lib/api.ts` deliberately implements only the real set and
> polls `/status` in place of SSE.
>
> **None of these routes is authenticated or owner-scoped yet** (§1.1).

### 2.1 Health

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `GET` | `/api/v1/health` | — | `HealthResponse` | 200, 503 |

`HealthResponse`: `{ status: "ok" | "degraded", version: str, model_configured: bool }`

Never echo the API key or any secret value. `model_configured` is a boolean only.

### 2.2 Projects & sessions

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `POST` | `/api/v1/projects` | `CreateProjectRequest` | `ProjectCreatedResponse` | 201, 422, 429, 503 |
| `GET` | `/api/v1/projects` | — | `ProjectSummaryList` | 200 |
| `GET` | `/api/v1/projects/{project_id}` | — | `ProjectStateResponse` | 200, 404 |
| `GET` | `/api/v1/projects/{project_id}/status` | — | `StatusResponse` | 200, 404 |
| `POST` | `/api/v1/projects/{project_id}/messages` | `UserMessageRequest` | `MessageAcceptedResponse` | 202, 404, 409, 422 |
| `DELETE` | `/api/v1/projects/{project_id}` | — | — | 204, 404, 409 |

- `POST /projects` starts Requirement Analysis and returns immediately (202-style
  semantics with a 201 because a resource is created). The workflow runs in the
  background; progress arrives over SSE.
- `GET .../status` is the cheap polling endpoint (SSE fallback). `GET .../{project_id}`
  returns the full public state projection.
- `POST .../messages` submits a refinement or a follow-up instruction outside a gate.
  `409` when the project is mid-stage and cannot accept input.
- `DELETE` returns `409` while a build or external action is in flight.

### 2.3 HITL

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/hitl` | — | `HitlCheckpoint` or `204` | 200, 204, 404 |
| `POST` | `/api/v1/projects/{project_id}/hitl` | `HitlDecisionRequest` | `HitlDecisionAccepted` | 202, 404, 409, 410, 422 |
| `GET` | `/api/v1/projects/{project_id}/hitl/history` | — | `HitlHistoryResponse` | 200, 404 |

- `204` means no checkpoint is currently pending.
- `409` — the project is not awaiting HITL.
- `410` — `checkpoint_id` refers to a checkpoint that was already resolved
  (stale client, duplicate submit). This must not be treated as a validation error.

### 2.4 Reference images

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `POST` | `/api/v1/projects/{project_id}/reference-images` | `multipart/form-data` | `ReferenceImageResponse` | 201, 404, 413, 415, 422 |
| `GET` | `/api/v1/projects/{project_id}/reference-images` | — | `ReferenceImageList` | 200, 404 |
| `GET` | `/api/v1/projects/{project_id}/reference-images/{image_id}` | — | image bytes | 200, 404 |

- `413` — over `MAX_UPLOAD_BYTES`. `415` — MIME type not allowlisted.
- Validation rules are in `docs/security.md` §4. Content type is confirmed by magic
  bytes, not by the declared header.
- The client-supplied filename is **never** used as a path component.

### 2.5 Build & preview

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/build` | — | `BuildStatusResponse` | 200, 404 |
| `GET` | `/api/v1/projects/{project_id}/preview` | — | `PreviewResponse` | 200, 404, 409 |
| `GET` | `/api/v1/projects/{project_id}/files` | — | `GeneratedFileManifest` | 200, 404 |
| `GET` | `/api/v1/projects/{project_id}/files/{file_path:path}` | — | file content | 200, 404 |

- `409` on `/preview` when no successful build exists yet.

**The preview URL carries a signed, short-lived capability.**
`PreviewResponse.preview_url` is `/preview/{token}/`, **not**
`/preview/{project_id}/`. The static route cannot authenticate anyone — a
browser tab or an iframe issues a plain GET and cannot attach an
`Authorization` header — so the right to read a project's built output travels
in the URL instead:

| Property | Value |
|---|---|
| Algorithm | HS256, **pinned** on verify (`backend/auth/preview_token.py`) |
| Signing key | `PREVIEW_URL_SECRET`; **required** when `DATABASE_URL_APP` is set, and startup fails without it |
| TTL | 5 minutes; `expires_at` is returned alongside |
| Claims | `pid`, `sub`, `scope`, `exp`, `iat`, `nbf`, `jti` — all required |
| Scope | one project's `dist/` subtree, not a single file |

Issuance happens only on this owner-scoped route, so a token is never minted
for a project the caller does not own. Every request to `/preview/{token}/...`
then re-checks that the project still exists and is still owned by `sub`, so a
token does not outlive a deletion or an ownership change.

Three independent controls, none of which replaces another: token validity,
the ownership re-check, and path containment within `dist/`
(`docs/security.md` §3.4). **Every failure returns a bare `404`** — expired,
forged, another user's, traversal and missing file are indistinguishable to a
caller.

The URL is a credential. Clients must not cache, log or share it, and should
re-request this endpoint rather than storing the string.

> **This closes a verified cross-user leak.** `/preview/{project_id}/*`
> previously had no authentication at all: a probe read user A's built
> `index.html` as user B, and with no credentials, receiving `200` and A's
> content both times. Old `/preview/{project_id}/...` URLs now `404`.
- `file_path` is resolved against the project workspace and must stay inside it
  (`docs/security.md` §3.4). Traversal attempts return `404`, never the file.

### 2.5a GitHub integration (implemented 2026-10-11)

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `GET` | `/api/v1/github/status` | — | `GitHubStatusResponse` | 200, 401 |
| `GET` | `/api/v1/github/repositories` | — | `RepositoryList` | 200, 401, 501, 502 |
| `GET` | `/api/v1/projects/{id}/github` | — | `GitHubLinkResponse` | 200, 401, 404 |
| `PUT` | `/api/v1/projects/{id}/github` | `SelectRepositoryRequest` | `GitHubLinkResponse` | 200, 401, 404, 409, 501 |
| `DELETE` | `/api/v1/projects/{id}/github` | — | — | 204, 401, 404 |
| `POST` | `/api/v1/projects/{id}/github/push/preview` | — | `SyncPlanResponse` | 200, 401, 404, 409, 501 |
| `POST` | `/api/v1/projects/{id}/github/push` | `ExecuteSyncRequest` | `SyncResultResponse` | 200, 401, 403, 404, 409, 410, 502 |

**The user supplies a URL.** `SelectRepositoryRequest.repository` accepts a
GitHub URL (`https://github.com/owner/repo`, with or without `.git`, a trailing
slash, a deeper path such as `/tree/main`, or a query), an SSH clone string
(`git@github.com:owner/repo.git`, `ssh://…`), a bare `github.com/owner/repo`,
or plain `owner/repo`. All reduce to two segments validated against GitHub's
naming rules; traversal is refused. Selection **verifies that the repository
exists and that the credential can push to it** before storing anything, so a
read-only repository is rejected at connect time rather than at first push.

**Scope.** Pushing a generated project into the user's existing repository is
the ONLY sync operation. DETROIT never creates a repository. Pull/import and
pull-request creation were implemented and then **removed** as out of scope —
re-adding either means re-adding its approval path and its tests deliberately.

**Two-phase, like every external write.** `*/preview` mutates nothing and
returns a plan plus a single-use, short-TTL `approval_token`. `*/push` and
`*/pull` refuse without it: `403 approval_required` for an invalid token,
`410 approval_expired` for one already spent or past its TTL. An approval is
bound to its project and never generalises to another.

**What push synchronises.** Only `src/`.

| | Push |
|---|---|
| Direction | workspace `src/` → branch |
| Outside `src/` | not sent — the rest is the frozen template and build output |
| Deletions | never — the commit is additive (`base_tree` preserved) |
| History | never rewritten; the ref moves **without force** |
| Conflict | branch moved since the plan → `409`, never an overwrite |
| Unsupported | non-UTF-8 and oversized files skipped, each with a reason |

`SyncChange.kind` is `add`, `update` or `skip`. There is no `conflict` or
`unchanged` kind: both belonged to the removed pull path.

**No credential is ever returned.** `GitHubStatusResponse` carries booleans and
an account name. `configured` is verified by a real call, so a revoked or
wrong-scoped token reports `false` rather than "connected".

### 2.6 Evaluation

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/evaluation` | — | `EvaluationReport` | 200, 404, 503 |
| `POST` | `/api/v1/projects/{project_id}/evaluation/fixes` | `TargetedFixRequest` | `FixAcceptedResponse` | 202, 404, 409, 422 |

`409` when `iteration_count >= MAX_AUTO_ITERATIONS` (3). The response body explains
the limit was reached and that user input is required — this is a normal workflow
outcome, not a server error.

`503 quality_gate_blocked` when Impeccable is unavailable. Impeccable is a
**mandatory** gate (`docs/design.md` §5): the workflow stops, `run_status` becomes
`blocked`, and the API must never report the evaluation as completed or passed.

### 2.7 External actions (MCP)

Two-phase, so a destructive action can never be a side effect of an ordinary call:

| Method | Path | Request | Response | Codes |
|---|---|---|---|---|
| `POST` | `/api/v1/projects/{project_id}/external-actions/preview` | `ExternalActionRequest` | `ExternalActionPlan` | 200, 404, 422, 501, 503 |
| `POST` | `/api/v1/projects/{project_id}/external-actions` | `ExternalActionExecuteRequest` | `ExternalActionResult` | 202, 403, 404, 409, 410, 422, 502 |
| `GET` | `/api/v1/projects/{project_id}/external-actions` | — | `ExternalActionHistory` | 200, 404 |

- The preview call performs **no** mutation. It returns a human-readable plan of
  exactly what would change, plus a single-use, short-TTL `approval_token`.
- Execution requires that token. `403` without a valid token, `410` if expired or
  already spent.
- `501` when the requested capability is not present on the connected MCP server —
  capabilities are discovered, never assumed (`docs/mcp.md` §4).
- `502` for upstream MCP/provider failures.

### 2.8 Event stream

| Method | Path | Response |
|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/events` | `text/event-stream` |

**SSE is the primary progress channel**, chosen over WebSocket because the traffic is
one-directional (server → client); all client actions are ordinary POSTs. It survives
proxies, reconnects natively via `Last-Event-ID`, and needs no extra dependency.

Event types:

| `event` | `data` payload | Meaning |
|---|---|---|
| `stage_changed` | `{ stage, status, previous_stage }` | workflow advanced |
| `agent_started` | `{ agent_role, stage }` | an agent began |
| `agent_completed` | `{ agent_role, stage, ok }` | an agent finished |
| `hitl_required` | `HitlCheckpoint` | a gate is now blocking |
| `build_progress` | `{ step, pct, message }` | build step update |
| `preview_ready` | `{ preview_url, built_at }` | preview available |
| `evaluation_finding` | `EvaluationFinding` | one finding, streamed as produced |
| `iteration_started` | `{ iteration_count, max_iterations }` | auto-correction loop |
| `error` | `ErrorBody` | safe, user-facing failure |
| `heartbeat` | `{ ts }` | keep-alive every 15s |

Every event carries a monotonic `id` so a reconnecting client can resume with
`Last-Event-ID`. Clients that cannot use SSE poll `GET .../status`.

## 3. Shared Enums

### 3.1 `WorkflowStage`

One-to-one with `docs/workflow.md` §1:

```text
requirement_analysis
planning
design
reference_analysis
frontend_architecture
build
preview
evaluation
targeted_fix
re_evaluation
final_validation
external_action
```

### 3.2 `RunStatus`

```text
idle            # created, not started
running         # an agent or build step is executing
awaiting_hitl   # blocked on a user decision
completed       # final validation passed
blocked         # a mandatory gate cannot run (e.g. Impeccable unavailable)
failed          # unrecoverable; see last_error
cancelled       # user cancelled
```

`stage` and `status` are orthogonal and always reported together. The Chatbot page
renders the pair — e.g. `stage=evaluation, status=running` displays as "Evaluating".
This satisfies the requirement that the chatbot can explain: current stage, waiting
for HITL, building, evaluating, fixing, completed, failed.

### 3.3 `HitlAction`

```text
approve          # accept the result as-is
select_option    # choose one AI-generated modification option
custom           # free-text instruction that overrides/refines the stage
reject           # reject without direction; returns to the stage for a new attempt
```

### 3.4 `AgentRole`

Matches `docs/agents.md`: `requirement_analyzer`, `planner`, `design_director`,
`reference_analyzer`, `frontend_architect`, `builder`, `visual_ux_critic`,
`anti_slop_reviewer`, `accessibility_reviewer`, `code_reviewer`, `fixer`,
`github_operator`.

### 3.5 `Severity`

```text
blocker | major | minor | info
```

## 4. Request / Response Models

Field types are Pydantic. `?` marks optional.

```text
CreateProjectRequest
  prompt: str (1..8000)
  title: str? (1..120)
  surface_hint: str?            # e.g. "landing_page" | "pricing_page"

ProjectCreatedResponse
  project_id: str               # "proj_<32 hex>"
  session_id: str               # "sess_<32 hex>"
  stage: WorkflowStage
  status: RunStatus
  created_at: datetime

ProjectSummary
  project_id, title, stage, status, updated_at

StatusResponse
  project_id: str
  stage: WorkflowStage
  status: RunStatus
  iteration_count: int
  max_iterations: int
  awaiting_checkpoint_id: str?
  preview_available: bool
  last_error: ErrorBody?
  updated_at: datetime

ProjectStateResponse            # public projection of ProjectState (docs/state.md §2)
  project_id, session_id, title
  stage, status, iteration_count
  normalized_requirements: NormalizedRequirements?
  plan: Plan?
  design_spec: DesignSpec?
  reference_analysis: ReferenceAnalysis?
  frontend_architecture: FrontendArchitecture?
  component_tree: ComponentTree?
  generated_files: FileRef[]
  evaluation_results: EvaluationReport?
  approved_decisions: ApprovedDecision[]
  hitl_history: HitlRecord[]
  mcp_action_history: McpActionRecord[]
  created_at, updated_at
  # NEVER included: raw prompts, provider keys, internal agent reasoning,
  # file system paths, checkpointer internals

UserMessageRequest
  message: str (1..8000)

MessageAcceptedResponse
  accepted: bool
  stage: WorkflowStage
  status: RunStatus

HitlCheckpoint
  checkpoint_id: str            # "ckpt_<32 hex>"
  stage: WorkflowStage
  title: str
  summary: str                  # what the agent produced, in user terms
  result_ref: str               # which state field holds the result
  options: HitlOption[]         # 0..N, derived from the actual result
  allows_custom: bool           # always true
  created_at: datetime

HitlOption
  option_id: str
  label: str
  description: str
  impact: str                   # what changes if chosen

HitlDecisionRequest
  checkpoint_id: str
  action: HitlAction
  option_id: str?               # required iff action == select_option
  instruction: str? (1..4000)   # required iff action == custom
  # validated: exactly one of option_id / instruction per action

HitlDecisionAccepted
  accepted: bool
  stage: WorkflowStage
  status: RunStatus
  resumed: bool

HitlRecord
  checkpoint_id, stage, action, option_id?, instruction?, decided_at

ReferenceImageResponse
  image_id: str                 # server-generated; client filename never reused
  content_type: str
  size_bytes: int
  sha256: str
  uploaded_at: datetime

BuildStatusResponse
  status: "pending" | "running" | "succeeded" | "failed"
  step: str?
  started_at: datetime?
  finished_at: datetime?
  error: ErrorBody?             # sanitized build failure summary

PreviewResponse
  preview_url: str              # "/preview/{token}/" — signed, 5-minute capability
  built_at: datetime
  status: "fresh" | "stale"     # stale = files changed since the last build
  expires_at: datetime          # when preview_url stops working

GeneratedFileManifest
  files: FileRef[]
FileRef
  path: str                     # workspace-relative, forward slashes
  size_bytes: int
  sha256: str
  updated_at: datetime

EvaluationReport
  iteration: int
  impeccable_available: bool      # false -> the gate is BLOCKED; not a valid result
  impeccable_version: str?        # actual installed version; never assumed
  gate_status: "passed" | "failed" | "blocked"
  findings: EvaluationFinding[]
  generated_at: datetime
  # INVARIANT: impeccable_available == false  =>  gate_status == "blocked"
  # A blocked gate may never be reported as passed.

EvaluationFinding
  finding_id: str
  source: AgentRole
  category: str                 # e.g. "anti_slop", "a11y", "ux", "code"
  severity: Severity
  summary: str
  location: str?                # file path and/or component name
  suggested_fix: str?
  conflicts_with_approved_decision: bool   # true -> must go to the user, not the Fixer

TargetedFixRequest
  finding_ids: str[]            # empty = all blocking/major findings
  instruction: str? (1..4000)

FixAcceptedResponse
  accepted: bool
  iteration_count: int
  max_iterations: int

ExternalActionRequest
  action_type: "github_branch" | "github_commit" | "github_push" | "github_pr"
  params: dict                  # validated per action_type

ExternalActionPlan
  plan_id: str
  action_type: str
  summary: str                  # exactly what will change, in user terms
  changes: str[]
  approval_token: str           # single-use, short TTL
  expires_at: datetime

ExternalActionExecuteRequest
  plan_id: str
  approval_token: str

ExternalActionResult
  action_id: str
  action_type: str
  status: "succeeded" | "failed"
  detail: str
  performed_at: datetime

McpActionRecord
  action_id, action_type, status, performed_at
```

## 5. Errors

Every non-2xx response uses one envelope:

```text
ErrorResponse
  error: ErrorBody

ErrorBody
  code: str        # stable machine-readable, e.g. "iteration_limit_reached"
  message: str     # safe, user-facing, actionable
  details: dict?   # safe structured context only
  request_id: str  # correlates with server logs
```

### Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `validation_error` | 422 | request model failed validation |
| `unauthenticated` | 401 | missing, malformed or expired bearer token (§1.1) |
| `forbidden` | 403 | verified user, but not permitted. **Never used for ownership** — see below |
| `project_not_found` | 404 | unknown `project_id`, **or a project owned by someone else** (§1.1) |
| `invalid_stage_for_action` | 409 | action not valid in the current stage |
| `no_pending_checkpoint` | 409 | HITL submitted with nothing pending |
| `checkpoint_already_resolved` | 410 | stale or duplicate HITL submit |
| `iteration_limit_reached` | 409 | 3 automatic corrections already used |
| `upload_too_large` | 413 | over `MAX_UPLOAD_BYTES` |
| `unsupported_media_type` | 415 | MIME not allowlisted / magic bytes mismatch |
| `preview_not_available` | 409 | no successful build yet |
| `quality_gate_blocked` | 503 | Impeccable unavailable — mandatory gate cannot run; workflow stopped |
| `build_failed` | 500 | sanitized build failure |
| `approval_required` | 403 | external action attempted without a valid token |
| `approval_expired` | 410 | token expired or already used |
| `capability_unavailable` | 501 | MCP server does not expose that capability |
| `upstream_error` | 502 | model provider or MCP failure |
| `rate_limited` | 429 | provider or local throttle |
| `internal_error` | 500 | unexpected; details withheld |

**Never leaked in any error:** API keys, environment values, stack traces, internal
prompts, absolute filesystem paths, MCP credentials, raw provider responses
(`docs/security.md` §6). Full detail goes to the server log, correlated by
`request_id`.

## 6. Frontend Consumption Summary

```text
POST /projects                       -> project_id, session_id
GET  /projects/{id}/events  (SSE)    -> live stage/status/progress/findings
GET  /projects/{id}/hitl             -> pending gate (or 204)
POST /projects/{id}/hitl             -> approve | select_option | custom | reject
GET  /projects/{id}/preview          -> preview URL once built
GET  /projects/{id}/status           -> polling fallback if SSE is unavailable
```

**Which surfaces call the backend (verified 2026-10-09).** This was previously
"only the Chatbot Page", which is no longer true:

| Surface | Calls the backend |
|---|---|
| `/chat` | yes — create, status polling, HITL, reference upload |
| `/dashboard` | yes — `GET /projects`, plus `/{id}` and `/{id}/status` per project |
| `/login` | **not yet.** It is a placeholder; when wired it talks to **Supabase Auth**, not to this API |
| `/`, `/features` | no — static |
| `/pricing` | n/a — not built |

Every call goes through `frontend/src/lib/api.ts`. No page builds a URL or
reads `import.meta.env` itself, which is what keeps the auth header a
one-line change in a single file when §1.1 lands.
