/**
 * Product Page (`/features`) — what DETROIT does, and why it matters.
 *
 * Why this file exists:
 * The Landing Page sells the outcome; this page explains the mechanism. It is
 * reached from inside the workspace, next to the Dashboard.
 *
 * Layer: frontend presentation (route page component).
 *
 * ---------------------------------------------------------------------------
 * A SEPARATE SURFACE, NOT A SECOND LANDING PAGE
 *
 * This page deliberately imports NOTHING from the Landing Page — no `SiteNav`,
 * no `hero-bg.jpg`, none of its sections. An earlier revision did import both,
 * and the result read as the Landing Page wearing different copy: same sticky
 * nav, same mountain photograph, same hero rhythm.
 *
 * It follows `pages/dashboard.tsx` instead: its own slim top bar, its own
 * content, reachable from the workspace rail. The Landing Page is approved and
 * is left exactly as it is (`docs/frontend.md` §3).
 *
 * Scope: static. No network calls, no API client. The CTAs are plain anchors
 * into `/chat`. Everything visual comes from the tokens in `index.css`
 * (`docs/design.md` §2) — warm near-black ground, cream ink, one ember accent,
 * Syne display, IBM Plex Sans body, Space Mono labels.
 *
 * The workflow section is `components/ui/ink-orbit-features.tsx`. That
 * component stays generic — its defaults still describe a nameless SaaS — and
 * every word of DETROIT's copy is passed in from here as props, so the
 * component can be reused without carrying our marketing with it.
 * ---------------------------------------------------------------------------
 */

import { ArrowUpRight, LayoutDashboard } from "lucide-react";

import featuresHeroBg from "@/assets/features-hero-bg.jpg";
import featuresTraceBg from "@/assets/features-trace-bg.jpg";
import featuresWorkflowBg from "@/assets/features-workflow-bg.jpg";
import { PricingTiers } from "@/components/pricing-tiers";
import ShutterGlyphFooter from "@/components/ui/shutter-glyph-footer";
import { AgentTrace, type TraceSpan } from "@/components/ui/agent-trace";
import { Button } from "@/components/ui/button";
import ContributionSkyline from "@/components/ui/contribution-skyline";
import InkOrbitFeatures from "@/components/ui/ink-orbit-features";

/* The pipeline's own vocabulary, not generic SaaS copy. Each block maps to a
   real stage in `docs/workflow.md` §1, so the page describes the product that
   exists rather than one we would like to have. */
const WORKFLOW_COPY = {
  tag: "The pipeline",
  title: "Built by a pipeline,\n*not a single prompt*",
  brand: "DETROIT-AI",
  collaboration: {
    title: "Human review at every gate",
    description:
      "Planning, design and final validation each stop for your approval. Approve it, pick a generated option, or write your own instruction — your words outrank the agent's.",
  },
  reports: {
    title: "Production-grade pages",
    description:
      "Typed components on a frozen build template. The model writes the source; the toolchain that compiles it is not model-writable.",
  },
  integrations: {
    title: "Wired in over MCP",
    description:
      "The pipeline acts inside the tools you already use. Capabilities are discovered from the server at run time, never assumed.",
    tools: ["GitHub", "Claude", "Cursor", "Lovable"],
  },
  insights: {
    title: "An anti-slop quality gate",
    description:
      "Every build clears Impeccable before it ships. A blocked gate stops the run — it is never quietly reported as a pass.",
  },
};

/** The twelve documented stages, as a readable rhythm rather than a list. */
const STAGES = [
  "Requirement analysis",
  "Planning",
  "Design",
  "Reference analysis",
  "Frontend architecture",
  "Build",
  "Preview",
  "Evaluation",
  "Targeted fix",
  "Re-evaluation",
  "Final validation",
  "External action",
];

/**
 * An illustrative run, shaped after a real one.
 *
 * Stage names are the `WorkflowStage` values from `docs/api.md` §3.1 and the
 * reviewers are the four `AgentRole`s the Evaluation stage fans out to
 * (`docs/workflow.md` §1) — so the trace shows the pipeline that exists, not a
 * generic agent demo. Durations follow the proportions of an actual stubbed
 * end-to-end run: build dominates, the four reviewers overlap, no step fails.
 * `reference_analysis` is absent because it is conditional and no image was
 * attached.
 */
const RUN_TOTAL = 17_400;

const RUN: TraceSpan[] = [
  { id: "req", label: "requirement_analysis", kind: "model", start: 0, end: 900, tokens: 1180, detail: "sparse input" },
  { id: "plan", label: "planning", kind: "model", start: 940, end: 2200, tokens: 2040, detail: "4 sections" },
  { id: "plan-gate", label: "hitl.plan_gate", kind: "io", start: 2240, end: 2600, detail: "approved" },
  { id: "design", label: "design", kind: "model", start: 2640, end: 4100, tokens: 2360, detail: "1 accent" },
  { id: "design-gate", label: "hitl.design_gate", kind: "io", start: 4140, end: 4500, detail: "approved" },
  { id: "arch", label: "frontend_architecture", kind: "model", start: 4540, end: 5800, tokens: 1720, detail: "1 page" },
  { id: "build", label: "build", kind: "tool", start: 5840, end: 11_200, detail: "3 files" },
  { id: "preview", label: "preview", kind: "io", start: 11_240, end: 11_800, detail: "dist ready" },
  { id: "eval", label: "evaluation", kind: "agent", start: 11_840, end: 16_800, tokens: 6240, detail: "gate passed" },
  { id: "imp", label: "impeccable.detect", kind: "tool", parentId: "eval", start: 11_900, end: 12_600, detail: "0 findings" },
  { id: "visual", label: "visual_ux_critic", kind: "model", parentId: "eval", start: 12_650, end: 15_200, tokens: 1890 },
  { id: "slop", label: "anti_slop_reviewer", kind: "model", parentId: "eval", start: 12_650, end: 15_400, tokens: 1760 },
  { id: "a11y", label: "accessibility_reviewer", kind: "model", parentId: "eval", start: 12_650, end: 14_900, tokens: 1310 },
  { id: "code", label: "code_reviewer", kind: "model", parentId: "eval", start: 12_650, end: 15_100, tokens: 1280 },
  { id: "final", label: "final_validation", kind: "io", start: 16_840, end: 17_400, detail: "approved" },
];

export function FeaturesPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* ---------- Own top bar. Not SiteNav. ---------- */}
      <header className="sticky top-0 z-50 border-b border-border bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-6 sm:px-10 lg:px-14">
          <a href="/" className="shrink-0 font-display text-base font-bold tracking-tight">
            DETROIT<span className="text-ember">-AI</span>
          </a>
          <span
            aria-hidden="true"
            className="hidden h-4 w-px shrink-0 bg-border sm:block"
          />
          <p className="hidden min-w-0 flex-1 truncate font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground sm:block">
            Product
          </p>

          <a
            href="/dashboard"
            className="ml-auto flex shrink-0 items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground sm:ml-0"
          >
            <LayoutDashboard size={14} />
            <span className="hidden sm:inline">Dashboard</span>
          </a>

          <Button asChild size="sm" className="shrink-0">
            <a href="/chat">Open builder</a>
          </Button>
        </div>
      </header>

      {/* ---------- Hero ---------- */}
      <section className="relative isolate overflow-hidden border-b border-border px-6 py-20 sm:px-10 lg:px-14 lg:py-28">
        {/* Photographic ground, under a two-axis scrim — the section motif in
            `docs/design.md` §2.

            Mirrored on purpose. In the source the accretion disk burns
            left-of-centre, which is exactly where the headline sits; flipped,
            the glow falls on the right behind the chart and the copy keeps a
            dark ground. The horizontal scrim then holds the left end down
            further, because cream text over that highlight is unreadable.

            The source is 736×414, so it upscales roughly 2× at desktop. It is
            soft rather than sharp — acceptable for an out-of-focus field, and
            the reason it is dimmed rather than shown at full strength. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10 bg-cover bg-center"
          style={{ backgroundImage: `url(${featuresHeroBg})`, transform: "scaleX(-1)" }}
        />
        {/* Two scrims MULTIPLY, they do not average — a first pass at 0.42 and
            0.6 left the glow transmitting at ~16% and the photograph was
            effectively invisible. These are tuned against the product: ~65%
            transmission through the glow (0.8 × 0.85), ~5% over the headline
            (0.06 × 0.85), and near-zero at both edges so the section blends
            into the header above and the next section below. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10"
          style={{
            background: [
              // Left-weighted: protects the headline and body copy.
              "linear-gradient(90deg, rgba(17,15,14,0.97) 0%, rgba(17,15,14,0.88) 30%, rgba(17,15,14,0.30) 58%, rgba(17,15,14,0.20) 78%, rgba(17,15,14,0.42) 100%)",
              // Vertical: blends into the sticky header above and the next
              // section below, so neither edge shows a seam.
              "linear-gradient(180deg, rgba(17,15,14,0.80) 0%, rgba(17,15,14,0.12) 30%, rgba(17,15,14,0.20) 65%, rgba(17,15,14,0.97) 100%)",
            ].join(","),
          }}
        />

        <div className="mx-auto max-w-6xl">
          {/* Two columns from `lg`: the pitch, and a year of shipped builds in
              the space that was otherwise empty. Below `lg` they stack and the
              chart sits under the CTAs. */}
          <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-[1fr_1fr] lg:gap-14">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.18em] text-ember">
                What it does
              </p>
              {/* No hard <br />: beside the chart the column is ~420px, and a
                  forced break there produced four ragged lines. `text-balance`
                  wraps it evenly at whatever width the column ends up. */}
              <h1 className="mt-5 text-balance font-display text-[2.25rem] font-bold leading-[1.06] tracking-tightest sm:text-5xl lg:text-[2.75rem] xl:text-[3.25rem]">
                You stay in the loop. The pipeline does the rest.
              </h1>
              <p className="mt-6 max-w-xl text-balance text-base leading-relaxed text-muted-foreground sm:text-lg">
                DETROIT plans, designs, builds and reviews a page across twelve
                stages — and stops for your approval at every decision that
                matters.
              </p>

              <div className="mt-10 flex flex-wrap items-center gap-4">
                <Button asChild size="lg" className="group">
                  <a href="/chat">
                    Open the builder
                    <ArrowUpRight
                      size={18}
                      className="transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                    />
                  </a>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <a href="/dashboard">See your usage</a>
                </Button>
              </div>
            </div>

            {/* A year of shipped builds. `palette="ember"` is the component's
                own preset, so the skyline reads in the brand accent rather than
                GitHub green. Stats are off: in a half-width column the chart
                would otherwise push the hero far taller than the copy. */}
            <div className="min-w-0">
              <ContributionSkyline
                palette="ember"
                unit="build"
                defaultView="3d"
                heightScale={0.85}
                showStats={false}
                title={
                  <>
                    <span className="font-semibold">Pages shipped</span>
                    <span className="text-muted-foreground"> · last 12 months</span>
                  </>
                }
                footer="Illustrative data · drag to orbit"
              />
            </div>
          </div>

          {/* The twelve stages, stated plainly. */}
          <ul className="mt-14 flex flex-wrap gap-x-5 gap-y-2.5">
            {STAGES.map((s, i) => (
              <li
                key={s}
                className="flex items-baseline gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground"
              >
                <span className="text-ember/70 tabular-nums">
                  {String(i + 1).padStart(2, "0")}
                </span>
                {s}
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ---------- Workflow ---------- */}
      <section
        aria-label="How the pipeline works"
        className="relative isolate overflow-hidden"
      >
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10 bg-cover bg-center"
          style={{ backgroundImage: `url(${featuresWorkflowBg})` }}
        />
        {/* Mostly even, heavier at both ends so the section blends into the
            hero above and the trace below. Scrims multiply, so this single
            layer is tuned to leave roughly 45% of the photograph visible
            through the middle band. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10"
          style={{
            background:
              "linear-gradient(180deg, rgba(17,15,14,0.97) 0%, rgba(17,15,14,0.62) 22%, rgba(17,15,14,0.55) 60%, rgba(17,15,14,0.88) 86%, rgba(17,15,14,0.99) 100%)",
          }}
        />

        <InkOrbitFeatures
          theme="dark"
          /* The component paints its own opaque ground: `--ib-page` and
             `--ib-paper` are both `hsl(var(--background))`, which would hide
             the photograph entirely. Overriding them inline beats the class
             rule inside the component, so the page shows through and the
             section panel becomes a translucent pane instead of a solid slab.
             The bento cards keep `--ib-card`, so they stay opaque and legible
             on top. */
          style={
            {
              "--ib-page": "transparent",
              "--ib-paper": "hsl(var(--background) / 0.42)",
            } as React.CSSProperties
          }
          brand={WORKFLOW_COPY.brand}
          tag={WORKFLOW_COPY.tag}
          title={WORKFLOW_COPY.title}
          collaboration={WORKFLOW_COPY.collaboration}
          reports={WORKFLOW_COPY.reports}
          integrations={WORKFLOW_COPY.integrations}
          insights={WORKFLOW_COPY.insights}
        />
      </section>

      {/* ---------- Trace ---------- */}
      <section
        aria-label="What a run looks like"
        className="relative isolate overflow-hidden border-t border-border px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      >
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10 bg-cover bg-center"
          style={{ backgroundImage: `url(${featuresTraceBg})` }}
        />
        {/* Tuned for a BRIGHT source — sun flare and a white cloud field,
            the opposite of the near-black image this section carried before.
            The earlier light scrim (0.35 through the middle) would have left
            cream text sitting on near-white cloud.

            The crop helps: `bg-cover` on a tall section shows the full height,
            and this image's top band is dark storm cloud, which is exactly
            where the heading lands. Transmission is ~8% at the very top rising
            to ~38% behind the heading, ~30% across the bright middle (mostly
            hidden behind the opaque trace card anyway), then clamped down
            again so the caption at the foot stays readable and the section
            blends into pricing below. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 -z-10"
          style={{
            background:
              "linear-gradient(180deg, rgba(17,15,14,0.92) 0%, rgba(17,15,14,0.62) 18%, rgba(17,15,14,0.70) 55%, rgba(17,15,14,0.93) 85%, rgba(17,15,14,0.99) 100%)",
          }}
        />

        <div className="mx-auto max-w-6xl">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-ember">
            Observability
          </p>
          <h2 className="mt-5 max-w-2xl font-display text-3xl font-bold leading-[1.1] tracking-tight sm:text-4xl">
            See exactly what the pipeline did.
          </h2>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-muted-foreground">
            Every run is a trace: which agent ran, for how long, what it cost,
            and where it waited for you. Drag the timeline to replay it.
          </p>

          <div className="mt-10">
            <AgentTrace
              spans={RUN}
              duration={RUN_TOTAL}
              runId="run_9d2be03"
              model="claude-opus-5"
              defaultTime={RUN_TOTAL * 0.62}
              autoPlay={false}
              loop
              labelWidth={196}
            />
          </div>

          <p className="mt-4 font-mono text-[10px] uppercase leading-relaxed tracking-[0.14em] text-muted-foreground/70">
            Illustrative run · stage names are the real WorkflowStage values
          </p>
        </div>
      </section>

      {/* ---------- Pricing ---------- */}
      <PricingTiers />

      {/* ---------- Footer ----------
          The component ships its own scoped stylesheet and its own SVG
          alphabet, so it is themed through props rather than edited.

          Every colour below is a reference to the token in `index.css`, not a
          hex copied out of it — the previous pass hardcoded `#110f0e` and
          `#f5f2ef`, which is the same thing right up until a token moves. The
          component feeds these straight into CSS custom properties, so any
          valid CSS colour works.

          What this fixes (`docs/design.md` §2): the footer had no accent at
          all. It rendered as a flat cream-on-black slab with the ember hue —
          the one thing carrying emphasis across every other surface —
          missing, and the full-bleed wordmark at body-text contrast was the
          brightest element on the site. Now ember marks the shutter, the
          submit arrow, link hover and focus rings, the wordmark drops to
          `--muted-foreground` — the token that already means "de-emphasised"
          here, and warm rather than the neutral grey a plain dimming gives —
          and the mono is Space Mono like the rest of the project rather than
          the component's own JetBrains-first stack.

          The shutter sits on the "R" of DETROIT. */}
      <ShutterGlyphFooter
        brand="DETROIT"
        company="DETROIT-AI · Prompt to live page"
        since={2025}
        shutterAt={3}
        className="border-t border-border"
        background="hsl(var(--background))"
        backgroundTop="hsl(var(--card))"
        ink="hsl(var(--foreground))"
        accent="hsl(var(--ember))"
        markInk="hsl(var(--muted-foreground))"
        fontMono="'Space Mono', monospace"
        signupLabel="Build notes, pipeline changes and release news"
        placeholder="Email address"
        socials={[
          { label: "Product", href: "/features" },
          { label: "Builder", href: "/chat" },
          { label: "Dashboard", href: "/dashboard" },
        ]}
        legal={[
          { label: "Pricing", href: "/features#pricing" },
          { label: "Sign in", href: "/login" },
        ]}
      />
    </div>
  );
}
