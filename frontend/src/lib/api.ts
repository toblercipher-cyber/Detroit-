/**
 * Backend transport for the DETROIT builder API.
 *
 * WHY THIS FILE EXISTS
 *   The Chatbot page is the only surface that talks to the builder backend
 *   (`docs/frontend.md` §4.6), and until now no transport existed anywhere in
 *   the frontend — `pages/chat.tsx` carries hand-transcribed copies of the
 *   backend vocabulary and renders illustrative state. This file is the single
 *   place that knows the wire format, so a page component never builds a URL,
 *   never reads `import.meta.env`, and never decodes an error envelope itself.
 *
 * LAYER
 *   Frontend infrastructure (`src/lib/`). It imports nothing from `components/`
 *   or `pages/`, so no page can create a cycle through it. It renders nothing.
 *
 * WHAT IT OWNS
 *   - the API base URL and how a backend-relative path becomes absolute
 *   - TypeScript mirrors of the Pydantic request/response models
 *   - the one `ErrorResponse` envelope, decoded into a typed `ApiError`
 *   - one function per route that is ACTUALLY IMPLEMENTED in the backend
 *
 * WHAT IT DOES NOT OWN
 *   React state, retry/backoff policy, caching, or any notion of a "session".
 *   Those belong to the page or to a hook built on top of this.
 *
 * ---------------------------------------------------------------------------
 * THE CONTRACT HERE IS THE CODE, NOT THE SPEC
 *
 * `docs/api.md` specifies more endpoints than `backend/api/` implements. Every
 * function below was written against the actual route table in
 * `backend/api/projects.py` and `backend/api/preview.py`, and the actual
 * Pydantic models in `backend/schemas/api.py` and `backend/schemas/state.py`.
 *
 * Specified but NOT IMPLEMENTED in the backend, and therefore deliberately
 * absent from this client — adding a function for one would be inventing an
 * endpoint:
 *
 *   GET  /projects/{id}/events            SSE. Does not exist. There is no
 *                                         StreamingResponse anywhere in the
 *                                         backend. See POLLING below.
 *   POST /projects/{id}/messages          `backend/api/projects.py` says so
 *                                         explicitly: ProjectState has nowhere
 *                                         to put a message.
 *   GET  /projects/{id}/build             no route
 *   GET  /projects/{id}/files[/{path}]    no route
 *   GET  /projects/{id}/evaluation        no route (the report is reachable via
 *                                         GET /projects/{id} -> evaluation_results)
 *   POST /projects/{id}/evaluation/fixes  no route
 *   GET  /projects/{id}/reference-images  no list route (upload exists)
 *   .../external-actions (all three)      no routes; `services/external_actions.py`
 *                                         does not execute anything
 *
 * POLLING, NOT A FAKE EVENT STREAM
 *   Because SSE does not exist, `watchProjectStatus` polls
 *   `GET /projects/{id}/status`. That is not a workaround invented here:
 *   `docs/api.md` §2.8 names it as the supported fallback — "Clients that
 *   cannot use SSE poll GET .../status" — and the route returns stage, status,
 *   iteration_count, the pending checkpoint id, preview availability and
 *   last_error, which covers what the workspace needs to render. It is not
 *   dressed up as an event stream and emits no event type the backend does not
 *   produce.
 *
 * NO AUTH, BY DESIGN
 *   `docs/api.md` §1.1: there is no authentication in v1. The backend binds to
 *   loopback and CORS is an explicit allowlist with `allow_credentials=False`,
 *   so requests here send no credentials and no `Authorization` header. There
 *   is also nothing to put in one — no key, no token, ever, in frontend code
 *   (`docs/security.md` §2).
 *
 * CORS NOTE THAT AFFECTS THIS FILE
 *   The backend allows methods GET/POST/DELETE and the single request header
 *   `Content-Type`. It sets no `expose_headers`, so the `X-Request-ID` response
 *   header is NOT readable from JavaScript. The correlation id is therefore
 *   read from the error body, which is where the backend also puts it.
 * ---------------------------------------------------------------------------
 */

/* ------------------------------------------------------------------ *
 * Configuration
 * ------------------------------------------------------------------ */

/**
 * Where the builder backend listens.
 *
 * Default matches the backend's own defaults: `API_HOST=127.0.0.1`,
 * `API_PORT=8000` (`docs/runtime.md` §3.2). Override with `VITE_API_BASE_URL`
 * in a frontend `.env` — which must hold a URL and nothing else. No secret is
 * ever read here; the provider key lives server-side only.
 */
import { getAccessToken } from "@/lib/auth";

export const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"
)
  .toString()
  .replace(/\/+$/, "");

/** Every typed route lives under this prefix (`docs/api.md` §1). */
const API_PREFIX = "/api/v1";

/* ------------------------------------------------------------------ *
 * Shared vocabulary — `backend/schemas/enums.py`.
 * These values are the wire format. Changing one is a breaking change.
 * ------------------------------------------------------------------ */

/** `WorkflowStage`, in pipeline order. */
export const WORKFLOW_STAGES = [
  "requirement_analysis",
  "planning",
  "design",
  "reference_analysis",
  "frontend_architecture",
  "build",
  "preview",
  "evaluation",
  "targeted_fix",
  "re_evaluation",
  "final_validation",
  "external_action",
] as const;
export type WorkflowStage = (typeof WORKFLOW_STAGES)[number];

/** `RunStatus`. Orthogonal to the stage; the two are always reported together. */
export const RUN_STATUSES = [
  "idle",
  "running",
  "awaiting_hitl",
  "completed",
  "blocked",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/** `HitlAction`. */
export type HitlAction = "approve" | "select_option" | "custom" | "reject";

/** `AgentRole` — the 12 v1 roles. */
export type AgentRole =
  | "requirement_analyzer"
  | "planner"
  | "design_director"
  | "reference_analyzer"
  | "frontend_architect"
  | "builder"
  | "visual_ux_critic"
  | "anti_slop_reviewer"
  | "accessibility_reviewer"
  | "code_reviewer"
  | "fixer"
  | "github_operator";

/** `Severity`, most severe first. */
export type Severity = "blocker" | "major" | "minor" | "info";

/** Status vocabularies Pydantic declares as `Literal` rather than as enums. */
export type GateStatus = "passed" | "failed" | "blocked";
export type PreviewFreshness = "fresh" | "stale";

/**
 * Timestamps cross the wire as ISO-8601 UTC strings, not `Date` objects.
 * Kept as strings so nothing is silently reinterpreted in a local timezone;
 * a caller that wants a `Date` constructs one deliberately.
 */
export type IsoDateTime = string;

/* ------------------------------------------------------------------ *
 * Response models — `backend/schemas/state.py` and `schemas/api.py`.
 * ------------------------------------------------------------------ */

/** `ErrorBody`. Safe, user-facing. Never carries secrets, paths or traces. */
export interface ErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
  request_id?: string | null;
}

/** The single envelope on every non-2xx response. */
export interface ErrorResponse {
  error: ErrorBody;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  /** Boolean only. The key itself is never exposed (`docs/runtime.md` §3.4). */
  model_configured: boolean;
}

export interface FileRef {
  path: string;
  size_bytes: number;
  sha256: string;
  updated_at: IsoDateTime;
}

export interface HitlOption {
  option_id: string;
  label: string;
  description: string;
  /** What changes if this option is chosen. */
  impact: string;
}

/** A gate currently blocking the workflow. `options` may legitimately be empty. */
export interface HitlCheckpoint {
  checkpoint_id: string;
  stage: WorkflowStage;
  title: string;
  summary: string;
  /** Which state field holds the result under review. */
  result_ref: string;
  options: HitlOption[];
  /** Always true — the custom path must be able to override the stage. */
  allows_custom: true;
  created_at: IsoDateTime;
}

export interface HitlRecord {
  checkpoint_id: string;
  stage: WorkflowStage;
  action: HitlAction;
  option_id?: string | null;
  instruction?: string | null;
  decided_at: IsoDateTime;
}

export interface ApprovedDecision {
  decision_id: string;
  stage: WorkflowStage;
  statement: string;
  origin?: HitlAction | null;
  decided_at: IsoDateTime;
}

export interface McpActionRecord {
  action_id: string;
  action_type: string;
  status: "succeeded" | "failed";
  performed_at: IsoDateTime;
}

export interface EvaluationFinding {
  finding_id: string;
  source: AgentRole;
  /** e.g. "anti_slop", "a11y", "ux", "code". */
  category: string;
  severity: Severity;
  summary: string;
  location?: string | null;
  suggested_fix?: string | null;
  /** True -> goes to the user, never to the Fixer (`docs/design.md` §5.5). */
  conflicts_with_approved_decision: boolean;
}

/**
 * One evaluation pass.
 *
 * The backend enforces the invariant structurally: `impeccable_available: false`
 * implies `gate_status: "blocked"`. A blocked gate is never a pass, and the UI
 * must not render it as one (CLAUDE.md §15).
 */
export interface EvaluationReport {
  iteration: number;
  impeccable_available: boolean;
  /** The actually installed version, or null. Never assumed. */
  impeccable_version?: string | null;
  gate_status: GateStatus;
  findings: EvaluationFinding[];
  generated_at: IsoDateTime;
}

export interface NormalizedRequirements {
  goal: string;
  audience?: string | null;
  surfaces: string[];
  sections: string[];
  explicit_decisions: string[];
  interactions: string[];
  responsive_requirements: string[];
  constraints: string[];
  acceptance_criteria: string[];
  conflicts: string[];
  gaps: string[];
  /** Drives adaptive HITL: detailed input earns fewer questions. */
  input_density: "sparse" | "detailed";
}

export interface Plan {
  objective: string;
  audience?: string | null;
  pages: string[];
  sections: string[];
  content_hierarchy?: string | null;
  cta_strategy?: string | null;
  interactions: string[];
  responsive_requirements: string[];
  design_intent?: string | null;
  technical_constraints: string[];
  acceptance_criteria: string[];
  open_questions: string[];
}

/** The single holder of the project's visual decisions. */
export interface DesignSpec {
  font_families: string[];
  typography_scale: Record<string, string>;
  weights: string[];
  color_system: Record<string, string>;
  spacing_rules: string[];
  layout_grid?: string | null;
  density?: string | null;
  radius?: string | null;
  button_language?: string | null;
  card_language?: string | null;
  image_treatment?: string | null;
  motion_principles: string[];
  visual_tone?: string | null;
  rationale?: string | null;
}

export interface ReferenceAnalysis {
  composition?: string | null;
  layout?: string | null;
  hierarchy?: string | null;
  typography?: string | null;
  color_relationships?: string | null;
  spacing?: string | null;
  container_treatment?: string | null;
  navigation?: string | null;
  hero_structure?: string | null;
  decorative_language?: string | null;
  interaction_cues?: string | null;
  responsive_implications?: string | null;
  conflicts_with_approved_system: string[];
  adoptable_without_conflict: string[];
}

export interface ComponentNode {
  name: string;
  purpose?: string | null;
  children: ComponentNode[];
}

export interface ComponentTree {
  root: ComponentNode[];
}

export interface FrontendArchitecture {
  pages: string[];
  component_tree?: ComponentTree | null;
  reused_components: string[];
  new_components: string[];
  responsive_strategy?: string | null;
  file_plan: string[];
}

export interface ProjectCreatedResponse {
  /** "proj_" + 32 hex. */
  project_id: string;
  /** "sess_" + 32 hex. */
  session_id: string;
  stage: WorkflowStage;
  status: RunStatus;
  created_at: IsoDateTime;
}

export interface ProjectSummary {
  project_id: string;
  title: string;
  stage: WorkflowStage;
  status: RunStatus;
  updated_at: IsoDateTime;
}

export interface ProjectSummaryList {
  projects: ProjectSummary[];
}

/** The cheap polling endpoint, and the SSE fallback. */
export interface StatusResponse {
  project_id: string;
  stage: WorkflowStage;
  status: RunStatus;
  iteration_count: number;
  /** From backend configuration (`MAX_AUTO_ITERATIONS`), not a frontend literal. */
  max_iterations: number;
  awaiting_checkpoint_id?: string | null;
  preview_available: boolean;
  last_error?: ErrorBody | null;
  updated_at: IsoDateTime;
}

/**
 * The public projection of `ProjectState`.
 *
 * Deliberately omits the raw prompt, internal agent working data, file hashes
 * and filesystem paths. Anything not listed here is not available to the
 * frontend by design — not an oversight to route around.
 */
export interface ProjectStateResponse {
  project_id: string;
  session_id: string;
  title: string;
  stage: WorkflowStage;
  status: RunStatus;
  iteration_count: number;
  normalized_requirements?: NormalizedRequirements | null;
  plan?: Plan | null;
  design_spec?: DesignSpec | null;
  reference_analysis?: ReferenceAnalysis | null;
  frontend_architecture?: FrontendArchitecture | null;
  component_tree?: ComponentTree | null;
  generated_files: FileRef[];
  evaluation_results?: EvaluationReport | null;
  approved_decisions: ApprovedDecision[];
  hitl_history: HitlRecord[];
  mcp_action_history: McpActionRecord[];
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface HitlHistoryResponse {
  history: HitlRecord[];
}

export interface HitlDecisionAccepted {
  accepted: boolean;
  stage: WorkflowStage;
  status: RunStatus;
  /** True when the workflow resumed from the gate. */
  resumed: boolean;
}

export interface ReferenceImageResponse {
  /** Server-generated. The uploaded filename is never reused or echoed back. */
  image_id: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_at: IsoDateTime;
}

/**
 * Where the built output is served, and whether it reflects the current files.
 *
 * `preview_url` is backend-relative (`/preview/{project_id}/`), so it must be
 * resolved against `API_BASE_URL` before it goes in an iframe — the frontend
 * dev server is a different origin and would 404 on it. Use `previewSrc`.
 */
export interface PreviewResponse {
  preview_url: string;
  built_at: IsoDateTime;
  status: PreviewFreshness;
}

/* ------------------------------------------------------------------ *
 * Request models
 * ------------------------------------------------------------------ */

/**
 * The backend models set `extra="forbid"`, so an unrecognised key is a 422 and
 * not a silently ignored field. These interfaces therefore list exactly the
 * accepted keys and nothing more.
 */
export interface CreateProjectRequest {
  /** 1..8000 characters. */
  prompt: string;
  /** 1..120 characters. Derived from the prompt when omitted. */
  title?: string;
  /** e.g. "landing_page", "pricing_page". */
  surface_hint?: string;
}

/**
 * A decision at a gate.
 *
 * Modelled as a discriminated union because the backend validator rejects any
 * other combination: `select_option` requires `option_id` and forbids
 * `instruction`; `custom` requires `instruction` and forbids `option_id`;
 * `approve` and `reject` take neither. Making that a type error here means a
 * malformed decision cannot be constructed in the first place.
 */
export type HitlDecisionRequest =
  | { checkpoint_id: string; action: "approve" | "reject" }
  | { checkpoint_id: string; action: "select_option"; option_id: string }
  | { checkpoint_id: string; action: "custom"; instruction: string };

/* ------------------------------------------------------------------ *
 * Errors
 * ------------------------------------------------------------------ */

/** Stable backend error codes (`backend/schemas/api.py: ErrorCode`). */
export const API_ERROR_CODES = [
  "validation_error",
  "unauthenticated",
  "forbidden",
  "project_not_found",
  "invalid_stage_for_action",
  "no_pending_checkpoint",
  "checkpoint_already_resolved",
  "iteration_limit_reached",
  "upload_too_large",
  "unsupported_media_type",
  "preview_not_available",
  "quality_gate_blocked",
  "build_failed",
  "approval_required",
  "approval_expired",
  "capability_unavailable",
  "upstream_error",
  "rate_limited",
  "internal_error",
] as const;
export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

/**
 * Codes this client mints when the request never produced a backend envelope.
 *
 * Kept separate and prefixed so they can never be mistaken for a backend code:
 * the backend has no opinion about an unreachable server, and reusing something
 * like `"internal_error"` here would misattribute the failure.
 */
export const CLIENT_ERROR_CODES = {
  /** The request did not complete — server down, DNS, CORS, offline. */
  NETWORK_UNREACHABLE: "client_network_unreachable",
  /** A response arrived but was not the JSON this contract specifies. */
  MALFORMED_RESPONSE: "client_malformed_response",
} as const;

/**
 * A failed API call, carrying the backend's own code and message.
 *
 * The message is already safe to display: the backend guarantees no secret,
 * path or stack trace reaches it (`docs/security.md` §6). `requestId`
 * correlates with the server log and comes from the body, because CORS does
 * not expose the `X-Request-ID` header to scripts.
 */
export class ApiError extends Error {
  readonly code: string;
  /** HTTP status, or 0 when the request never reached the server. */
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;
  /** True when this client produced the error rather than the backend. */
  readonly clientSide: boolean;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    requestId?: string | null;
    details?: Record<string, unknown> | null;
    clientSide?: boolean;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId ?? null;
    this.details = init.details ?? null;
    this.clientSide = init.clientSide ?? false;
  }
}

/** Narrow an unknown caught value, optionally to one specific code. */
export function isApiError(value: unknown, code?: string): value is ApiError {
  return value instanceof ApiError && (code === undefined || value.code === code);
}

/* ------------------------------------------------------------------ *
 * Transport
 * ------------------------------------------------------------------ */

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  /** Serialized as JSON. Omit for GET/DELETE and for multipart. */
  json?: unknown;
  /** Sent as-is; the browser sets the multipart boundary. */
  body?: FormData;
  signal?: AbortSignal;
}

function url(path: string): string {
  return `${API_BASE_URL}${API_PREFIX}${path}`;
}

/**
 * Turn a non-2xx response into a typed `ApiError`.
 *
 * Reads the documented envelope and falls back to the status alone when the
 * body is not one — a proxy or a crash can produce HTML, and guessing a code
 * from it would be worse than admitting the shape was unexpected.
 */
async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  const envelope = body as Partial<ErrorResponse> | null;
  const error = envelope?.error;

  if (error && typeof error.code === "string" && typeof error.message === "string") {
    return new ApiError({
      code: error.code,
      message: error.message,
      status: response.status,
      requestId: error.request_id ?? null,
      details: (error.details as Record<string, unknown> | null) ?? null,
    });
  }

  return new ApiError({
    code: CLIENT_ERROR_CODES.MALFORMED_RESPONSE,
    message: `The backend returned ${response.status} without the expected error envelope.`,
    status: response.status,
    clientSide: true,
  });
}

/**
 * One request. Every call below goes through here, so error decoding, JSON
 * headers and abort handling exist in exactly one place.
 *
 * `credentials` is left at the default (omitted cross-origin): the backend sets
 * `allow_credentials=False` and there is no authentication in v1, so sending
 * any would be rejected by CORS and would imply a session that does not exist.
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", json, body, signal } = options;

  /* The access token, fetched per request rather than captured once.
     `getAccessToken` reads the live session and refreshes it if it has
     expired, so a tab left open overnight sends a valid token instead of a
     stale one the backend would reject.

     Only the token is sent. No user id, no email, no role: the backend
     derives identity from the signature and ignores anything a client claims
     about itself (`docs/api.md` §1.1). */
  const headers: Record<string, string> = {};
  if (json !== undefined) headers["Content-Type"] = "application/json";
  const token = await getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(url(path), {
      method,
      signal,
      headers,
      body: json === undefined ? body : JSON.stringify(json),
    });
  } catch (cause) {
    // An abort is the caller's own doing and must stay distinguishable from a
    // dead server, so it is rethrown untouched.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError({
      code: CLIENT_ERROR_CODES.NETWORK_UNREACHABLE,
      message: `Could not reach the builder backend at ${API_BASE_URL}.`,
      status: 0,
      clientSide: true,
    });
  }

  if (!response.ok) {
    /* 401 means the session is gone or was never valid. Surfaced as an
       ordinary ApiError so callers keep one error path; the route guard is
       what sends the user to /login, because only it knows where they were. */
    throw await toApiError(response);
  }

  // 204 carries no body: DELETE, and the "no gate pending" answer.
  if (response.status === 204) return undefined as T;

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError({
      code: CLIENT_ERROR_CODES.MALFORMED_RESPONSE,
      message: "The backend returned a response that was not valid JSON.",
      status: response.status,
      clientSide: true,
    });
  }
}

/* ------------------------------------------------------------------ *
 * Routes — one function per implemented backend route, and no others.
 * ------------------------------------------------------------------ */

/** `GET /api/v1/health`. Liveness plus a boolean-only credential check. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal });
}

/**
 * `POST /api/v1/projects` — create a project and start Requirement Analysis.
 *
 * Returns as soon as the project is persisted (201). The workflow then runs in
 * a background task, so the returned `status` is the state at creation, not the
 * outcome; follow it with `watchProjectStatus`.
 */
export function createProject(
  payload: CreateProjectRequest,
  signal?: AbortSignal,
): Promise<ProjectCreatedResponse> {
  return request<ProjectCreatedResponse>("/projects", {
    method: "POST",
    json: payload,
    signal,
  });
}

/** `GET /api/v1/projects` — stored projects, newest first. */
export function listProjects(signal?: AbortSignal): Promise<ProjectSummaryList> {
  return request<ProjectSummaryList>("/projects", { signal });
}

/** `GET /api/v1/projects/{id}` — the full public state projection. */
export function getProject(
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectStateResponse> {
  return request<ProjectStateResponse>(`/projects/${encodeURIComponent(projectId)}`, {
    signal,
  });
}

/** `GET /api/v1/projects/{id}/status` — the cheap polling endpoint. */
export function getProjectStatus(
  projectId: string,
  signal?: AbortSignal,
): Promise<StatusResponse> {
  return request<StatusResponse>(
    `/projects/${encodeURIComponent(projectId)}/status`,
    { signal },
  );
}

/**
 * `GET /api/v1/projects/{id}/preview`.
 *
 * Throws `ApiError` with code `preview_not_available` (409) when no successful
 * build exists yet. That is a normal state, not a failure: the project exists
 * and the caller should keep waiting rather than treat it as broken.
 */
export function getPreview(
  projectId: string,
  signal?: AbortSignal,
): Promise<PreviewResponse> {
  return request<PreviewResponse>(
    `/projects/${encodeURIComponent(projectId)}/preview`,
    { signal },
  );
}

/**
 * `GET /api/v1/projects/{id}/hitl` — the gate awaiting a decision.
 *
 * Resolves to `null` on 204, which is the backend's way of saying "nothing
 * pending". That is a normal answer for a polling client, not an error.
 */
export async function getPendingCheckpoint(
  projectId: string,
  signal?: AbortSignal,
): Promise<HitlCheckpoint | null> {
  const checkpoint = await request<HitlCheckpoint | undefined>(
    `/projects/${encodeURIComponent(projectId)}/hitl`,
    { signal },
  );
  return checkpoint ?? null;
}

/**
 * `POST /api/v1/projects/{id}/hitl` — answer the gate and resume the workflow.
 *
 * Two failures are distinct and must stay so in the UI:
 *   409 `no_pending_checkpoint`       — nothing is open; the client is stale.
 *   410 `checkpoint_already_resolved` — this gate was already answered. A
 *                                       duplicate submit, not a validation
 *                                       error; re-fetch rather than retry.
 */
export function submitHitlDecision(
  projectId: string,
  decision: HitlDecisionRequest,
  signal?: AbortSignal,
): Promise<HitlDecisionAccepted> {
  return request<HitlDecisionAccepted>(
    `/projects/${encodeURIComponent(projectId)}/hitl`,
    { method: "POST", json: decision, signal },
  );
}

/** `GET /api/v1/projects/{id}/hitl/history` — the audit trail, oldest first. */
export function getHitlHistory(
  projectId: string,
  signal?: AbortSignal,
): Promise<HitlHistoryResponse> {
  return request<HitlHistoryResponse>(
    `/projects/${encodeURIComponent(projectId)}/hitl/history`,
    { signal },
  );
}

/**
 * `POST /api/v1/projects/{id}/reference-images` — attach a reference image.
 *
 * The filename is not used by the backend as a path component and is never
 * echoed back; the response carries a server-generated `image_id` instead
 * (`docs/security.md` §4). Rejections worth distinguishing: 413
 * `upload_too_large`, and 415 `unsupported_media_type` — the declared type is
 * confirmed against the file's magic bytes, so a renamed file still fails.
 */
export function uploadReferenceImage(
  projectId: string,
  file: File,
  signal?: AbortSignal,
): Promise<ReferenceImageResponse> {
  const form = new FormData();
  form.append("file", file);
  return request<ReferenceImageResponse>(
    `/projects/${encodeURIComponent(projectId)}/reference-images`,
    { method: "POST", body: form, signal },
  );
}

/**
 * `DELETE /api/v1/projects/{id}`.
 *
 * Refused with 409 `invalid_stage_for_action` while a run is in flight, so a
 * delete cannot race the graph.
 */
export function deleteProject(projectId: string, signal?: AbortSignal): Promise<void> {
  return request<void>(`/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
    signal,
  });
}

/**
 * Absolute URL for the built preview, for an iframe `src`.
 *
 * `PreviewResponse.preview_url` is relative to the BACKEND origin, which in dev
 * is not the origin serving this app. Resolving it here keeps that one piece of
 * knowledge in the transport layer instead of in every component that embeds a
 * preview. The route lives outside `/api/v1` on purpose — it serves files, not
 * typed JSON (`docs/architecture.md` §5.4).
 */
export function previewSrc(preview: PreviewResponse | string): string {
  const path = typeof preview === "string" ? preview : preview.preview_url;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

/* ------------------------------------------------------------------ *
 * Status polling — the documented SSE fallback (`docs/api.md` §2.8).
 * ------------------------------------------------------------------ */

export interface WatchOptions {
  /** Poll interval in ms. Default 1500. */
  intervalMs?: number;
  /** Called on every successful poll, including unchanged ones. */
  onStatus: (status: StatusResponse) => void;
  /**
   * Called when a poll fails. Polling CONTINUES afterwards, because a single
   * failed request is not proof the run ended — call the returned stop function
   * to actually stop.
   */
  onError?: (error: ApiError) => void;
}

/**
 * Poll `GET /projects/{id}/status` until stopped.
 *
 * This is NOT an event stream and does not pretend to be one. It emits the
 * status document the backend actually returns, with no synthesized event
 * types, no `stage_changed` / `agent_started` / `build_progress` payloads, and
 * no ordering guarantee beyond "most recent wins". When the real SSE endpoint
 * is implemented, this is the one function that gets replaced.
 *
 * Self-correcting cadence: the next poll is scheduled only after the previous
 * one settles, so a slow backend cannot accumulate overlapping requests.
 *
 * @returns a function that stops polling and aborts any request in flight.
 */
export function watchProjectStatus(
  projectId: string,
  options: WatchOptions,
): () => void {
  const { intervalMs = 1500, onStatus, onError } = options;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;

  function scheduleNext(): void {
    if (stopped) return;
    timer = setTimeout(() => void poll(), intervalMs);
  }

  async function poll(): Promise<void> {
    if (stopped) return;
    try {
      const status = await getProjectStatus(projectId, controller.signal);
      if (stopped) return;
      onStatus(status);
    } catch (error) {
      if (stopped) return;
      // An abort means we were stopped mid-flight; that is not a failure.
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (isApiError(error)) onError?.(error);
    }
    scheduleNext();
  }

  void poll();

  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
    controller.abort();
  };
}

/**
 * True when the run has settled and polling can stop.
 *
 * `awaiting_hitl` is deliberately NOT terminal: the run is paused on the user,
 * and the client must keep watching so it sees the resume.
 */
export function isTerminalStatus(status: RunStatus): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "blocked"
  );
}
