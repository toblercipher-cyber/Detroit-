/**
 * Account Dashboard — per-account usage and credit consumption (`/dashboard`).
 *
 * Why this file exists:
 * It gives an account holder one place to see what their builds have cost: how
 * much of the billing cycle's credit allowance is gone, where it went, and which
 * projects spent it.
 *
 * Layer: frontend presentation (route page component).
 *
 * ---------------------------------------------------------------------------
 * HALF OF THIS PAGE IS NOW REAL. THE OTHER HALF IS MARKED.
 *
 * The backend exposes project and run information, and those widgets now read
 * it through `@/lib/api`. It exposes NO concept of a credit, a token, a cost,
 * a billing cycle or an allowance — searching `backend/` for
 * token/usage/credit/cost/billing finds nothing but prose inside agent
 * prompts. Those widgets therefore keep their existing composition and carry a
 * visible `NO BACKEND SOURCE` chip, rather than being deleted or silently
 * dressed up as live figures.
 *
 * REAL, fetched once on mount:
 *   GET /api/v1/projects              the project list
 *   GET /api/v1/projects/{id}         per project: evaluation_results
 *   GET /api/v1/projects/{id}/status  per project: stage, status,
 *                                     iteration_count, and max_iterations —
 *                                     configuration, not a frontend literal
 *
 * Which widgets that makes real:
 *   "Projects"            titles, real stage and status labels, real
 *                         iteration_count against the real configured limit
 *   "Correction spend"    the total and the per-project iteration list
 *   "Quality gate"        aggregated from evaluation_results across runs,
 *                         including a blocked gate, which is never shown as
 *                         a pass (CLAUDE.md §15)
 *
 * NOT REAL, and chipped as such in the UI:
 *   credits used / allowance / remaining / cycle dates / renewal countdown,
 *   the daily spend strip, the day × hour burn map, per-stage credit
 *   attribution, per-project credit cost, and the plan name
 *
 * The numbers behind those chips are the same illustrative shapes this page
 * shipped with. They are kept because the composition is approved and the
 * instruction is to preserve it; the chip is what stops a reader believing
 * them.
 *
 * STILL NOT AUTHENTICATED. `docs/api.md` §1.1: "There is no authentication in
 * v1. The backend is a local single-user service." There is no user identity
 * to key off, so the account block names the workspace instead of a person,
 * matching the approved treatment in `pages/chat.tsx`.
 *
 * NOT IN THE v1 SCOPE TABLE. `docs/frontend.md` §1 lists `/`, `/pricing` and
 * `/chat`. A dashboard is a scope expansion, requested directly by the user.
 *
 * DESIGN
 * Structure follows the supplied dashboard reference: grouped left nav with the
 * account pinned at the bottom, a dense metric grid, and a right column of
 * stacked summary cards. Its palette does NOT — the reference is blue on navy,
 * and the approved ember-on-warm-near-black system wins (`docs/design.md` §3).
 * The widget language (mono eyebrow, oversized figure, bar strip, status tones)
 * is reused from `components/dashboard-section.tsx`, which `docs/design.md` §3
 * names as the treatment to reuse rather than reinvent.
 *
 * KNOWN DUPLICATION
 * The collapsing side rail and the stage-label table both repeat
 * `pages/chat.tsx`. Extracting either would mean editing that file too, so both
 * are left for a follow-up rather than smuggled into this change.
 * ---------------------------------------------------------------------------
 */

import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  ChevronRight,
  CreditCard,
  Gauge,
  Layers,
  LayoutDashboard,
  PanelLeft,
  Receipt,
  Settings,
  TriangleAlert,
  X,
  Zap,
} from "lucide-react";

import dashboardPageBg from "@/assets/dashboard-page-bg.jpg";
import {
  ApiError,
  getProject,
  getProjectStatus,
  isApiError,
  listProjects,
  type GateStatus,
  type RunStatus,
  type WorkflowStage,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ *
 * Real backend vocabulary.
 *
 * The values live in `@/lib/api`, transcribed from `backend/schemas/enums.py`.
 * Only the labels are a UI decision. `Record<WorkflowStage, string>` makes a
 * new backend stage a compile error rather than an unlabelled row.
 * ------------------------------------------------------------------ */

const STAGE_LABEL: Record<WorkflowStage, string> = {
  requirement_analysis: "Requirement analysis",
  planning: "Planning",
  design: "Design",
  reference_analysis: "Reference analysis",
  frontend_architecture: "Frontend architecture",
  build: "Build",
  preview: "Preview",
  evaluation: "Evaluation",
  targeted_fix: "Targeted fix",
  re_evaluation: "Re-evaluation",
  final_validation: "Final validation",
  external_action: "External action",
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

/* ------------------------------------------------------------------ *
 * ILLUSTRATIVE FIGURES — NOT BACKED BY THE BACKEND.
 *
 * Every constant in this block describes a credit, a cost or a billing cycle.
 * The backend models none of those: there is no usage endpoint, no token
 * counter and no billing concept in `ProjectState` or in any route. They are
 * retained only so the approved composition still renders, and every widget
 * that reads one carries a `NO BACKEND SOURCE` chip.
 *
 * Do not wire these to anything. When a real usage source exists, these go.
 * ------------------------------------------------------------------ */

const CYCLE = {
  label: "Jul 1 – Jul 31",
  allowance: 2500,
  used: 1618,
  renewsIn: 13,
};

/** Daily credit spend across the cycle. Last entry is the current day. */
const DAILY_SPEND = [
  18, 42, 36, 0, 0, 64, 91, 73, 58, 120, 96, 44, 0, 61, 88, 134, 102, 77, 59, 0,
  83, 118, 95, 66, 41, 72, 110, 87,
];

/** Attribution by pipeline stage. The stage names are real; the credits are not. */
const BY_STAGE = [
  { stage: "Build", credits: 612 },
  { stage: "Evaluation", credits: 324 },
  { stage: "Design", credits: 241 },
  { stage: "Frontend architecture", credits: 188 },
  { stage: "Planning", credits: 134 },
  { stage: "Requirement analysis", credits: 72 },
  { stage: "Reference analysis", credits: 47 },
];

/** No plan, no subscription and no sign-in exist (`docs/api.md` §1.1). */
const PLAN_NAME = "Studio";

/* ------------------------------------------------------------------ *
 * Account context
 * ------------------------------------------------------------------ */

/**
 * There is no authentication and no user record, so this names the workspace
 * rather than inventing a person. Kept as a prop so a real account can be
 * passed in unchanged once auth exists.
 */
const ACCOUNT = {
  name: "Local workspace",
  email: "No sign-in required",
  initials: "D",
};

const NAV_GROUPS = [
  {
    label: "Overview",
    items: [
      { icon: LayoutDashboard, label: "Dashboard", href: "/dashboard", active: true },
      { icon: Layers, label: "Projects", href: "/chat" },
    ],
  },
  {
    label: "Usage",
    items: [
      { icon: Gauge, label: "Credits", href: "/dashboard" },
      { icon: Activity, label: "Runs", href: "/dashboard" },
    ],
  },
  {
    label: "Account",
    items: [
      { icon: Receipt, label: "Billing", href: "/pricing" },
      { icon: Settings, label: "Settings", href: "/dashboard" },
    ],
  },
];

/* ------------------------------------------------------------------ *
 * Widget language, reused from `components/dashboard-section.tsx`
 * ------------------------------------------------------------------ */

type Tone = "ok" | "warn" | "err" | "idle";

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-emerald-400",
  warn: "text-amber-300",
  err: "text-rose-400",
  idle: "text-muted-foreground",
};

const TONE_DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  err: "bg-rose-500",
  idle: "bg-muted-foreground/60",
};

const fmt = (v: number) => v.toLocaleString("en-US");

/**
 * Marks a figure the backend cannot supply.
 *
 * Deliberately repeated per card rather than explained once at the top of the
 * page: a reader who looks at one tile should not have to find a legend
 * elsewhere to know the number under it is placeholder content.
 */
function Unbacked({ what = "no backend source" }: { what?: string }) {
  return (
    <span
      title="This figure has no backend source yet. It is placeholder content, not a measurement."
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-amber-500/35 bg-amber-500/[0.07] px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-amber-300/90"
    >
      <span aria-hidden="true" className="size-1 rounded-full bg-amber-400" />
      {what}
    </span>
  );
}

function Card({
  title,
  meta,
  children,
  className,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "flex flex-col rounded-2xl border border-border bg-card/60 p-5",
        className,
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          {title}
        </h2>
        {meta && <span className="shrink-0 text-[12px] text-muted-foreground">{meta}</span>}
      </header>
      <div className="mt-4 flex min-h-0 flex-1 flex-col">{children}</div>
    </section>
  );
}

function Figure({ children, unit }: { children: ReactNode; unit?: string }) {
  return (
    <p className="font-display text-[32px] font-bold leading-none tracking-tight tabular-nums text-foreground">
      {children}
      {unit && (
        <span className="ml-1.5 font-sans text-xs font-normal tracking-normal text-muted-foreground">
          {unit}
        </span>
      )}
    </p>
  );
}

/** A failure, in the backend's own words. Never a message composed here. */
function ErrorNotice({ error }: { error: ApiError }) {
  return (
    <div
      role="alert"
      className="mb-4 flex items-start gap-2.5 rounded-lg border border-rose-500/40 bg-rose-500/[0.07] px-4 py-3"
    >
      <TriangleAlert size={15} className="mt-0.5 shrink-0 text-rose-400" />
      <div className="min-w-0">
        <p className="text-[13px] leading-relaxed text-foreground/90">{error.message}</p>
        <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
          {error.code}
          {error.requestId ? ` · ${error.requestId}` : ""}
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Burn map — credit spend binned by day of week × hour of day.
 *
 * NO BACKEND SOURCE. The backend records no timestamped cost of any kind, so
 * every cell below comes from `burnAt`. The widget is kept because the
 * composition is approved; its card carries the chip.
 *
 * Why a hex grid and not more bars: the cycle total and the daily bars
 * above both collapse the time of day. This is the only view that shows
 * *when* credits burn, which is what tells you whether spend is a working
 * -hours pattern or an overnight loop that got away from someone.
 *
 * Drawn as SVG rather than CSS clip-path so it scales to any column width
 * without 168 elements fighting over subpixel offsets.
 * ------------------------------------------------------------------ */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = 24;

/** Hex radius, centre to vertex. Everything else is derived from it. */
const HEX_R = 10;
const HEX_W = Math.sqrt(3) * HEX_R;
const HEX_ROW = 1.5 * HEX_R;

/** Deterministic jitter, same approach as `dashboard-section.tsx`. */
function noise(seed: number) {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/** Illustrative spend shape: a working-hours peak, quieter at the weekend. */
function burnAt(day: number, hour: number) {
  const weekend = day >= 5 ? 0.3 : 1;
  const shape =
    Math.exp(-((hour - 14) ** 2) / 26) * 88 + Math.exp(-((hour - 10) ** 2) / 12) * 46;
  return Math.max(
    0,
    Math.round((shape * weekend + 3) * (0.55 + noise(day * 31 + hour) * 0.9)),
  );
}

/** Pointy-top hexagon: vertices at -90°, -30°, 30°, 90°, 150°, 210°. */
function hexPoints(cx: number, cy: number, r: number) {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 180) * (60 * i - 90);
    return `${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`;
  }).join(" ");
}

/**
 * Six steps: a neutral "no spend" plus five ember intensities.
 *
 * The ramp varies LIGHTNESS, not the alpha of a single hue. Fading one ember
 * over a warm dark card compresses every step into a narrow band of muddy
 * brown — the levels stop being distinguishable, which is the whole job of a
 * heat scale. Climbing 14% → 68% lightness keeps each step legible, and the
 * brand ember (14 88% 55%) sits at step 4 so the scale peaks past it rather
 * than washing it out.
 */
const HEX_FILL = [
  "hsl(20 8% 15%)",
  "hsl(14 45% 24%)",
  "hsl(14 62% 34%)",
  "hsl(14 78% 45%)",
  "hsl(14 88% 55%)",
  "hsl(14 94% 70%)",
];

type Cell = { day: number; hour: number; credits: number; level: number };

function BurnMap() {
  const [hovered, setHovered] = useState<Cell | null>(null);

  const raw = DAYS.map((_, d) =>
    Array.from({ length: HOURS }, (_, h) => burnAt(d, h)),
  );
  const peak = Math.max(...raw.flat());
  const total = raw.flat().reduce((a, v) => a + v, 0);

  const cells: Cell[] = [];
  raw.forEach((row, day) =>
    row.forEach((credits, hour) => {
      const level = credits <= 2 ? 0 : Math.max(1, Math.ceil((credits / peak) * 5));
      cells.push({ day, hour, credits, level });
    }),
  );

  const peakCell = cells.reduce((a, c) => (c.credits > a.credits ? c : a), cells[0]);
  const shown = hovered ?? peakCell;

  const vbW = HEX_W * (HOURS + 0.5);
  const vbH = HEX_ROW * (DAYS.length - 1) + HEX_R * 2;

  /** Legend bands, derived from the live peak rather than hard-coded. */
  const bands = HEX_FILL.map((fill, i) => ({
    fill,
    label:
      i === 0
        ? "0"
        : `${Math.round((peak / 5) * (i - 1)) + 1}–${Math.round((peak / 5) * i)}`,
  }));

  return (
    <div className="flex flex-col gap-4 @container sm:flex-row sm:items-start">
      <div className="relative min-w-0 flex-1">
        <svg
          viewBox={`0 0 ${vbW.toFixed(1)} ${vbH.toFixed(1)}`}
          className="w-full"
          role="img"
          aria-label={`Placeholder credit burn by day and hour across the week; this figure has no backend source. ${fmt(total)} credits total. Busiest hour is ${DAYS[peakCell.day]} at ${String(peakCell.hour).padStart(2, "0")}:00 with ${peakCell.credits} credits.`}
          onMouseLeave={() => setHovered(null)}
        >
          {cells.map((c) => {
            const cx = HEX_W * (c.hour + (c.day % 2) / 2) + HEX_W / 2;
            const cy = HEX_ROW * c.day + HEX_R;
            const isShown = shown.day === c.day && shown.hour === c.hour;
            return (
              <polygon
                key={`${c.day}-${c.hour}`}
                points={hexPoints(cx, cy, HEX_R * 0.92)}
                fill={HEX_FILL[c.level]}
                stroke={isShown ? "hsl(var(--foreground))" : "none"}
                strokeWidth={isShown ? 1.2 : 0}
                onMouseEnter={() => setHovered(c)}
              />
            );
          })}
        </svg>

        <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground/70">
          Mon — Sun · 00:00 — 23:00
        </p>
      </div>

      {/* Readout. A panel rather than a floating tooltip: it never covers the
          data, and it still reads correctly without a pointer. */}
      <div className="flex shrink-0 gap-4 sm:w-[168px] sm:flex-col">
        <div className="min-w-0 flex-1 rounded-lg border border-border bg-background/60 px-3 py-2.5">
          <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
            {hovered ? "Selected" : "Busiest hour"}
          </p>
          <p className="mt-1 truncate text-[13px] text-foreground">
            {DAYS[shown.day]} · {String(shown.hour).padStart(2, "0")}:00
          </p>
          <p className="mt-0.5 font-display text-lg font-bold tabular-nums leading-none text-ember">
            {shown.credits}
            <span className="ml-1 font-sans text-[11px] font-normal text-muted-foreground">
              credits
            </span>
          </p>
        </div>

        <ul className="flex shrink-0 flex-col justify-center gap-1">
          {bands.map((b) => (
            <li key={b.label} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="size-2.5 shrink-0 rounded-[2px]"
                style={{ background: b.fill }}
              />
              <span className="font-mono text-[9px] tabular-nums text-muted-foreground">
                {b.label}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Real project data
 * ------------------------------------------------------------------ */

/**
 * One project, assembled from the two endpoints that describe it.
 *
 * `maxIterations` comes from `StatusResponse` rather than a literal `3`,
 * because the limit is configuration (`MAX_AUTO_ITERATIONS`) and a frontend
 * copy of it would silently lie the moment it is changed.
 */
type ProjectRow = {
  id: string;
  title: string;
  stage: WorkflowStage;
  status: RunStatus;
  iterations: number | null;
  maxIterations: number | null;
  gate: GateStatus | null;
  findings: number;
};

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export function DashboardPage({ account = ACCOUNT }: { account?: typeof ACCOUNT }) {
  const [navOpen, setNavOpen] = useState(false);
  const [rows, setRows] = useState<ProjectRow[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  /**
   * One snapshot on mount. This is a review surface, not a live console — the
   * builder workspace at `/chat` is what polls a running build. Each project's
   * detail is loaded independently so a single failure degrades one row to its
   * summary instead of blanking the list.
   */
  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        const list = await listProjects();
        if (!alive) return;

        const loaded = await Promise.all(
          list.projects.map(async (summary): Promise<ProjectRow> => {
            const base: ProjectRow = {
              id: summary.project_id,
              title: summary.title,
              stage: summary.stage,
              status: summary.status,
              iterations: null,
              maxIterations: null,
              gate: null,
              findings: 0,
            };
            try {
              const [detail, status] = await Promise.all([
                getProject(summary.project_id),
                getProjectStatus(summary.project_id),
              ]);
              return {
                ...base,
                stage: status.stage,
                status: status.status,
                iterations: status.iteration_count,
                maxIterations: status.max_iterations,
                gate: detail.evaluation_results?.gate_status ?? null,
                findings: detail.evaluation_results?.findings.length ?? 0,
              };
            } catch {
              return base;
            }
          }),
        );

        if (alive) {
          setRows(loaded);
          setError(null);
        }
      } catch (cause) {
        if (alive && isApiError(cause)) {
          setError(cause);
          setRows([]);
        }
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  /* ---- Figures with no backend source ---- */
  const remaining = CYCLE.allowance - CYCLE.used;
  const usedPct = Math.round((CYCLE.used / CYCLE.allowance) * 100);
  const peakSpend = Math.max(...DAILY_SPEND);
  const stagePeak = Math.max(...BY_STAGE.map((s) => s.credits));
  /* Over 85% of the allowance with days left is worth flagging, not hiding. */
  const budgetTone: Tone = usedPct >= 85 ? "err" : usedPct >= 70 ? "warn" : "ok";

  /* ---- Figures the backend actually provides ---- */
  const projects = rows ?? [];
  const loading = rows === null;
  const corrected = projects.filter((p) => (p.iterations ?? 0) > 0);
  const totalIterations = projects.reduce((a, p) => a + (p.iterations ?? 0), 0);
  /* The limit is one configuration value, so any run that reported it will do. */
  const maxIterations =
    projects.find((p) => p.maxIterations !== null)?.maxIterations ?? null;

  const evaluated = projects.filter((p) => p.gate !== null);
  const gateCounts = {
    passed: evaluated.filter((p) => p.gate === "passed").length,
    failed: evaluated.filter((p) => p.gate === "failed").length,
    blocked: evaluated.filter((p) => p.gate === "blocked").length,
  };
  const totalFindings = projects.reduce((a, p) => a + p.findings, 0);
  /* A blocked gate outranks everything: it means the mandatory check did not
     run, which is never a pass (CLAUDE.md §15). */
  const gateTone: Tone = gateCounts.blocked
    ? "err"
    : gateCounts.failed
      ? "warn"
      : gateCounts.passed
        ? "ok"
        : "idle";

  return (
    <div className="relative isolate flex h-screen overflow-hidden bg-background text-foreground">
      {/* Page ground. It sits on the root rather than per-section, so it does
          not scroll with the content area and stays put behind every tile.

          The scrim is far heavier here than on the Product page, and
          deliberately so: this is a dense working surface, and its cards are
          `bg-card/60` — translucent — so the photograph reads *through* the
          numbers unless it is held well down. ~18% transmission keeps the
          spiral legible as texture while the figures stay the brightest thing
          on screen. The left rail is opaque `bg-background`, so navigation is
          unaffected. */}
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10 bg-cover bg-center"
        style={{ backgroundImage: `url(${dashboardPageBg})` }}
      />
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10"
        style={{
          background:
            "linear-gradient(180deg, rgba(17,15,14,0.90) 0%, rgba(17,15,14,0.82) 35%, rgba(17,15,14,0.86) 70%, rgba(17,15,14,0.95) 100%)",
        }}
      />
      {/* ---------- Left rail: navigation ---------- */}
      {navOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-40 bg-background/70 lg:hidden"
        />
      )}

      <aside
        aria-label="Account navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[260px] shrink-0 flex-col border-r border-border bg-background transition-transform duration-300 ease-out lg:static lg:z-auto lg:translate-x-0",
          navOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-border px-5">
          <a href="/" className="font-display text-base font-bold tracking-tight">
            DETROIT<span className="text-ember">-AI</span>
          </a>
          <button
            type="button"
            onClick={() => setNavOpen(false)}
            aria-label="Close navigation"
            className="text-muted-foreground lg:hidden"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="min-h-0 flex-1 space-y-6 overflow-y-auto px-3 py-5">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <p className="px-3 pb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.label}>
                    <a
                      href={item.href}
                      aria-current={item.active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2.5 border-l-2 px-3 py-2 text-[13px] transition-colors",
                        item.active
                          ? "border-l-ember bg-secondary/70 text-foreground"
                          : "border-l-transparent text-muted-foreground hover:bg-secondary/40 hover:text-foreground",
                      )}
                    >
                      <item.icon
                        size={14}
                        className={cn("shrink-0", item.active && "text-ember")}
                      />
                      {item.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-3 border-t border-border px-5 py-4">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-card font-mono text-[10px] text-foreground/80">
            {account.initials}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] text-foreground">{account.name}</span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {account.email}
            </span>
          </span>
        </div>
      </aside>

      {/* ---------- Main ---------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border px-4 sm:px-6">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            className="shrink-0 text-muted-foreground transition-colors hover:text-foreground lg:hidden"
          >
            <PanelLeft size={18} />
          </button>

          <h1 className="min-w-0 flex-1 truncate font-display text-lg font-bold tracking-tight">
            Dashboard
          </h1>

          {/* No billing cycle exists server-side; this pill is placeholder. */}
          <span className="hidden shrink-0 items-center gap-2 rounded-full border border-border bg-secondary/60 px-3 py-1 sm:inline-flex">
            <CreditCard size={12} className="text-muted-foreground" />
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-foreground/80">
              {CYCLE.label}
            </span>
            <Unbacked what="placeholder" />
          </span>

          <Button asChild size="sm" className="shrink-0">
            <a href="/chat">New build</a>
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1200px] px-4 py-6 sm:px-6 sm:py-8">
            {error && <ErrorNotice error={error} />}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.55fr_1fr]">
              {/* ----- Left column ----- */}
              <div className="flex flex-col gap-4">
                <Card title="Credits used this cycle" meta={<Unbacked />}>
                  <div className="flex flex-wrap items-end justify-between gap-4">
                    <Figure unit={`of ${fmt(CYCLE.allowance)}`}>{fmt(CYCLE.used)}</Figure>
                    <p className="text-[13px] text-muted-foreground">
                      <span className={TONE_TEXT[budgetTone]}>{fmt(remaining)}</span> left ·
                      renews in {CYCLE.renewsIn} days
                    </p>
                  </div>

                  <div
                    role="img"
                    aria-label={`Placeholder daily credit spend across the cycle; this figure has no backend source. It peaks at ${peakSpend} credits.`}
                    className="mt-6 flex h-24 items-end gap-[3px]"
                  >
                    {DAILY_SPEND.map((v, i) => (
                      <span
                        key={i}
                        className={cn(
                          "flex-1 rounded-full",
                          i === DAILY_SPEND.length - 1 ? "bg-ember" : "bg-foreground/15",
                        )}
                        style={{ height: `${Math.max((v / peakSpend) * 100, 2)}%` }}
                      />
                    ))}
                  </div>
                  <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground/70">
                    Jul 1 — today
                  </p>
                </Card>

                <Card
                  title="When credits burn"
                  meta={
                    <span className="inline-flex items-center gap-2">
                      day × hour <Unbacked />
                    </span>
                  }
                >
                  <BurnMap />
                </Card>

                <Card
                  title="Where the credits went"
                  meta={
                    <span className="inline-flex items-center gap-2">
                      by pipeline stage <Unbacked />
                    </span>
                  }
                >
                  <dl className="space-y-2.5">
                    {BY_STAGE.map((s) => (
                      <div key={s.stage} className="flex items-center gap-3 text-[12px]">
                        <dt className="w-[150px] shrink-0 truncate text-foreground/80">
                          {s.stage}
                        </dt>
                        <dd className="flex min-w-0 flex-1 items-center gap-3">
                          <span className="h-[3px] flex-1 rounded-full bg-foreground/[0.08]">
                            <span
                              className={cn(
                                "block h-full rounded-full",
                                s.credits === stagePeak ? "bg-ember" : "bg-foreground/30",
                              )}
                              style={{ width: `${(s.credits / stagePeak) * 100}%` }}
                            />
                          </span>
                          <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">
                            {fmt(s.credits)}
                          </span>
                        </dd>
                      </div>
                    ))}
                  </dl>
                </Card>

                {/* REAL: GET /projects, plus per-project status for the stage,
                    the run status, the iteration count and the configured
                    limit. The credit column is the one part with no source, so
                    it reads "—". */}
                <Card
                  title="Projects"
                  meta={
                    <span className="inline-flex items-center gap-2">
                      {loading ? "loading…" : `${projects.length} total`}
                      <Unbacked what="credits only" />
                    </span>
                  }
                >
                  {loading ? (
                    <p className="text-[13px] text-muted-foreground">
                      Reading projects from the builder backend…
                    </p>
                  ) : error ? (
                    /* "None" and "could not ask" are different answers, and
                       only one of them is true here. */
                    <p className="text-[13px] text-muted-foreground">
                      The project list could not be read — see the error above.
                    </p>
                  ) : projects.length === 0 ? (
                    <p className="text-[13px] text-muted-foreground">
                      No projects yet. Start one from the builder workspace.
                    </p>
                  ) : (
                    <ul className="divide-y divide-border">
                      {projects.map((p) => (
                        <li
                          key={p.id}
                          className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13px] text-foreground">
                              {p.title}
                            </span>
                            <span className="block font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
                              {STAGE_LABEL[p.stage]} · {RUN_STATUS_LABEL[p.status]}
                            </span>
                          </span>
                          {p.iterations !== null &&
                            p.maxIterations !== null &&
                            p.iterations > 0 && (
                              <span
                                title={`${p.iterations} of ${p.maxIterations} automatic correction iterations used`}
                                className={cn(
                                  "shrink-0 font-mono text-[9px] uppercase tracking-[0.12em]",
                                  p.iterations >= p.maxIterations
                                    ? TONE_TEXT.err
                                    : TONE_TEXT.warn,
                                )}
                              >
                                {p.iterations}/{p.maxIterations} fixes
                              </span>
                            )}
                          <span
                            title="Per-project cost has no backend source"
                            className="w-14 shrink-0 text-right text-[13px] tabular-nums text-muted-foreground/50"
                          >
                            —
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              </div>

              {/* ----- Right column ----- */}
              <div className="flex flex-col gap-4">
                <Card title="Allowance remaining" meta={<Unbacked />}>
                  <Figure unit="remaining">{100 - usedPct}%</Figure>
                  <div
                    role="img"
                    aria-label={`Placeholder allowance meter with no backend source; it shows ${usedPct}% of a cycle allowance used.`}
                    className="mt-5 flex gap-[3px]"
                  >
                    {Array.from({ length: 40 }).map((_, i) => (
                      <span
                        key={i}
                        className={cn(
                          "h-6 flex-1 rounded-[2px]",
                          i < Math.round((usedPct / 100) * 40)
                            ? "bg-ember"
                            : "bg-foreground/[0.08]",
                        )}
                      />
                    ))}
                  </div>
                  <div className="mt-3 flex justify-between font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    <span>{fmt(CYCLE.used)} used</span>
                    <span>{fmt(CYCLE.allowance)}</span>
                  </div>
                </Card>

                <Card
                  title="Plan"
                  meta={
                    <span className="inline-flex items-center gap-2">
                      <Unbacked />
                      <a href="/pricing" className="text-ember hover:underline">
                        Change
                      </a>
                    </span>
                  }
                >
                  <dl className="space-y-3 text-[13px]">
                    <div className="flex items-center justify-between gap-3">
                      <dt className="text-muted-foreground">Current</dt>
                      <dd className="font-medium text-foreground">{PLAN_NAME}</dd>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <dt className="text-muted-foreground">Cycle</dt>
                      <dd className="tabular-nums text-foreground/80">{CYCLE.label}</dd>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <dt className="text-muted-foreground">Monthly credits</dt>
                      <dd className="tabular-nums text-foreground/80">
                        {fmt(CYCLE.allowance)}
                      </dd>
                    </div>
                  </dl>
                </Card>

                {/* REAL. The iteration counts come from the backend; only their
                    conversion into a credit cost does not exist, so the figure
                    reads in iterations — the unit the backend reports. */}
                <Card title="Correction spend" meta="iterations">
                  <Figure unit={totalIterations === 1 ? "iteration" : "iterations"}>
                    {loading ? "—" : totalIterations}
                  </Figure>
                  <p className="mt-3 text-[12px] leading-relaxed text-muted-foreground">
                    {maxIterations === null
                      ? "Automatic correction iterations across every run. The per-run cap is reported by the backend once a run exists."
                      : `Automatic correction iterations across every run. Each run is capped at ${maxIterations} before it returns to you.`}
                  </p>
                  {corrected.length > 0 && (
                    <ul className="mt-4 space-y-2">
                      {corrected.map((p) => (
                        <li key={p.id} className="flex items-center gap-2 text-[12px]">
                          <span
                            aria-hidden="true"
                            className={cn(
                              "size-1.5 shrink-0 rounded-full",
                              p.maxIterations !== null &&
                                (p.iterations ?? 0) >= p.maxIterations
                                ? TONE_DOT.err
                                : TONE_DOT.warn,
                            )}
                          />
                          <span className="min-w-0 flex-1 truncate text-foreground/80">
                            {p.title}
                          </span>
                          <span className="shrink-0 tabular-nums text-muted-foreground">
                            {p.iterations}
                            {p.maxIterations !== null ? `/${p.maxIterations}` : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>

                {/* REAL: aggregated from evaluation_results across every run.
                    A blocked gate means the mandatory detector did not run, and
                    is never rendered as a pass (CLAUDE.md §15). */}
                <Card
                  title="Quality gate"
                  meta={
                    evaluated.length === 0 ? "no runs yet" : `${evaluated.length} evaluated`
                  }
                >
                  <div className="flex items-start gap-2.5">
                    <Zap size={15} className={cn("mt-0.5 shrink-0", TONE_TEXT[gateTone])} />
                    <div className="min-w-0">
                      <p className="text-[13px] text-foreground">Impeccable</p>
                      <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
                        {loading
                          ? "Reading evaluation results…"
                          : evaluated.length === 0
                            ? "Mandatory on every build. No run has reached evaluation yet."
                            : gateCounts.blocked > 0
                              ? `${gateCounts.blocked} run(s) blocked — the detector did not run, so those gates cannot pass.`
                              : `${gateCounts.passed} passed · ${gateCounts.failed} failed · ${totalFindings} finding(s).`}
                      </p>
                    </div>
                  </div>
                </Card>

                <a
                  href="/chat"
                  className="group flex items-center justify-between gap-3 rounded-2xl border border-border bg-card/40 px-5 py-4 transition-colors hover:border-foreground/25 hover:bg-card/70"
                >
                  <span>
                    <span className="block text-[13px] font-medium text-foreground">
                      Start a new build
                    </span>
                    <span className="block text-[12px] text-muted-foreground">
                      Open the builder workspace
                    </span>
                  </span>
                  <ChevronRight
                    size={16}
                    className="shrink-0 text-muted-foreground transition-colors group-hover:text-ember"
                  />
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
