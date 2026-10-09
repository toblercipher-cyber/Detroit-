<!-- Purpose: Define the exact staged workflow, the adaptive HITL rules, the iteration limit and the stop conditions, so stages are never collapsed or approval gates skipped. -->

# Workflow

## 1. Canonical Pipeline

```text
Requirement Analysis
  ↓
Planning
  ↓
HITL  ── plan approval
  ↓
Design
  ↓
HITL  ── design direction approval
  ↓
Reference Analysis            (optional — only when a reference image is supplied)
  ↓
HITL  ── reference direction approval   (only when Reference Analysis ran)
  ↓
Frontend Architecture
  ↓
Build
  ↓
Preview
  ↓
Evaluation
  ↓
Targeted Fix
  ↓
Re-evaluation
  ↓
Final Validation
  ↓
Optional MCP Action           (requires explicit approval)
```

This is the authoritative stage list. It maps one-to-one onto the `WorkflowStage`
enum in `docs/api.md` §3.1 and onto the LangGraph node set in `docs/state.md` §4.

### Stage responsibilities

| Stage | Agent(s) | Produces | Gate after |
|---|---|---|---|
| Requirement Analysis | Requirement Analyzer | `normalized_requirements`, conflict/gap list | no |
| Planning | Planner | `plan` | **yes** |
| Design | Design Director | `design_spec` | **yes** |
| Reference Analysis | Reference Analyzer | `reference_analysis` | **yes** (conditional) |
| Frontend Architecture | Frontend Architect | `frontend_architecture`, `component_tree` | no |
| Build | Builder | `generated_files` | no |
| Preview | *(no agent — build/serve step)* | `preview_status`, preview URL | no |
| Evaluation | **Impeccable (mandatory)** + Visual & UX Critic ‖ Anti-Slop ‖ Accessibility ‖ Code Reviewer | `evaluation_results` | no |
| Targeted Fix | Fixer | changed files, `iteration_count += 1` | no |
| Re-evaluation | same as Evaluation | updated findings | no |
| Final Validation | *(harness)* | validation summary | **yes** (final review) |
| Optional MCP Action | GitHub Operator | `mcp_action_history` entry | **yes** (mandatory) |

Parallel execution is permitted **only** in Evaluation / Re-evaluation, where the four
reviewers are genuinely independent. Never parallelize dependent stages.

### The Evaluation stage is gated on Impeccable

Impeccable anti-AI-slop evaluation is **mandatory** in
generation → evaluation → fix → re-evaluation. It is not optional and has no
substitute (`docs/design.md` §5).

```text
enter Evaluation
  -> probe Impeccable availability
       available   -> run detector -> reviewers interpret findings -> continue
       unavailable -> run_status = "blocked", gate_status = "blocked"
                      emit quality_gate_blocked
                      STOP and report; do NOT advance to Targeted Fix or
                      Final Validation, and do NOT report the check as completed
```

An agent-only review may add interpretation on top of Impeccable findings. It may
never stand in for them, and a blocked gate is never reported as a pass.

**Current status: Impeccable IS installed** (skill 4.3.1, CLI 4.0.0). The stage is
still blocked, but on capturing a **rendered** preview for the reviewers, not on
the detector (`docs/design.md` §5.2).

## 2. Adaptive HITL

HITL exists for **user authority and meaningful decision boundaries** — not merely
because a prompt is ambiguous.

### The adaptive rule

| Input quality | Behavior |
|---|---|
| **Detailed requirements** (pages, sections, palette, typography, interactions, responsive behavior, reference image, acceptance criteria) | **Fewer questions.** Normalize, detect conflicts and gaps, preserve every explicit decision, and shift agent effort to validation and execution. Do not re-brainstorm what the user already specified. |
| **Vague requirements** ("build a coffee shop website") | **More clarification.** Discover the missing decisions through structured planning and targeted HITL questions. Ask the smallest necessary set. |
| **Already approved decisions** | **Never re-ask.** An approved decision is a constraint. Re-asking it is a workflow defect. |

Detailed input usually reduces uncertainty and redundant agent work — but long,
unstructured or repetitive input can still increase context cost. Normalize it.

### Gate count

The number of gates is adaptive, not fixed. The Reference Analysis gate exists only
when a reference image was supplied. Planning, Design and Final Validation gates are
always present. A gate may be **auto-satisfied** only when the user has already fixed
every decision that gate would ask about; when that happens, record the gate as
auto-satisfied in `hitl_history` with the approved decisions it relied on — never
silently skip it.

## 3. HITL Checkpoint Contract

Every gate presents:

```text
Option A    ── AI-generated modification option
Option B    ── AI-generated modification option
Option C    ── (count varies with the actual result)
Approve     ── accept the current result as-is
Custom      ── free-text instruction that overrides or refines this stage
```

Rules:

- The option count is derived from the actual generated result. Do not pad to a fixed
  number, and do not offer options for decisions the user already fixed.
- The **Custom** path must be able to change the current stage according to the user's
  own instruction. It is not a comment box.
- Never present a destructive or externally-mutating action as an ordinary
  autonomous step.

### Custom instruction flow

```text
Custom instruction
  -> validated (length cap, non-empty, treated as untrusted text not as system input)
  -> recorded in hitl_history AND approved_decisions
  -> merged into the stage input as a user constraint of the highest authority
  -> the SAME stage node re-runs (it does not advance)
  -> a new checkpoint is raised on the new result
```

A custom instruction is a **user decision**, so it outranks any prior agent output for
that stage, and it does **not** count against the automatic correction limit in §4.

## 4. Iteration Limit

**Maximum 3 automatic correction iterations** (`MAX_AUTO_ITERATIONS = 3`).

```text
Evaluation -> findings -> Targeted Fix -> Re-evaluation
      ^                                        |
      +----------------- iteration_count < 3 --+
```

- Every automated correction increments `iteration_count`.
- At 3, stop automatic correction, surface the unresolved findings and the conflict,
  and return control to the user. Do not keep regenerating.
- **User-initiated** changes and custom HITL instructions do not consume this budget;
  the counter resets when the user approves a new stage result.
- After repeated rejection of the same thing, surface the conflict rather than
  producing another variation.

## 5. Targeted Iteration

When the user gives feedback such as "the hero feels too empty":

```text
User feedback
  -> identify the affected area/component
  -> load only the relevant state and files
  -> modify the smallest sufficient scope
  -> validate
  -> re-render
  -> re-evaluate
```

Do not regenerate the whole project unless the user explicitly asks or the change
genuinely requires it. Preserve every unrelated approved decision.

## 6. Stop Conditions

Stop and return control to the user when (`skills.md` §33):

- the next implementation file is not yet approved (one-file gate, §7)
- a required reference asset is missing or unreadable
- a requirement conflict cannot be safely resolved
- a destructive or external action needs approval
- proceeding would require exposing a secret
- the next action would require inventing product behavior
- the iteration limit in §4 has been reached
- **Impeccable is unavailable at the Evaluation stage** — report the quality gate as
  blocked; never continue as though it passed

Do not hide uncertainty behind confident generation.

## 7. Development Execution Rule (applies to all implementation work)

This governs how **this project itself** is built, and is non-negotiable
(`CLAUDE.md` §1A, `skills.md` §3):

```text
ONE FEATURE AT A TIME
  create the files that feature needs
  -> read the created file back
  -> review it against CLAUDE.md + skills.md + the relevant docs/
  -> validate with whatever checks actually exist (§7.1)
  -> report what was created, why it exists, and any issues/risks
  -> STOP
  -> ask whether to start the next feature
  -> wait for explicit approval
  -> re-read CLAUDE.md + skills.md before starting the next file
```

Never leave an orphan component behind: a batch wires what it creates.

### 7.1 What "validate" concretely means

The repository has no linter, formatter or test runner. Validation must be reported
honestly against what actually exists:

| File type | Available check | Strength |
|---|---|---|
| Frontend `.ts` / `.tsx` | `npm run build` (`tsc -b && vite build`) from `frontend/` | compile + type check |
| Frontend rendered result | dev server + browser inspection | the only real UI validation |
| Backend `.py` (before a dependency manifest exists) | read-back + self-review only | **no executable check** — must be stated as such |
| Backend `.py` (after the manifest exists) | `python -m compileall`, import check, and any configured checks | syntax + import resolution |
| Docs / config | read-back + consistency review against authority order | manual |

If no executable check exists for a file, the completion report must say
"reviewed, not executed" rather than implying verification.
