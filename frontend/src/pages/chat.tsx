/**
 * Chatbot Page — the DETROIT build workspace (`/chat`).
 *
 * Why this file exists:
 * `docs/frontend.md` §4.4 designates `src/pages/chat.tsx` as the owner of the
 * Chatbot surface. This is the only surface that talks to the builder backend
 * (`docs/frontend.md` §4.6); Landing, Pricing and Product stay static.
 *
 * Layer: frontend presentation (route page component).
 *
 * ---------------------------------------------------------------------------
 * THIS FILE IS NOW CONNECTED TO THE REAL BACKEND
 *
 * It was previously a shell rendering illustrative placeholder state. Every
 * network call now goes through `@/lib/api`, which mirrors the routes that
 * `backend/api/projects.py` actually implements — not the larger set
 * `docs/api.md` specifies. The composition, spacing, typography and
 * interaction model are unchanged; only the data source moved.
 *
 * WHAT IS REAL NOW
 *   project list        GET  /projects
 *   project creation    POST /projects          (the composer)
 *   stage + status      GET  /projects/{id}/status, polled
 *   iteration budget    status.iteration_count / status.max_iterations
 *   stage outputs       GET  /projects/{id}     (normalized_requirements, plan)
 *   quality gate        project.evaluation_results
 *   HITL gate           GET  /projects/{id}/hitl  -> the checkpoint card
 *   HITL decision       POST /projects/{id}/hitl
 *   reference image     POST /projects/{id}/reference-images
 *   errors              the ErrorResponse envelope, via ApiError
 *
 * WHAT IS STILL NOT REAL, AND IS NOT FAKED HERE
 *
 *   No SSE. `docs/api.md` §2.8 specifies `GET /projects/{id}/events`, and the
 *   backend implements no such route — there is no StreamingResponse anywhere
 *   in it. This page therefore polls `GET .../status`, which §2.8 itself names
 *   as the supported fallback. Nothing here synthesizes `stage_changed`,
 *   `agent_started` or `build_progress` events that the backend never emits.
 *
 *   No follow-up messages. `POST /projects/{id}/messages` is unimplemented —
 *   `backend/api/projects.py` says why: `ProjectState` has nowhere to put a
 *   message. The composer therefore starts a project and is then disabled for
 *   the life of that run, with the hint line saying so. Refinement during a run
 *   goes through the HITL custom instruction, which is a real endpoint.
 *
 *   No conversation history. The backend stores no transcript, and
 *   `ProjectStateResponse` deliberately redacts `user_request`. The prompt
 *   bubble is a LOCAL echo of what the user typed in this browser session; it
 *   is not fetched and does not survive a reload or a project switch. Assistant
 *   bubbles are rendered strictly from fields the backend returns, never
 *   composed here.
 *
 *   No MCP status. No route exposes connected servers, so that panel reports
 *   that rather than claiming a connection.
 *
 *   No account identity. `docs/api.md` §1.1: there is no authentication in v1.
 *
 * Reference handling (`docs/design.md` §3):
 * The structure comes from `Refrence_images/chatbot_refrence.png` — three
 * columns, a grouped thread rail, greeting plus a suggestion row, a bordered
 * composer with a monospace hint, and a right-hand telemetry rail. Its palette
 * does NOT: the reference is indigo/violet on navy, and the approved
 * ember-on-warm-near-black system wins. Status tones reuse the emerald/amber
 * treatment `dashboard-section.tsx` already ships rather than introducing a new
 * color sub-system.
 * ---------------------------------------------------------------------------
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ArrowUp,
  Check,
  ChevronRight,
  Github,
  Layers,
  LayoutDashboard,
  type LucideIcon,
  MessageSquare,
  PanelLeft,
  Paperclip,
  Plus,
  ScanEye,
  Settings,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";

/* The same spiral the Dashboard stands on. It is byte-identical to the image
   supplied for this page, so it is imported rather than duplicated — two
   copies of one photograph would ship twice and drift apart. The filename
   still says "dashboard" because renaming it would mean editing that page
   too; worth tidying in a later pass. */
import pageBg from "@/assets/dashboard-page-bg.jpg";
import {
  ApiError,
  createProject,
  getPendingCheckpoint,
  getProject,
  isApiError,
  isTerminalStatus,
  listProjects,
  submitHitlDecision,
  uploadReferenceImage,
  watchProjectStatus,
  WORKFLOW_STAGES,
  type ErrorBody,
  type HitlCheckpoint,
  type HitlDecisionRequest,
  type ProjectStateResponse,
  type ProjectSummary,
  type RunStatus,
  type StatusResponse,
  type WorkflowStage,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ *
 * Presentation tables for the backend vocabulary.
 *
 * The VALUES live in `@/lib/api`, transcribed from `backend/schemas/enums.py`.
 * Only the human-readable labels live here, because a label is a UI decision
 * and the enum is a contract. `Record<WorkflowStage, ...>` makes a new backend
 * stage a compile error rather than a silently unlabelled row.
 * ------------------------------------------------------------------ */

const STAGE_META: Record<
  WorkflowStage,
  { label: string; gate?: boolean; conditional?: boolean }
> = {
  requirement_analysis: { label: "Requirement analysis" },
  planning: { label: "Planning", gate: true },
  design: { label: "Design", gate: true },
  reference_analysis: {
    label: "Reference analysis",
    gate: true,
    conditional: true,
  },
  frontend_architecture: { label: "Frontend architecture" },
  build: { label: "Build" },
  preview: { label: "Preview" },
  evaluation: { label: "Evaluation" },
  targeted_fix: { label: "Targeted fix" },
  re_evaluation: { label: "Re-evaluation" },
  final_validation: { label: "Final validation", gate: true },
  external_action: { label: "External action", gate: true, conditional: true },
};

const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  idle: "Idle",
  running: "Running",
  awaiting_hitl: "Awaiting review",
  completed: "Completed",
  blocked: "Blocked",
  failed: "Failed",
  cancelled: "Cancelled",
};

/* Status tone reuses the treatment already shipping in `dashboard-section.tsx`
   rather than adding a color sub-system for the reference's green pill. */
type Tone = "ok" | "warn" | "err" | "idle";

const TONE_DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  err: "bg-rose-500",
  idle: "bg-muted-foreground/60",
};

const RUN_STATUS_TONE: Record<RunStatus, Tone> = {
  idle: "idle",
  running: "ok",
  awaiting_hitl: "warn",
  completed: "ok",
  blocked: "err",
  failed: "err",
  cancelled: "idle",
};

/** The upload allowlist the backend enforces (`ALLOWED_UPLOAD_TYPES`). */
const ACCEPTED_IMAGE_TYPES = "image/png,image/jpeg,image/webp";

/**
 * A thread entry.
 *
 * `user` is a local echo of what was typed in this session — the backend
 * redacts `user_request` from its public projection, so it cannot be fetched.
 * `assistant` carries text taken verbatim from a backend field; the only thing
 * composed here is the short label that says which field it is.
 */
type Message = { id: string; role: "user" | "assistant"; body: string };

/* Surfaces reachable from the workspace. Real destinations, not placeholders. */
const WORKSPACE_LINKS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/features", label: "Product", icon: Sparkles },
];

/**
 * MCP servers the project intends to integrate (`docs/mcp.md`).
 *
 * No backend route reports connection state — `tools/mcp_client.py` performs
 * discovery only and nothing exposes it over HTTP. So this lists the intended
 * integrations and says the status is unavailable, rather than showing a
 * "Connected" pill that nothing stands behind.
 */
const MCP_SERVERS: { name: string; icon: LucideIcon }[] = [
  { name: "GitHub", icon: Github },
  { name: "Claude", icon: Sparkles },
  { name: "Cursor", icon: Layers },
  { name: "Lovable", icon: ScanEye },
];

const PROMPT_STARTERS = [
  {
    icon: Layers,
    title: "Landing page",
    body: "A launch page with a hero, proof, and one clear call to action.",
  },
  {
    icon: MessageSquare,
    title: "Pricing page",
    body: "Tiered plans with a comparison table and an annual toggle.",
  },
  {
    icon: ScanEye,
    title: "Dashboard",
    body: "An analytics view with live tiles, charts, and a filter rail.",
  },
];

/* ------------------------------------------------------------------ *
 * Shell pieces
 * ------------------------------------------------------------------ */

function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
      {children}
    </p>
  );
}

function StatusPill({ status }: { status: RunStatus }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-border bg-secondary/60 px-3 py-1">
      <span
        aria-hidden="true"
        className={cn(
          "size-1.5 rounded-full",
          TONE_DOT[RUN_STATUS_TONE[status]],
        )}
      />
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-foreground/80">
        {RUN_STATUS_LABEL[status]}
      </span>
    </span>
  );
}

/** The pipeline readout — a build log, not a generic circle-and-line stepper. */
function StageRail({ current }: { current: WorkflowStage | null }) {
  const currentIndex = current ? WORKFLOW_STAGES.indexOf(current) : -1;

  return (
    <ol className="space-y-px">
      {WORKFLOW_STAGES.map((stageId, i) => {
        const meta = STAGE_META[stageId];
        const done = i < currentIndex;
        const active = i === currentIndex;

        return (
          <li
            key={stageId}
            aria-current={active ? "step" : undefined}
            className={cn(
              "flex items-baseline gap-3 border-l-2 py-1.5 pl-3 transition-colors",
              active
                ? "border-l-ember bg-ember/[0.07]"
                : done
                  ? "border-l-border"
                  : "border-l-transparent",
            )}
          >
            <span
              className={cn(
                "w-5 shrink-0 font-mono text-[10px] tabular-nums",
                active ? "text-ember" : "text-muted-foreground/60",
              )}
            >
              {String(i + 1).padStart(2, "0")}
            </span>

            <span
              className={cn(
                "flex-1 text-[13px] leading-snug",
                active
                  ? "font-medium text-foreground"
                  : done
                    ? "text-foreground/60"
                    : "text-muted-foreground/60",
              )}
            >
              {meta.label}
              {meta.conditional && (
                <span className="ml-1.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground/50">
                  opt
                </span>
              )}
            </span>

            {done ? (
              <Check
                size={12}
                aria-label="complete"
                className="shrink-0 text-emerald-500"
              />
            ) : meta.gate ? (
              <span
                title="Human review gate"
                className="shrink-0 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground/50"
              >
                gate
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The HITL checkpoint — the product's defining interaction.
 *
 * Every word rendered here comes from the `HitlCheckpoint` the backend
 * returned: its title, its summary, and its options. The option count is
 * whatever the agent produced and is never padded (`docs/workflow.md` §3);
 * with zero options the list simply does not render, and Approve / Custom /
 * Reject remain — `allows_custom` is always true.
 */
function CheckpointCard({
  checkpoint,
  onDecide,
}: {
  checkpoint: HitlCheckpoint;
  onDecide: (decision: HitlDecisionRequest) => Promise<boolean>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resolved, setResolved] = useState<string | null>(null);

  const stageLabel = STAGE_META[checkpoint.stage].label;

  /* A decision only counts as taken once the backend accepts it. A failed
     submit leaves the card open so the user can act on the error, rather than
     showing a resolution that did not happen. */
  async function decide(decision: HitlDecisionRequest, outcome: string) {
    setSubmitting(true);
    const ok = await onDecide(decision);
    setSubmitting(false);
    if (ok) setResolved(outcome);
  }

  if (resolved) {
    return (
      <div
        role="status"
        className="flex items-center gap-3 rounded-2xl border border-border bg-card/60 px-5 py-4"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
          <Check size={14} strokeWidth={3} />
        </span>
        <p className="text-sm text-foreground/80">
          {stageLabel} gate resolved —{" "}
          <span className="text-foreground">{resolved}</span>. The run continues.
        </p>
      </div>
    );
  }

  const trimmedCustom = custom.trim();
  const primaryLabel = trimmedCustom
    ? "Send instruction"
    : selected
      ? "Use this option"
      : "Approve";

  function submitPrimary() {
    if (trimmedCustom) {
      void decide(
        {
          checkpoint_id: checkpoint.checkpoint_id,
          action: "custom",
          instruction: trimmedCustom,
        },
        "custom instruction sent",
      );
      return;
    }
    if (selected) {
      const option = checkpoint.options.find((o) => o.option_id === selected);
      void decide(
        {
          checkpoint_id: checkpoint.checkpoint_id,
          action: "select_option",
          option_id: selected,
        },
        option ? option.label.toLowerCase() : "option selected",
      );
      return;
    }
    void decide(
      { checkpoint_id: checkpoint.checkpoint_id, action: "approve" },
      "approved as-is",
    );
  }

  return (
    <section
      aria-label={`${stageLabel} review checkpoint`}
      className="overflow-hidden rounded-2xl border border-border bg-card/60"
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3.5">
        <Eyebrow>Human review · {stageLabel.toLowerCase()}</Eyebrow>
        <StatusPill status="awaiting_hitl" />
      </header>

      <div className="px-5 py-5">
        <h3 className="font-display text-lg font-bold tracking-tight text-foreground">
          {checkpoint.title}
        </h3>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
          {checkpoint.summary}
        </p>

        {checkpoint.options.length > 0 && (
          <ul className="mt-5 space-y-2">
            {checkpoint.options.map((opt) => {
              const isOn = selected === opt.option_id;
              return (
                <li key={opt.option_id}>
                  <button
                    type="button"
                    onClick={() => setSelected(isOn ? null : opt.option_id)}
                    aria-pressed={isOn}
                    className={cn(
                      "w-full rounded-lg border px-4 py-3 text-left transition-colors",
                      isOn
                        ? "border-ember bg-ember/[0.06]"
                        : "border-border hover:border-foreground/25",
                    )}
                  >
                    <span className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-foreground">
                        {opt.label}
                      </span>
                      <span
                        aria-hidden="true"
                        className={cn(
                          "size-3.5 shrink-0 rounded-full border",
                          isOn ? "border-ember bg-ember" : "border-border",
                        )}
                      />
                    </span>
                    <span className="mt-1 block text-[13px] leading-relaxed text-muted-foreground">
                      {opt.description}
                    </span>
                    <span className="mt-1.5 block font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground/70">
                      {opt.impact}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="mt-5">
          <label
            htmlFor="hitl-custom"
            className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground"
          >
            Or write your own instruction
          </label>
          <textarea
            id="hitl-custom"
            rows={2}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="Keep the palette, but make the hero typography much larger."
            className="mt-2 w-full resize-none rounded-lg border border-border bg-background/60 px-3.5 py-2.5 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground/70 focus-visible:border-ember focus-visible:outline-none"
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-3">
          <Button
            type="button"
            size="sm"
            disabled={submitting}
            onClick={submitPrimary}
          >
            {submitting ? "Sending…" : primaryLabel}
          </Button>
          <button
            type="button"
            disabled={submitting}
            onClick={() =>
              void decide(
                { checkpoint_id: checkpoint.checkpoint_id, action: "reject" },
                "sent back for another attempt",
              )
            }
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      </div>
    </section>
  );
}

function ThreadMessage({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-xl rounded-2xl rounded-br-md border border-border bg-secondary/70 px-5 py-3.5 text-[15px] leading-relaxed text-foreground">
          {message.body}
        </p>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <span
        aria-hidden="true"
        className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-card font-mono text-[9px] font-bold text-ember"
      >
        D
      </span>
      <p className="min-w-0 max-w-2xl flex-1 text-[15px] leading-relaxed text-foreground/85">
        {message.body}
      </p>
    </div>
  );
}

/** A failure, in the backend's own words. Never a message composed here. */
function ErrorNotice({ error }: { error: ApiError | ErrorBody }) {
  const code = error.code;
  const message = error.message;
  const requestId = error instanceof ApiError ? error.requestId : error.request_id;

  return (
    <div
      role="alert"
      className="mx-auto mb-3 flex w-full max-w-3xl items-start gap-2.5 rounded-lg border border-rose-500/40 bg-rose-500/[0.07] px-4 py-3"
    >
      <TriangleAlert size={15} className="mt-0.5 shrink-0 text-rose-400" />
      <div className="min-w-0">
        <p className="text-[13px] leading-relaxed text-foreground/90">{message}</p>
        <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
          {code}
          {requestId ? ` · ${requestId}` : ""}
        </p>
      </div>
    </div>
  );
}

/**
 * A side rail. Static column at its breakpoint, slide-over drawer below it,
 * so the three-column desktop composition degrades to one column on a phone
 * without duplicating the markup.
 */
function Rail({
  side,
  open,
  onClose,
  label,
  children,
}: {
  side: "left" | "right";
  open: boolean;
  onClose: () => void;
  label: string;
  children: ReactNode;
}) {
  const isLeft = side === "left";

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label={`Close ${label}`}
          onClick={onClose}
          className={cn(
            "fixed inset-0 z-40 bg-background/70",
            isLeft ? "lg:hidden" : "xl:hidden",
          )}
        />
      )}

      <aside
        aria-label={label}
        className={cn(
          "fixed inset-y-0 z-50 flex shrink-0 flex-col border-border bg-background transition-transform duration-300 ease-out",
          isLeft
            ? "left-0 w-[280px] border-r lg:static lg:z-auto lg:translate-x-0"
            : "right-0 w-[300px] border-l xl:static xl:z-auto xl:translate-x-0",
          open ? "translate-x-0" : isLeft ? "-translate-x-full" : "translate-x-full",
        )}
      >
        {children}
      </aside>
    </>
  );
}

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export function ChatPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [project, setProject] = useState<ProjectStateResponse | null>(null);
  const [checkpoint, setCheckpoint] = useState<HitlCheckpoint | null>(null);
  /* The prompt as typed, held only for this session: the backend redacts
     `user_request`, so there is nothing to fetch it back from. */
  const [localPrompt, setLocalPrompt] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [creating, setCreating] = useState(false);

  const [draft, setDraft] = useState("");
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

  const fileInput = useRef<HTMLInputElement>(null);

  const refreshProjects = useCallback(async () => {
    try {
      const list = await listProjects();
      setProjects(list.projects);
    } catch (cause) {
      if (isApiError(cause)) setError(cause);
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  /* Poll `GET /status` — the fallback `docs/api.md` §2.8 names, because the
     SSE route it specifies does not exist in the backend. Polling stops once
     the run settles; `awaiting_hitl` is deliberately not settled, since the
     client has to see the resume. */
  const settled = status ? isTerminalStatus(status.status) : false;

  useEffect(() => {
    if (!activeId || settled) return;
    return watchProjectStatus(activeId, {
      onStatus: (next) => {
        setStatus(next);
        setError(null);
      },
      onError: setError,
    });
  }, [activeId, settled]);

  /* The full projection is only re-read when the run actually moved, so a
     1.5s poll does not pull the whole state document every tick. */
  const movedAt = status?.updated_at;

  useEffect(() => {
    if (!activeId || !movedAt) return;
    let alive = true;
    getProject(activeId)
      .then((next) => {
        if (alive) setProject(next);
      })
      .catch((cause) => {
        if (alive && isApiError(cause)) setError(cause);
      });
    return () => {
      alive = false;
    };
  }, [activeId, movedAt]);

  /* The gate is fetched when status says one is open, and cleared when it is
     not. `GET /hitl` answers 204 for "nothing pending", which the client
     surfaces as null — a normal answer, not an error. */
  const pendingCheckpointId = status?.awaiting_checkpoint_id ?? null;

  useEffect(() => {
    if (!activeId || !pendingCheckpointId) {
      setCheckpoint(null);
      return;
    }
    let alive = true;
    getPendingCheckpoint(activeId)
      .then((next) => {
        if (alive) setCheckpoint(next);
      })
      .catch((cause) => {
        if (alive && isApiError(cause)) setError(cause);
      });
    return () => {
      alive = false;
    };
  }, [activeId, pendingCheckpointId]);

  /* The left rail is re-read when a run settles, so its stage and status
     columns do not go stale after a build finishes. */
  useEffect(() => {
    if (settled) void refreshProjects();
  }, [settled, refreshProjects]);

  function openProject(projectId: string) {
    setActiveId(projectId);
    setStatus(null);
    setProject(null);
    setCheckpoint(null);
    setLocalPrompt(null);
    setError(null);
    setLeftOpen(false);
  }

  function newProject() {
    setActiveId(null);
    setStatus(null);
    setProject(null);
    setCheckpoint(null);
    setLocalPrompt(null);
    setError(null);
    setDraft("");
    setLeftOpen(false);
  }

  /**
   * `POST /projects` — the only write the composer can make.
   *
   * There is no follow-up message endpoint, so this runs once per project and
   * the composer is then disabled for the life of the run.
   */
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = draft.trim();
    if (!prompt || activeId || creating) return;

    setCreating(true);
    setError(null);
    try {
      const created = await createProject({ prompt });
      setLocalPrompt(prompt);
      setDraft("");
      setActiveId(created.project_id);
      void refreshProjects();
    } catch (cause) {
      if (isApiError(cause)) setError(cause);
    } finally {
      setCreating(false);
    }
  }

  /** `POST /projects/{id}/hitl`. Returns whether the backend accepted it. */
  async function decide(decision: HitlDecisionRequest): Promise<boolean> {
    if (!activeId) return false;
    setError(null);
    try {
      await submitHitlDecision(activeId, decision);
      /* The gate is gone server-side; clear it locally rather than waiting a
         poll interval to stop showing a card that can no longer be answered. */
      setCheckpoint(null);
      return true;
    } catch (cause) {
      if (isApiError(cause)) setError(cause);
      return false;
    }
  }

  /** `POST /projects/{id}/reference-images`. Needs a project to attach to. */
  async function attachReference(file: File) {
    if (!activeId) return;
    setError(null);
    try {
      await uploadReferenceImage(activeId, file);
    } catch (cause) {
      if (isApiError(cause)) setError(cause);
    }
  }

  const empty = activeId === null;
  const stage = status?.stage ?? null;
  const runStatus: RunStatus = status?.status ?? "idle";
  const stageLabel = stage ? STAGE_META[stage].label : null;
  const title =
    project?.title ??
    projects.find((p) => p.project_id === activeId)?.title ??
    (empty ? "New project" : "Loading…");

  /**
   * The thread, assembled from what actually exists.
   *
   * Nothing here is written by this component: the user bubble is the typed
   * prompt, and each assistant bubble is a backend field with a label naming
   * which field it is. A stage that has not produced output contributes no
   * bubble, which is why the thread can legitimately be short.
   */
  const messages = useMemo<Message[]>(() => {
    const thread: Message[] = [];
    if (localPrompt) thread.push({ id: "prompt", role: "user", body: localPrompt });

    const requirements = project?.normalized_requirements;
    if (requirements) {
      thread.push({
        id: "requirements",
        role: "assistant",
        body: `Requirements normalized — ${requirements.goal}`,
      });
    }

    const plan = project?.plan;
    if (plan) {
      const detail = [
        plan.pages.length ? `${plan.pages.length} page(s)` : null,
        plan.sections.length ? `${plan.sections.length} section(s)` : null,
      ]
        .filter(Boolean)
        .join(", ");
      thread.push({
        id: "plan",
        role: "assistant",
        body: detail
          ? `Plan — ${plan.objective} (${detail})`
          : `Plan — ${plan.objective}`,
      });
    }

    return thread;
  }, [localPrompt, project]);

  const evaluation = project?.evaluation_results ?? null;
  const maxIterations = status?.max_iterations ?? null;
  const usedIterations = status?.iteration_count ?? 0;

  return (
    <div className="relative isolate flex h-screen overflow-hidden bg-background text-foreground">
      {/* Page ground. Mounted on the root rather than on the conversation
          column so it does not scroll with the thread and stays fixed behind
          every panel, exactly as on the Dashboard.

          Both rails are opaque `bg-background`, so the spiral is confined to
          the centre column — which is the intent: it gives the conversation a
          ground without putting texture behind the project list or the
          telemetry readouts.

          The scrim is heavier than the Dashboard's. There the photograph sits
          behind translucent cards; here 15px body copy sits directly on it,
          and unscrimmed starfield behind running text is the fastest way to
          make a reading surface tiring. ~12% transmission keeps the spiral
          legible as texture while the thread stays the brightest thing on
          screen. */}
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10 bg-cover bg-center"
        style={{ backgroundImage: `url(${pageBg})` }}
      />
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10"
        style={{
          background:
            "linear-gradient(180deg, rgba(17,15,14,0.92) 0%, rgba(17,15,14,0.86) 35%, rgba(17,15,14,0.88) 70%, rgba(17,15,14,0.96) 100%)",
        }}
      />
      {/* ---------- Left rail: projects ---------- */}
      <Rail
        side="left"
        open={leftOpen}
        onClose={() => setLeftOpen(false)}
        label="Projects"
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-border px-5">
          <a href="/" className="font-display text-base font-bold tracking-tight">
            DETROIT<span className="text-ember">-AI</span>
          </a>
          <button
            type="button"
            onClick={() => setLeftOpen(false)}
            aria-label="Close projects"
            className="text-muted-foreground lg:hidden"
          >
            <X size={18} />
          </button>
        </div>

        <div className="shrink-0 px-4 py-4">
          <Button type="button" size="sm" className="w-full" onClick={newProject}>
            <Plus size={15} />
            New project
          </Button>
        </div>

        {/* Workspace destinations, kept separate from the project list below:
            without this the builder is a dead end — the only way out was the
            brand mark back to the Landing Page. */}
        <nav aria-label="Workspace" className="shrink-0 px-3 pb-3">
          <ul className="space-y-0.5">
            {WORKSPACE_LINKS.map((l) => (
              <li key={l.href}>
                <a
                  href={l.href}
                  className="flex items-center gap-2.5 border-l-2 border-l-transparent px-3 py-2 text-[13px] text-muted-foreground transition-colors hover:bg-secondary/40 hover:text-foreground"
                >
                  <l.icon size={14} className="shrink-0" />
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <nav
          aria-label="Your projects"
          className="min-h-0 flex-1 overflow-y-auto px-3 pb-4"
        >
          <div className="px-2 pb-2">
            <Eyebrow>Projects</Eyebrow>
          </div>
          {projects.length === 0 ? (
            <p className="px-3 py-2 text-[13px] leading-relaxed text-muted-foreground">
              No projects yet.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {projects.map((p) => {
                const active = p.project_id === activeId;
                return (
                  <li key={p.project_id}>
                    <button
                      type="button"
                      onClick={() => openProject(p.project_id)}
                      aria-current={active ? "true" : undefined}
                      className={cn(
                        "flex w-full items-center gap-2.5 border-l-2 px-3 py-2.5 text-left transition-colors",
                        active
                          ? "border-l-ember bg-secondary/70"
                          : "border-l-transparent hover:bg-secondary/40",
                      )}
                    >
                      <MessageSquare
                        size={14}
                        className={cn(
                          "shrink-0",
                          active ? "text-ember" : "text-muted-foreground",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-foreground">
                          {p.title}
                        </span>
                        <span className="block font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
                          {STAGE_META[p.stage].label}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        {/* No authentication exists in v1 (`docs/api.md` §1.1), so this names
            the workspace rather than a signed-in person. */}
        <div className="flex shrink-0 items-center gap-3 border-t border-border px-5 py-4">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-card font-mono text-[10px] text-foreground/80">
            D
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] text-foreground">
              Local workspace
            </span>
            <span className="block font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
              No sign-in required
            </span>
          </span>
          <button
            type="button"
            aria-label="Settings"
            className="text-muted-foreground transition-colors hover:text-foreground"
          >
            <Settings size={16} />
          </button>
        </div>
      </Rail>

      {/* ---------- Center: the conversation ---------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border px-4 sm:px-6">
          <button
            type="button"
            onClick={() => setLeftOpen(true)}
            aria-label="Open projects"
            className="shrink-0 text-muted-foreground transition-colors hover:text-foreground lg:hidden"
          >
            <PanelLeft size={18} />
          </button>

          <div className="min-w-0 flex-1">
            <h1 className="truncate font-display text-sm font-bold tracking-tight">
              {title}
            </h1>
            <p className="truncate font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              {stageLabel ? `Stage · ${stageLabel}` : "No run started"}
            </p>
          </div>

          <StatusPill status={runStatus} />

          <button
            type="button"
            onClick={() => setRightOpen(true)}
            aria-label="Open run details"
            className="shrink-0 text-muted-foreground transition-colors hover:text-foreground xl:hidden"
          >
            <ChevronRight size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-10">
            {empty ? (
              <div className="pt-6 sm:pt-14">
                <h2 className="font-display text-3xl font-bold leading-[1.1] tracking-tight sm:text-4xl">
                  What are we building?
                </h2>
                <p className="mt-4 max-w-md text-base leading-relaxed text-muted-foreground">
                  Describe the page you need. DETROIT plans it, designs it, builds
                  it and reviews it — stopping for your approval at every decision
                  that matters.
                </p>

                <ul className="mt-8 grid gap-3 sm:grid-cols-3">
                  {PROMPT_STARTERS.map((s) => (
                    <li key={s.title}>
                      <button
                        type="button"
                        onClick={() => setDraft(s.body)}
                        className="group h-full w-full rounded-lg border border-border bg-card/40 p-4 text-left transition-colors hover:border-foreground/25 hover:bg-card/70"
                      >
                        <s.icon
                          size={16}
                          className="text-muted-foreground transition-colors group-hover:text-ember"
                        />
                        <span className="mt-3 block text-sm font-medium text-foreground">
                          {s.title}
                        </span>
                        <span className="mt-1 block text-[13px] leading-relaxed text-muted-foreground">
                          {s.body}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="space-y-7">
                {messages.map((m) => (
                  <ThreadMessage key={m.id} message={m} />
                ))}
                {checkpoint && (
                  <CheckpointCard
                    key={checkpoint.checkpoint_id}
                    checkpoint={checkpoint}
                    onDecide={decide}
                  />
                )}
                {status?.last_error && <ErrorNotice error={status.last_error} />}
              </div>
            )}
          </div>
        </div>

        {/* Composer */}
        <div className="shrink-0 border-t border-border px-4 pb-5 pt-4 sm:px-6">
          {error && <ErrorNotice error={error} />}
          <form onSubmit={send} className="mx-auto w-full max-w-3xl">
            <div className="rounded-2xl border border-border bg-card/60 transition-colors focus-within:border-foreground/30">
              <label htmlFor="composer" className="sr-only">
                Describe what you want to build
              </label>
              <textarea
                id="composer"
                rows={2}
                value={draft}
                disabled={!empty}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    e.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder={
                  empty
                    ? "Describe the page you want DETROIT to build…"
                    : "This run has started. Use the review gate to steer it."
                }
                className="w-full resize-none bg-transparent px-5 pt-4 text-[15px] leading-relaxed text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-none disabled:cursor-not-allowed"
              />
              <div className="flex items-center justify-between gap-3 px-3 pb-3">
                <input
                  ref={fileInput}
                  type="file"
                  accept={ACCEPTED_IMAGE_TYPES}
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void attachReference(file);
                  }}
                />
                <button
                  type="button"
                  disabled={empty}
                  onClick={() => fileInput.current?.click()}
                  aria-label="Attach a reference image"
                  title={
                    empty
                      ? "Start a project first — an image attaches to a project"
                      : "Attach a reference image"
                  }
                  className="flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
                >
                  <Paperclip size={16} />
                </button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={!empty || !draft.trim() || creating}
                >
                  {creating ? "Starting…" : "Send"}
                  <ArrowUp size={15} />
                </Button>
              </div>
            </div>
            <p className="mt-2.5 px-1 font-mono text-[10px] uppercase leading-relaxed tracking-[0.14em] text-muted-foreground/70">
              {empty
                ? "Enter to send · Shift + Enter for a new line · Attach a reference image to guide the design"
                : "Follow-up messages need an endpoint the backend does not expose · Steer this run from the review gate · New project to start another"}
            </p>
          </form>
        </div>
      </div>

      {/* ---------- Right rail: run telemetry ---------- */}
      <Rail
        side="right"
        open={rightOpen}
        onClose={() => setRightOpen(false)}
        label="Run details"
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-border px-5">
          <Eyebrow>Run</Eyebrow>
          <button
            type="button"
            onClick={() => setRightOpen(false)}
            aria-label="Close run details"
            className="text-muted-foreground xl:hidden"
          >
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-7 overflow-y-auto px-5 py-5">
          <section>
            <Eyebrow>Pipeline</Eyebrow>
            <div className="mt-3">
              <StageRail current={stage} />
            </div>
          </section>

          {/* The limit is configuration (`MAX_AUTO_ITERATIONS`), reported by
              the status response. Before a run there is no value to report, so
              none is shown rather than a frontend guess at the default. */}
          <section>
            <Eyebrow>Correction budget</Eyebrow>
            <p className="mt-3 font-display text-2xl font-bold leading-none tracking-tight tabular-nums">
              {maxIterations === null ? "—" : usedIterations}
              <span className="ml-1.5 font-sans text-xs font-normal tracking-normal text-muted-foreground">
                {maxIterations === null
                  ? "no run yet"
                  : `of ${maxIterations} used`}
              </span>
            </p>
            {maxIterations !== null && (
              <div
                role="img"
                aria-label={`${usedIterations} of ${maxIterations} automatic correction iterations used.`}
                className="mt-3 flex gap-1"
              >
                {Array.from({ length: maxIterations }).map((_, i) => (
                  <span
                    key={i}
                    className={cn(
                      "h-1 flex-1 rounded-full",
                      i < usedIterations ? "bg-ember" : "bg-foreground/[0.08]",
                    )}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Impeccable is mandatory and has no substitute. A report whose
              detector did not run is BLOCKED, and the backend enforces that
              structurally — it is never rendered here as a pass (CLAUDE.md §15). */}
          <section>
            <Eyebrow>Quality gate</Eyebrow>
            <div className="mt-3 flex items-start gap-2.5 rounded-lg border border-border px-3.5 py-3">
              <ShieldCheck
                size={15}
                className={cn(
                  "mt-0.5 shrink-0",
                  evaluation?.gate_status === "passed"
                    ? "text-emerald-500"
                    : evaluation?.gate_status === "blocked"
                      ? "text-rose-400"
                      : evaluation?.gate_status === "failed"
                        ? "text-amber-500"
                        : "text-muted-foreground",
                )}
              />
              <div className="min-w-0">
                <p className="text-[13px] text-foreground">
                  Impeccable
                  {evaluation?.impeccable_version
                    ? ` ${evaluation.impeccable_version}`
                    : ""}
                </p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
                  {evaluation === null
                    ? "Mandatory. Runs at evaluation — not yet reached."
                    : evaluation.gate_status === "blocked"
                      ? "Blocked — the detector did not run, so the gate cannot pass."
                      : `Gate ${evaluation.gate_status} · ${evaluation.findings.length} finding(s) · iteration ${evaluation.iteration}`}
                </p>
              </div>
            </div>
          </section>

          <section>
            <Eyebrow>MCP servers</Eyebrow>
            <ul className="mt-3 space-y-0.5">
              {MCP_SERVERS.map((s) => (
                <li key={s.name} className="flex items-center gap-2.5 py-1.5">
                  <s.icon size={14} className="shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-[13px] text-foreground/85">
                    {s.name}
                  </span>
                  <span
                    aria-hidden="true"
                    className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT.idle)}
                  />
                  <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                    No status
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2.5 text-[12px] leading-relaxed text-muted-foreground">
              The backend exposes no MCP status endpoint yet.
            </p>
          </section>
        </div>
      </Rail>
    </div>
  );
}
