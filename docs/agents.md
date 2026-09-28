<!-- Purpose: Define the finalized v1 agent roles and their full contracts — purpose, responsibilities, allowed context, allowed tools, prohibited actions and output schema. -->

# Agents

## 1. Philosophy

Agents are specialized **roles**, not permanently running processes. Use the minimum
set required for the current stage. Do not activate an agent because it exists, and do
not create agents to increase the agent count.

All roles run on the same provider (Anthropic Claude, `docs/runtime.md` §4) with
different role instructions, scoped context and tool permissions.

## 2. Finalized v1 Roster

| # | Role | `AgentRole` | Stage | v1 |
|---|---|---|---|---|
| 1 | Requirement Analyzer | `requirement_analyzer` | Requirement Analysis | ✅ |
| 2 | Planner | `planner` | Planning | ✅ |
| 3 | Design Director | `design_director` | Design | ✅ |
| 4 | Reference Analyzer | `reference_analyzer` | Reference Analysis | ✅ conditional |
| 5 | Frontend Architect | `frontend_architect` | Frontend Architecture | ✅ |
| 6 | Builder | `builder` | Build | ✅ |
| 7 | Visual & UX Critic | `visual_ux_critic` | Evaluation | ✅ |
| 8 | Anti-Slop Reviewer | `anti_slop_reviewer` | Evaluation | ✅ requires Impeccable |
| 9 | Accessibility Reviewer | `accessibility_reviewer` | Evaluation | ✅ |
| 10 | Code Reviewer | `code_reviewer` | Evaluation | ✅ |
| 11 | Fixer | `fixer` | Targeted Fix | ✅ |
| 12 | GitHub Operator | `github_operator` | External Action | ✅ gated |

**Deferred** (do not build until explicitly requested): Lovable Operator, Deployment
Operator.

### Change from earlier drafts

**Visual Critic and UX Reviewer are merged into Visual & UX Critic.** Both inspected
the rendered page for hierarchy, layout, navigation and usability against the same
criteria; keeping them separate produced two agents competing over one finding set.
Overlapping agents are explicitly discouraged (`skills.md` §9). Accessibility stays
separate because its criteria are objective and distinct.

## 3. Contract Template

Every role declares: **purpose · responsibilities · allowed context · allowed tools ·
prohibited actions · output schema**.

Rules applying to all roles:

- Context comes from the ContextBuilder allowlist (`docs/state.md` §6). No agent
  receives the whole state, the conversation transcript, or other agents' raw output.
- `approved_decisions` is always passed as **constraints**, never as an open question.
- Every agent returns structured output validated by Pydantic. A validation failure is
  a retryable agent failure, not a silent pass.
- Structured output must be a single Pydantic model: a bare list is not one. The four
  reviewers therefore share the `ReviewFindings` container rather than returning
  `EvaluationFinding[]` directly. They are distinguished by the `source` on each
  finding, not by four near-identical models.
- An agent reports only what it observed. Facts about whether a stage's tooling ran
  belong to the layer that ran it, never to the model — see the Anti-Slop Reviewer.
- No agent may write to `approved_decisions` or `hitl_history`. Only the HITL resume
  path may.

---

## 4. Agent Contracts

### 4.1 Requirement Analyzer

- **Purpose** — turn a raw prompt into structured requirements and surface what is
  missing or contradictory, before any planning happens.
- **Responsibilities** — normalize the request into typed fields; preserve every
  explicit user decision verbatim; detect conflicts; detect gaps that genuinely block
  planning; classify the request as sparse or detailed to drive adaptive HITL.
- **Allowed context** — `user_request`, project constraints, `approved_decisions`.
- **Allowed tools** — none (pure reasoning over provided context).
- **Prohibited** — inventing requirements the user did not state; resolving conflicts
  on its own; designing; planning; asking questions the user already answered.
- **Output** — `NormalizedRequirements { goal, audience, surfaces[], sections[],
  explicit_decisions[], interactions[], responsive_requirements[], constraints[],
  acceptance_criteria[], conflicts[], gaps[], input_density: "sparse"|"detailed" }`

### 4.2 Planner

- **Purpose** — produce the implementation plan that later stages consume without
  reinterpreting the original prompt.
- **Responsibilities** — objective, audience, page/section structure, content
  hierarchy, CTA strategy, interaction and responsive requirements, design intent,
  technical constraints, acceptance criteria.
- **Allowed context** — `normalized_requirements`, `requirement_conflicts`,
  `requirement_gaps`, `approved_decisions`.
- **Allowed tools** — none.
- **Prohibited** — **writing or modifying code**; making design-system decisions that
  belong to the Design Director; re-opening approved decisions; expanding scope beyond
  the requested surfaces.
- **Output** — `Plan { objective, audience, pages[], sections[], content_hierarchy,
  cta_strategy, interactions[], responsive_requirements[], design_intent,
  technical_constraints[], acceptance_criteria[], open_questions[] }`

### 4.3 Design Director

- **Purpose** — translate the approved plan into one coherent design system.
- **Responsibilities** — typography scale and weights, color system, spacing rhythm,
  layout grid, density, radius, button/card/container language, image treatment,
  motion principles, visual tone. Preserve the approved Landing Page language for
  in-project surfaces (`docs/design.md` §2).
- **Allowed context** — approved `plan`, design constraints,
  `approved_reference_direction`, `approved_decisions`.
- **Allowed tools** — none.
- **Prohibited** — replacing a user-specified font, palette or visual direction;
  inventing a second design system; producing disconnected styling choices; writing
  code.
- **Output** — `DesignSpec { font_families, typography_scale, weights, color_system,
  spacing_rules, layout_grid, density, radius, button_language, card_language,
  image_treatment, motion_principles, visual_tone, rationale }`

### 4.4 Reference Analyzer

- **Purpose** — extract design *intent* from a reference image and flag where it
  conflicts with the approved system.
- **Responsibilities** — composition, layout, hierarchy, typography characteristics,
  color relationships, spacing, card/container treatment, navigation, hero structure,
  decorative language, interaction cues, inferable responsive implications; and an
  explicit conflict list against the current `design_spec`.
- **Allowed context** — the reference image, `design_spec` (for conflict detection),
  `approved_decisions`.
- **Allowed tools** — image read (the stored reference only).
- **Prohibited** — copying the reference literally; reproducing brand-specific or
  copyrighted assets; **silently adopting the reference palette/typography over the
  approved system**; inventing contents when the image is unreadable (report and stop
  instead, `skills.md` §16).
- **Output** — `ReferenceAnalysis { composition, layout, hierarchy, typography,
  color_relationships, spacing, container_treatment, navigation, hero_structure,
  decorative_language, interaction_cues, responsive_implications,
  conflicts_with_approved_system[], adoptable_without_conflict[] }`

### 4.5 Frontend Architect

- **Purpose** — define the page and component structure to be built.
- **Responsibilities** — page composition, component tree, reusable component
  identification, responsive structure, state boundaries, **reuse of existing
  project components before proposing new ones**.
- **Allowed context** — approved `plan`, approved `design_spec`, the existing
  component inventory.
- **Allowed tools** — repository read (frontend source, read-only).
- **Prohibited** — writing implementation code; unnecessary abstraction; proposing new
  dependencies; restructuring approved existing pages beyond a documented scoped
  technical change (`docs/frontend.md` §3).
- **Output** — `FrontendArchitecture { pages[], component_tree, reused_components[],
  new_components[], responsive_strategy, file_plan[] }`

### 4.6 Builder

- **Purpose** — implement the approved architecture and design.
- **Responsibilities** — create/modify only the planned files; follow the approved
  design tokens and component rules; keep changes minimal and targeted; add the
  required file-level comment explaining architectural purpose (`skills.md` §4).
- **Allowed context** — approved `plan`, `design_spec`, `component_tree`, **only the
  affected files**, acceptance criteria.
- **Allowed tools** — workspace file read/write (inside the project workspace only),
  the allowlisted build runner.
- **Prohibited** — touching files outside the plan; rewriting unrelated code;
  regenerating the whole project for a local change; adding or changing dependencies;
  writing outside `workspace/src/` and the asset allowlist; modifying builder-owned
  template files; generating a backend for the produced website; **creating more than
  one file per approved step when the one-file gate is active**.
- **Output** — `BuildResult { files_written[], files_modified[], notes[],
  deviations_from_plan[] }`

### 4.7 Visual & UX Critic

- **Purpose** — inspect the *rendered* page for visual and usability defects.
- **Responsibilities** — visual hierarchy, layout, spacing consistency, alignment,
  responsive behavior, information architecture, navigation clarity, interaction flow,
  cognitive load, consistency with the approved design spec.
- **Allowed context** — rendered preview, `design_spec`, acceptance criteria.
- **Allowed tools** — rendered-preview inspection (read-only).
- **Prohibited** — writing or modifying code; redesigning; judging from source alone
  (`skills.md` §18); flagging an approved decision as a defect without marking it as a
  conflict.
- **Output** — `ReviewFindings { findings[] }` (finding schema in `docs/api.md` §4)

### 4.8 Anti-Slop Reviewer

- **Purpose** — interpret Impeccable's anti-slop findings and produce concrete
  corrective instructions.
- **Requires Impeccable.** Impeccable is a **mandatory, blocking** evaluator
  (`docs/design.md` §5). This agent does not replace it.
- **Responsibilities** — consume Impeccable's findings against the rendered page;
  interpret them against the checklist in `docs/design.md` §5.4 and against approved
  state; give specific, actionable fixes rather than "make it better"; report only
  what was observed, leaving the detector's availability and version to the layer
  that ran it.
- **Allowed context** — rendered preview, Impeccable findings, `design_spec`,
  `approved_decisions`.
- **Allowed tools** — Impeccable; rendered-preview inspection.
- **Prohibited** — **running as a substitute for Impeccable when it is unavailable**
  (the correct behavior is to report the gate `blocked` and stop); **reporting a
  blocked or skipped check as completed or passed**; treating a detector finding as
  proof; treating a clean run as proof of quality; **"fixing" an intentional approved
  decision** — such a finding must be marked `conflicts_with_approved_decision: true`
  and routed to the user, not the Fixer; pursuing novelty for its own sake.
- **Output** — `ReviewFindings { findings[] }`, and nothing else.

  This role does **not** report `impeccable_available`, `impeccable_version` or
  `gate_status`. Whether the detector ran is a fact the runtime observes, not a claim
  an agent makes — a model able to set `impeccable_available: true` could forge the
  very gate it is being checked by, and `CLAUDE.md` §15's "never report the check as
  completed when it did not run" would then rest on the model's honesty rather than
  on code.

  The evaluation layer probes Impeccable, records those three fields on
  `EvaluationReport`, and enforces the invariant that an unavailable detector means
  `gate_status: "blocked"` (`docs/design.md` §5.3).

### 4.9 Accessibility Reviewer

- **Purpose** — verify basic accessibility and semantic quality.
- **Responsibilities** — semantic HTML, heading order, landmarks, keyboard
  operability and focus visibility, contrast, alt text, ARIA correctness, reduced-motion
  support, touch target sizing.
- **Allowed context** — rendered preview, changed files.
- **Allowed tools** — rendered-preview inspection (read-only).
- **Prohibited** — writing code; restyling; opinions on visual taste (that is the
  Visual & UX Critic's job).
- **Output** — `ReviewFindings { findings[] }`, each finding `category: "a11y"`

### 4.10 Code Reviewer

- **Purpose** — review changed code for correctness and maintainability.
- **Responsibilities** — correctness, imports/references, type safety, dead code,
  unnecessary complexity, security concerns, adherence to project conventions.
- **Allowed context** — changed files only.
- **Allowed tools** — repository read (read-only).
- **Prohibited** — writing or modifying code; opinions on visual design; reviewing
  files outside the change set.
- **Output** — `ReviewFindings { findings[] }`, each finding `category: "code"`

### 4.11 Fixer

- **Purpose** — apply targeted corrections for specific findings.
- **Responsibilities** — fix exactly the named findings; smallest sufficient scope;
  preserve every unrelated approved decision; report anything it could not fix.
- **Allowed context** — the specific findings, the affected files,
  `approved_decisions`.
- **Allowed tools** — scoped workspace write (only files named in the findings), the
  allowlisted build runner.
- **Prohibited** — unrelated modifications; redesigning; full-project regeneration;
  acting on a finding marked `conflicts_with_approved_decision` (escalate to the user
  instead); exceeding the 3-iteration limit.
- **Output** — `FixResult { findings_addressed[], files_modified[], unresolved[],
  escalated_to_user[] }`

### 4.12 GitHub Operator *(gated)*

- **Purpose** — perform approved repository operations through MCP.
- **Responsibilities** — discover actual server capabilities before use; produce a
  non-mutating plan for approval; execute only the approved plan; record the outcome
  in `mcp_action_history`.
- **Allowed context** — the approved action plan, the generated file manifest.
- **Allowed tools** — GitHub MCP tools only, and only those the plan requires.
- **Prohibited** — any mutation without a valid `approval_token`; destructive git
  operations (force push, history rewrite, branch deletion) unless explicitly
  authorized; pushing unreviewed or failing code; **assuming a capability exists**
  without discovery; exposing credentials in output.
- **Output** — `ExternalActionResult` (schema in `docs/api.md` §4)

---

## 5. Parallelization

Allowed only for genuinely independent work:

```text
Rendered page
├── Visual & UX Critic
├── Anti-Slop Reviewer
├── Accessibility Reviewer
└── Code Reviewer
```

Never parallelize dependent stages (Planning → Design → Build). Parallel execution
reduces wall-clock time but increases total token usage — use it because the work is
independent, not because it is available.

## 6. Failure Handling

An agent failure (provider error, schema validation failure, timeout) is recorded in
structured state with the role, stage and failure class. The harness decides whether
to retry, switch strategy or stop. Do not repeat a failing action indefinitely, and
do not silently downgrade the architecture to route around a failure.
