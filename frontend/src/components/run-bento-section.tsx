/**
 * "One run" bento section of the Landing Page.
 *
 * Why this file exists:
 * It replaces `demo-video-section` — the interactive three.js factory that sat
 * in the demo slot. That section spent a 24rem-tall stage and a large `three`
 * chunk restating something the page already says three times over (hero,
 * about, harness), and it asked the visitor to drive a toy before it told them
 * anything. This section answers the same question — *what actually happens
 * when I press go?* — as readable surface instead of a scene.
 *
 * Layer: frontend presentation (Landing Page section).
 *
 * The layout is the bento the user supplied: a tall left card, a right column
 * split into a list card and a stacked metric strip + chart card, and a
 * full-width action rail underneath. What was *not* carried over is the
 * reference's finance content and its own design system — `docs/design.md` §1
 * makes the approved Landing Page the source of truth, and §3 says layout and
 * structure are adopted from a reference while colour, type and radius are
 * not. So: ember accent, Syne/Plex/Space Mono, `rounded-3xl` surfaces on the
 * white/[0.03] hairline treatment every other section uses, and a photographic
 * background under the standard four-stop scrim.
 *
 * Three substitutions worth naming:
 *
 * 1. No `recharts`, no `@tabler/icons-react`, no shadcn `Card`. The reference
 *    pulls all three; this project charts with hand-rolled SVG
 *    (`dashboard-section`, `contribution-skyline`), draws with `lucide-react`,
 *    and composes surfaces from plain divs. Adding a charting runtime to the
 *    approved Landing Page for one four-point series is not a trade worth
 *    making, and `docs/design.md` §3 says to reuse the chart treatment this
 *    project already ships.
 *
 * 2. The reference's hero control is a button that fakes a "Connecting…"
 *    state on a 2s timer. A landing page CTA that pretends to do something is
 *    the one control that must not be fake, so it is a real link into `/chat`.
 *
 * 3. Every number here either describes a fixed property of the pipeline or is
 *    labelled as one illustrative run. This page has been burned once already
 *    by copy asserting capability the product did not have (see the note in
 *    `mcp-section`), so nothing below claims usage it cannot show.
 */

import type { ReactNode } from "react";
import {
  ArrowUpRight,
  Blocks,
  Image as ImageIcon,
  ListChecks,
  ListTree,
  Palette,
  ScanEye,
  TrendingDown,
  type LucideIcon,
} from "lucide-react";

import phaseTwoBg from "@/assets/phase-two-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ *
 * Shared surface
 * ------------------------------------------------------------------ */

/**
 * One bento tile. Every card in this section shares the hairline-on-glass
 * treatment the rest of the Landing Page uses, so the grid reads as one
 * object rather than four pasted widgets.
 */
function Tile({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-3xl border border-white/10 bg-white/[0.03] backdrop-blur-sm",
        "transition-colors duration-300 hover:border-white/20",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** The section's recurring label voice: mono, uppercase, wide. */
function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
      {children}
    </p>
  );
}

/* ------------------------------------------------------------------ *
 * 1. Agent lattice — the left tall tile
 * ------------------------------------------------------------------ */

interface Role {
  id: string;
  /** Short enough to sit under an icon in a ~70px tile. */
  name: string;
  /** The role's actual name, for the accessible description and the tooltip. */
  full: string;
  icon: LucideIcon;
  /** Position in the 3x3 lattice; the gaps are the point. */
  col: number;
  row: number;
}

/**
 * Four of the twelve roles, scattered across a 3x3 lattice.
 *
 * The empty cells carry the argument: the harness wakes a role because its
 * specialisation is relevant, not because it exists (`CLAUDE.md` §6). A full
 * grid would say the opposite.
 */
const ROLES: readonly Role[] = [
  { id: "planner", name: "Plan", full: "Planner", icon: ListTree, col: 2, row: 0 },
  {
    id: "design",
    name: "Design",
    full: "Design Director",
    icon: Palette,
    col: 0,
    row: 1,
  },
  { id: "builder", name: "Build", full: "Builder", icon: Blocks, col: 2, row: 1 },
  {
    id: "antislop",
    name: "Review",
    full: "Anti-Slop Reviewer",
    icon: ScanEye,
    col: 1,
    row: 2,
  },
];

const LATTICE = Array.from(
  { length: 9 },
  (_, i) =>
    ROLES.find((r) => r.row === Math.floor(i / 3) && r.col === i % 3) ?? null,
);

function AgentLattice() {
  return (
    <div
      className="mx-auto grid w-full max-w-[260px] grid-cols-3 gap-2.5"
      role="img"
      aria-label={`Four of twelve agent roles are active on this run: ${ROLES.map(
        (r) => r.full,
      ).join(", ")}.`}
    >
      {LATTICE.map((role, i) =>
        role ? (
          <div
            key={role.id}
            title={role.full}
            className="group/tile flex aspect-square flex-col items-center justify-center gap-1.5 overflow-hidden rounded-2xl border border-ember/30 bg-ember/[0.07] p-1.5 transition-colors duration-300 hover:border-ember/60"
          >
            <role.icon
              className="h-5 w-5 shrink-0 text-ember transition-transform duration-300 group-hover/tile:scale-110"
              aria-hidden="true"
            />
            <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-foreground/70">
              {role.name}
            </span>
          </div>
        ) : (
          <div
            key={`gap-${i}`}
            aria-hidden="true"
            className="aspect-square rounded-2xl border border-dashed border-white/10 bg-white/[0.015]"
          />
        ),
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 2. Approval gates
 * ------------------------------------------------------------------ */

const GATES = [
  {
    id: "plan",
    title: "Plan",
    stage: "Stage 1 · Planner",
    detail: "6 sections",
    status: "Approved",
    icon: ListChecks,
    live: false,
  },
  {
    id: "design",
    title: "Design direction",
    stage: "Stage 2 · Design Director",
    detail: "Syne / ember",
    status: "Waiting on you",
    icon: Palette,
    live: true,
  },
  {
    id: "reference",
    title: "Reference read",
    stage: "Stage 3 · Reference Analyzer",
    detail: "1 image",
    status: "Queued",
    icon: ImageIcon,
    live: false,
  },
] as const;

function GatesTile() {
  return (
    <Tile className="flex h-full flex-col gap-5 p-5 sm:p-6">
      <div className="space-y-1.5">
        <h3 className="font-display text-lg font-bold tracking-tight text-foreground">
          Nothing ships past you
        </h3>
        <p className="text-sm leading-relaxed text-foreground/70">
          The run stops at each checkpoint and waits. Approve it, pick a
          variation, or write your own instruction.
        </p>
      </div>

      <ul className="flex flex-col gap-2.5">
        {GATES.map((gate) => (
          <li
            key={gate.id}
            className="rounded-2xl border border-white/10 bg-background/40 p-3 transition-colors duration-200 hover:border-white/20"
          >
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-foreground">
                <gate.icon className="h-4 w-4" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-foreground">
                  {gate.title}
                </p>
                <p className="truncate font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                  {gate.stage}
                </p>
              </div>
            </div>

            <div className="mt-3 flex items-center justify-between gap-2">
              <span className="font-mono text-xs text-foreground/70">
                {gate.detail}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-full border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em]",
                  gate.live
                    ? "border-ember/50 bg-ember/10 text-ember"
                    : "border-white/10 bg-white/5 text-muted-foreground",
                )}
              >
                {gate.status}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </Tile>
  );
}

/* ------------------------------------------------------------------ *
 * 3. Run vitals strip
 * ------------------------------------------------------------------ */

/**
 * Four fixed properties of the pipeline, not usage statistics.
 *
 * Each is checkable against the architecture: five staged phases, twelve
 * defined roles, a hard redesign ceiling of three (`CLAUDE.md` §18), and one
 * pull request as the output of the GitHub stage (§20).
 */
const VITALS = [
  { value: "5", label: "Stages" },
  { value: "12", label: "Roles" },
  { value: "3", label: "Max retries" },
  { value: "1", label: "Pull request" },
] as const;

function VitalsTile() {
  return (
    <Tile className="shrink-0 p-2.5">
      <dl className="grid grid-cols-4 gap-2">
        {VITALS.map((vital) => (
          <div
            key={vital.label}
            className="flex flex-col items-center justify-center gap-0.5 rounded-2xl border border-white/10 bg-background/40 px-1.5 py-2.5"
          >
            <dd className="font-display text-xl font-bold leading-none tracking-tight text-foreground tabular-nums">
              {vital.value}
            </dd>
            <dt className="text-center font-mono text-[9px] uppercase leading-tight tracking-[0.1em] text-muted-foreground">
              {vital.label}
            </dt>
          </div>
        ))}
      </dl>
    </Tile>
  );
}

/* ------------------------------------------------------------------ *
 * 4. Quality gate chart
 * ------------------------------------------------------------------ */

/**
 * Anti-slop findings across the fix loop of one illustrative run.
 *
 * Drawn by hand rather than with a charting library: four points do not
 * justify a runtime, and the rest of this project charts the same way.
 */
const PASSES = [
  { label: "Draft", findings: 18 },
  { label: "Pass 1", findings: 9 },
  { label: "Pass 2", findings: 3 },
  { label: "Pass 3", findings: 0 },
] as const;

const VIEW = { w: 300, h: 110, pad: 8 } as const;
const PEAK = 18;

/** Maps a pass index and finding count onto the fixed viewBox. */
function point(i: number, findings: number) {
  const x = (i / (PASSES.length - 1)) * VIEW.w;
  const y = VIEW.pad + (1 - findings / PEAK) * (VIEW.h - VIEW.pad * 2);
  return { x, y };
}

const LINE = PASSES.map((p, i) => {
  const { x, y } = point(i, p.findings);
  return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
}).join(" ");

const AREA = `${LINE} L${VIEW.w} ${VIEW.h} L0 ${VIEW.h} Z`;

const GRIDLINES = [0, 1, 2, 3].map((i) => point(0, (PEAK / 3) * i).y);

function QualityTile() {
  return (
    <Tile className="flex flex-1 flex-col gap-4 p-5 sm:p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Eyebrow>Anti-slop gate</Eyebrow>
          <p className="mt-2 font-display text-3xl font-bold leading-none tracking-tight text-foreground tabular-nums sm:text-4xl">
            0
          </p>
          <p className="mt-1.5 text-xs text-foreground/70">findings left open</p>
        </div>

        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-ember/50 bg-ember/10 px-2.5 py-0.5 font-mono text-[10px] text-ember">
          <TrendingDown className="h-3 w-3" aria-hidden="true" />
          18 fixed
        </span>
      </div>

      {/* `preserveAspectRatio="none"` lets the plot fill whatever height the
          tile ends up with next to the gates list; the stroke keeps its real
          width because of `vectorEffect`. */}
      <figure className="flex flex-1 flex-col gap-2">
        <svg
          viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
          preserveAspectRatio="none"
          className="h-full min-h-[110px] w-full flex-1"
          role="img"
          aria-label={`Anti-slop findings across one run: ${PASSES.map(
            (p) => `${p.label} ${p.findings}`,
          ).join(", ")}.`}
        >
          <defs>
            <linearGradient id="run-bento-fill" x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="0%"
                stopColor="hsl(var(--ember))"
                stopOpacity="0.28"
              />
              <stop offset="100%" stopColor="hsl(var(--ember))" stopOpacity="0" />
            </linearGradient>
          </defs>

          {GRIDLINES.map((y) => (
            <line
              key={y}
              x1="0"
              x2={VIEW.w}
              y1={y}
              y2={y}
              stroke="currentColor"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
              className="text-white/[0.07]"
            />
          ))}

          <path d={AREA} fill="url(#run-bento-fill)" />
          <path
            d={LINE}
            fill="none"
            stroke="hsl(var(--ember))"
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        <figcaption className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.1em]">
          {PASSES.map((p, i) => (
            <span
              key={p.label}
              className={
                i === PASSES.length - 1
                  ? "text-foreground"
                  : "text-muted-foreground"
              }
            >
              {p.label}
            </span>
          ))}
        </figcaption>
      </figure>
    </Tile>
  );
}

/* ------------------------------------------------------------------ *
 * 5. Action rail
 * ------------------------------------------------------------------ */

/**
 * Real destinations only. Every href below resolves today — two in-page
 * anchors and three surfaces that exist in `App.tsx`. `/pricing` is
 * deliberately absent: it is not built (`CLAUDE.md` §28) and would fall
 * through to this very page.
 */
const ACTIONS = [
  { label: "The agents", href: "#agents" },
  { label: "Builder", href: "/chat" },
  { label: "Features", href: "/features" },
  { label: "Dashboard", href: "/dashboard" },
  { label: "Integrations", href: "#mcp" },
] as const;

function ActionRail() {
  return (
    <Tile className="shrink-0 px-5 py-4 sm:px-8">
      <nav aria-label="Where to go next">
        {/* Justified only once all five fit on one line. Below that the rail
            wraps, and `justify-between` would stretch a two-item row across
            the full width with a hole in the middle. */}
        <ul className="flex flex-wrap items-center justify-start gap-x-6 gap-y-3 lg:justify-between">
          {ACTIONS.map((action) => (
            <li key={action.href}>
              <a
                href={action.href}
                className="group/link inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-foreground transition-colors hover:text-ember sm:text-xs"
              >
                {action.label}
                <ArrowUpRight
                  className="h-3.5 w-3.5 text-muted-foreground transition-all duration-200 group-hover/link:-translate-y-0.5 group-hover/link:translate-x-0.5 group-hover/link:text-ember"
                  aria-hidden="true"
                />
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </Tile>
  );
}

/* ------------------------------------------------------------------ *
 * Section
 * ------------------------------------------------------------------ */

export function RunBentoSection() {
  return (
    <section
      id="demo"
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(8,7,6,0.9) 0%, rgba(8,7,6,0.78) 30%, rgba(8,7,6,0.92) 78%, rgba(8,7,6,0.98) 100%), url(${phaseTwoBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 45%",
      }}
    >
      <div className="mx-auto max-w-6xl">
        <div className="max-w-2xl">
          <Eyebrow>One run, end to end</Eyebrow>
          <AnimatedText
            as="h2"
            text="See what a single prompt hands back."
            className="mt-4 font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl"
          />
          <AnimatedText
            as="p"
            text="A request enters the harness, wakes only the agents it needs, stops at every gate you own, and leaves through a quality check it has to pass. Here is that run, laid out."
            delay={0.2}
            stagger={0.014}
            className="mt-5 max-w-md text-base leading-relaxed text-foreground/80"
          />
        </div>

        <div className="mt-12 grid grid-cols-1 gap-4 lg:grid-cols-12 lg:items-stretch">
          {/* Left: who woke up */}
          <div className="lg:col-span-4">
            <Tile className="flex h-full flex-col gap-6 p-5 sm:p-6 md:flex-row md:items-center md:gap-8 lg:flex-col lg:items-stretch lg:justify-between">
              <div className="flex-1 space-y-2">
                <h3 className="font-display text-xl font-bold tracking-tight text-foreground">
                  Twelve agents. Only the ones your page needs.
                </h3>
                <p className="text-sm leading-relaxed text-foreground/70">
                  A role is activated because its specialisation is relevant to
                  the stage in front of it — never because it exists. A landing
                  page may wake four. A dashboard wakes nine.
                </p>
              </div>

              <div className="shrink-0 md:w-[260px] lg:w-full">
                <AgentLattice />
              </div>

              <Button asChild size="sm" className="w-full md:w-auto lg:w-full">
                <a href="/chat">Start a build</a>
              </Button>
            </Tile>
          </div>

          {/* Right: what the run does, and what it leaves behind */}
          <div className="flex flex-col gap-4 lg:col-span-8">
            <div className="grid flex-1 grid-cols-1 gap-4 md:grid-cols-2">
              <GatesTile />
              <div className="flex flex-col gap-4">
                <VitalsTile />
                <QualityTile />
              </div>
            </div>

            <ActionRail />
          </div>
        </div>

        <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.18em] text-foreground/40">
          Gate states and finding counts show one illustrative run
        </p>
      </div>
    </section>
  );
}
