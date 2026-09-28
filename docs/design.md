<!-- Purpose: Keep new frontend pages visually consistent with the approved Landing Page, define how reference-image conflicts are resolved, and record the status of the anti-slop quality gate. -->

# Design

## 1. Primary Rule

**The existing Landing Page is the design source of truth for new pages.**

Before creating the Pricing Page or the Chatbot Page, inspect the Landing Page
implementation and follow its established visual language. For the Chatbot Page, also
inspect `Refrence_images/chatbot_refrence.png` as an *additional* reference — subject
to §3 below.

Do not introduce an unrelated design system. Preserve the project's typography,
colors, spacing, layout, component treatment, visual hierarchy and responsive
behavior. Change the established design language only when the user explicitly asks.

## 2. The Approved Design System

Extracted from `frontend/src/index.css` and `frontend/tailwind.config.ts`. These are
approved decisions and are constraints, not suggestions (`skills.md` §31).

### Color tokens (HSL custom properties, dark-only)

| Token | Value | Role |
|---|---|---|
| `--background` | `20 9% 6%` | warm near-black page base |
| `--foreground` | `32 24% 95%` | warm cream text |
| `--card` | `20 10% 9%` | raised surface |
| `--primary` / `--accent` / `--ember` / `--ring` | `14 88% 55%` | the single ember-orange accent |
| `--ember-dim` | `12 65% 32%` | accent shadow/gradient partner |
| `--secondary` | `20 10% 14%` | subdued surface |
| `--muted` | `20 9% 13%` | muted surface |
| `--muted-foreground` | `30 9% 62%` | secondary text |
| `--border` / `--input` | `25 10% 20%` | hairlines and field borders |
| `--radius` | `0.375rem` | base radius |

The page is **dark-only**: `color-scheme: dark`, `darkMode: ["class"]`, and no light
token set exists. Do not add a light theme without approval.

### Typography

| Family | Stack | Use |
|---|---|---|
| `font-display` | Syne 600/700/800 | headlines |
| `font-sans` | IBM Plex Sans 400/500/600 + italic | body |
| `font-mono` | Space Mono 400/700 | eyebrows, labels, status text |

Loaded via a Google Fonts `@import` at the top of `index.css`.

### Layout and rhythm

- Section padding: `px-6 sm:px-10 lg:px-14`, `py-20 lg:py-28`
- Inner width: `mx-auto max-w-6xl`
- Buttons/inputs use the small base radius; large surfaces use explicit
  `rounded-2xl` / `rounded-3xl`
- The `container` utility is configured and used by `site-nav`; section bodies use
  `max-w-6xl` + explicit padding. New pages follow the **section** convention.

### Signature motifs

- Every section sits on a photographic background under a four-stop vertical
  `linear-gradient` scrim
- Eyebrows/labels: `font-mono text-xs uppercase tracking-[0.18em]`
- Headlines animate in per character via `AnimatedText`; blocks use `animate-fade-up`
- Accent is used sparingly — one ember hue carries emphasis across the whole page

### Accessibility already established (must not regress)

- `:focus-visible` renders a 2px ember outline with 3px offset
- `prefers-reduced-motion` disables smooth scrolling
- The nav toggle carries `aria-expanded` and `aria-label`

## 3. Reference Image Conflict Procedure

The Chatbot reference (`Refrence_images/chatbot_refrence.png`) is part of the approved
project context, but it does **not** outrank the approved Landing Page.

```text
Reference image
  -> Reference Analyzer (structural + visual interpretation)
  -> conflict detection against the approved design system
  -> if a meaningful conflict exists: HITL with concrete options
  -> approved reference direction recorded in state
  -> build
```

**Never silently adopt the reference's palette, fonts or radius when they conflict
with the approved system.**

### Known conflict to raise at the Reference HITL gate

| Dimension | Reference | Approved system | Default resolution |
|---|---|---|---|
| Accent hue | indigo / violet | ember-orange `14 88% 55%` | approved system wins |
| Base surface | navy / blue-black | warm near-black `20 9% 6%` | approved system wins |
| Status color | green "connected" pill | no green token exists | raise at HITL — a semantic success color may need adding |
| Syntax highlighting | multi-hue on dark | no code-block treatment exists | raise at HITL — new sub-system, needs a decision |
| Data-viz colors | multi-series blue/orange | `dashboard-section` already ships a chart treatment | reuse the existing treatment |

What is adopted from the reference by default: **layout, structure, information
architecture, component inventory and interaction patterns** (see `docs/frontend.md` §5).
What is not adopted by default: **color, typography, radius and decorative language.**

A "meaningful conflict" is any difference that a user would notice as a change in
brand or visual system. Spacing or proportion differences that stay inside the
approved language are not conflicts and do not need a gate.

## 4. Pending Design Decisions (resolve at the Design HITL gate)

| # | Decision | Options |
|---|---|---|
| D-1 | `src/components/ui/pricing-card.tsx` treatment. It is staged but never rendered, so it is **not** part of the approved Landing Page and is not protected by the preserve rule. Its current styling (`backdrop-blur-xl`, `bg-white/[0.05]`, `border-white/15`, heavy drop-shadow) is glassmorphism, which the anti-slop rules flag. | (a) adapt it to the flatter approved language — `--card` surface, `--border` hairline, ember accents, base radius; (b) use as-is and accept the glass treatment as intentional; (c) set it aside and build the pricing card from existing primitives |
| D-2 | Pricing Page background. `pricing-bg.jpg` is named for Pricing but already used by `about-section`. | (a) reuse and accept the repeat; (b) use a different existing asset; (c) no photographic background for Pricing |
| D-3 | Pricing Page model story. Backend v1 is Claude-only; the approved Landing Page markets five models. | (a) model-agnostic pricing copy; (b) mirror the Landing Page's five-model story; (c) state Claude explicitly |
| D-4 | Chatbot Page model attribution. If the workspace shows per-agent/per-model attribution the way `dashboard-section` does with mock data, it would display model names the backend will never invoke. | (a) show agent roles, not model names; (b) show the real provider only; (c) mirror the Landing Page's mock presentation and mark it clearly as illustrative |

None of these may be decided silently during implementation.

## 5. Impeccable & the Anti-Slop Gate

### 5.1 Ruling: Impeccable is MANDATORY

Impeccable anti-AI-slop evaluation is a **required, blocking quality gate** in the
generation → evaluation → fix → re-evaluation workflow. It is **not optional**, and
there is **no fallback substitute**.

| | |
|---|---|
| **Is the anti-slop gate optional?** | **No.** |
| **Is Impeccable optional?** | **No.** It is the required evaluator. |
| **If Impeccable is unavailable** | **STOP.** Report the quality gate as **blocked**. Do not continue to Final Validation, do not mark evaluation complete, and do not report the check as done. |
| **Is an agent-only review an acceptable substitute?** | **No.** An agent review may add interpretation *on top of* Impeccable findings. It may never stand in for them. |
| **May a blocked gate be reported as a pass?** | **Never.** Reporting an uncompleted check as completed is a correctness failure, not a shortcut. |

### 5.2 Current availability: NOT INSTALLED

Impeccable is **not installed** in this repository (`.impeccable/` is absent, and no
`DESIGN.md` artifact exists).

**Consequence:** the evaluation stage is **blocked** until Impeccable is installed.
This is a real, recorded blocker (`docs/README.md`, `docs/workflow.md` §6) — not a
condition to work around. Installing Impeccable requires explicit user approval and
must happen before the Build → Evaluation path can complete.

### 5.3 Blocked-gate behavior (exact)

```text
Evaluation stage entered
  -> probe Impeccable availability
       available   -> run detector -> findings -> interpretation -> continue
       unavailable -> run_status = "blocked"
                      gate_status = "blocked"
                      emit error code: quality_gate_blocked
                      STOP; surface to the user; do NOT advance the workflow
```

Recording requirements on every `EvaluationReport`:

- `impeccable_available: bool` — when `false`, the report is **not** a valid
  evaluation result
- `impeccable_version: str?` — the actual installed version, never assumed
- `gate_status: "passed" | "failed" | "blocked"`

A report with `impeccable_available: false` may never carry `gate_status: "passed"`.

### 5.4 Anti-slop checklist (blocking criteria)

Derived from `CLAUDE.md` §15 and `skills.md` §17. Impeccable is the detector; this
checklist is what the Anti-Slop Reviewer interprets its findings against:

- unnecessary gradients
- excessive glassmorphism
- repetitive rounded cards
- arbitrary icon tiles / icon grids
- weak typography hierarchy
- excessive decorative blobs
- excessive glow / shadow effects
- generic SaaS template composition
- inconsistent spacing
- poor responsive behavior

Use the installed Impeccable version's actual rule set and commands. Do not assume a
fixed rule count or command set (`CLAUDE.md` §15).

### 5.5 How findings are treated

- A detector finding is **evidence, not proof**. A clean run is **not** proof of
  visual quality — it is a necessary condition, not a sufficient one.
- Always inspect the rendered result and a real interaction path; reading source is
  not validation (`skills.md` §18).
- **Never "fix" an intentional, approved design decision** because a detector flagged
  it. Check the finding against approved state first; if they conflict, mark it
  `conflicts_with_approved_decision: true` and surface it to the user instead of
  silently changing approved work.
- The goal is coherent, purposeful, human-directed design — not novelty for its own
  sake.
