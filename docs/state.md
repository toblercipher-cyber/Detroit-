<!-- Purpose: Define the canonical project state, its relationship to LangGraph state, how it persists across restarts, and how scoped agent context is derived from it. -->

# Project State

## 1. Principle

Project state is the source of truth for approved decisions and stage outputs. Do not
reconstruct important decisions from conversation memory when state already records
them (`skills.md` §28).

There is exactly **one** canonical state model. It is not duplicated across a separate
"graph state" and "app state".

## 2. Canonical `ProjectState`

A single Pydantic model. Field groups:

### Identity

| Field | Type | Notes |
|---|---|---|
| `project_id` | `str` | `proj_<32 hex>` — stable for the project's whole life |
| `session_id` | `str` | `sess_<32 hex>` — the current client attachment |
| `title` | `str` | derived from the prompt, user-editable |
| `created_at` / `updated_at` | `datetime` | UTC |
| `schema_version` | `int` | migration guard for persisted JSON |

### Requirements & planning

`user_request`, `normalized_requirements`, `requirement_conflicts`,
`requirement_gaps`, `plan`

### Design

`design_spec`, `selected_font`, `typography`, `color_system`, `spacing_rules`,
`component_rules`

### Reference

`reference_images` (list of `ReferenceImageRef`), `reference_analysis`,
`approved_reference_direction`

### Architecture & build

`frontend_architecture`, `component_tree`, `generated_files`, `file_versions`,
`build_status`, `preview_status`

### Evaluation

`evaluation_results`, `impeccable_findings`, `ux_findings`,
`accessibility_findings`, `code_review_findings`, `iteration_count`

### Control & audit

`current_stage`, `run_status`, `next_action`, `pending_checkpoint`,
`hitl_history`, `approved_decisions`, `rejected_options`, `mcp_action_history`,
`last_error`

### Transient

`node_scratch` — per-node working data. **Never persisted into
`approved_decisions`, never included in an API response, never treated as a
decision.** Cleared when a stage completes.

### 2.1 State rules

- **Immutable decisions.** Once explicitly approved, a decision is preserved unless
  the user changes it. Agents may read `approved_decisions`; only a HITL decision or
  an explicit user instruction may write to it.
- **Stage outputs are structured.** Each stage writes a typed object the next stage
  consumes without re-reading the original prompt.
- **Auditability.** Record approvals, rejected options, custom instructions, important
  tool actions, evaluation findings and final accepted decisions.
- **Separation.** Durable decisions live in state; conversational noise does not.

## 3. Relationship to LangGraph State

**`ProjectState` *is* the LangGraph graph state.** There is no second model and no
translation layer.

```text
ProjectState  ── flows through every graph node
      │
      ├── node reads only the slice the ContextBuilder gives it (§6)
      ├── node returns a partial update (only the fields it owns)
      └── LangGraph reducers merge the update
```

Rationale: a separate graph state plus a separate app state would create two sources
of truth for approved decisions — exactly the failure mode `CLAUDE.md` §9 warns
against.

Conventions:

- **Field ownership.** Each field has exactly one owning node. A node returns only its
  own fields. `iteration_count` is owned by the Targeted Fix node; `approved_decisions`
  and `hitl_history` are owned by the HITL resume path only.
- **Append-only lists** (`hitl_history`, `mcp_action_history`, `approved_decisions`,
  `rejected_options`) use append reducers, never replacement.
- `thread_id` for the checkpointer is the **`project_id`**.

## 4. Graph Shape

Nodes map one-to-one onto `docs/workflow.md` §1:

```text
requirement_analysis
  -> planning -> [interrupt: plan gate]
  -> design -> [interrupt: design gate]
  -> reference_analysis (conditional edge: only if reference_images is non-empty)
       -> [interrupt: reference gate]
  -> frontend_architecture
  -> build
  -> preview
  -> evaluation   (parallel fan-out: visual_ux | anti_slop | a11y | code_review)
  -> conditional: findings? AND iteration_count < 3
       -> targeted_fix -> re_evaluation -> (loop back to the conditional)
       else -> final_validation -> [interrupt: final gate]
  -> external_action (optional; entered only on explicit request + approval)
```

A gate node raises a checkpoint and interrupts. The stage a custom instruction
targets **re-runs in place** rather than advancing (`docs/workflow.md` §3).

## 5. Persistence

### 5.0 Target store: Supabase / PostgreSQL

**Decided:** the project's database is **Supabase / PostgreSQL**
(`docs/architecture.md` §3.8), and the architecture is multi-user with
Supabase Auth and RLS (`docs/database.md` — the authority).

**The schema is no longer undecided.** `docs/database.md` §3 designs it. This
section previously said "do not design a schema in advance"; that instruction
is spent, and keeping it would now block the work it was protecting.

**Status, verified 2026-10-09:**

| | |
|---|---|
| Connection to Supabase PostgreSQL | **live** — PG 17.11, authenticated query succeeded |
| `DATABASE_URL` in settings, as `SecretStr` | **done** |
| `psycopg` 3.3.6, `langgraph-checkpoint-postgres` 3.1.2 | installed **and declared** in `requirements.txt` |
| Postgres `StateRepository` | **written** — `backend/memory/postgres.py`, owner-scoped |
| Postgres checkpointer | **live** — durable across restarts, verified |
| Tables, migrations, RLS | **applied** — migrations 0001 and 0002 |

The interface rule is unchanged and still load-bearing:

- Persistence lives behind a narrow interface in `backend/memory/`.
- The graph and the API depend on that interface, never on a concrete store.
- Postgres arrives as a **second implementation** behind it — the checkpointer
  moves to the Postgres saver and artifacts move to tables — without reworking
  the graph, the agents or the API contract.

#### 5.0.1 One thing multi-user does change here — OPEN

Owner-scoped listing means the caller's identity has to reach the query.
`ProjectState` has no owner field and the `StateRepository` methods take no
caller. Either:

- `ProjectState` gains an owner, which makes it part of the state the graph
  carries; or
- identity is passed beside it as a caller argument, which changes the
  Protocol's signatures.

**DECIDED 2026-10-09: neither.** `ProjectState` gains no owner — it is the
LangGraph graph state, so an owner there would be serialized into every
checkpoint and reach agent context slices (`CLAUDE.md` §8). And the Protocol's
eight signatures are unchanged, because the owner binds to a *scoped
repository view* (`OwnerScopedRepository`) and a *scoped service*
(`ProjectService.for_owner`) instead of becoming a parameter on eighteen call
sites. A route that forgets to scope raises `TypeError` rather than leaking.

#### 5.0.2 A Windows constraint worth recording

Async psycopg refuses to run on Windows' default `ProactorEventLoop`:

> `Psycopg cannot use the 'ProactorEventLoop' to run in async mode.`

uvicorn on Windows selects that loop by default, so the composition root must
set `WindowsSelectorEventLoopPolicy` or the async checkpointer fails at
runtime. Verified: the same connection succeeds under the selector loop.

### 5.1 Interim layers (still in force today)

| Layer | What | Where | Purpose |
|---|---|---|---|
| **Checkpointer** | LangGraph graph checkpoints, including pending interrupts | `InMemorySaver` — **process memory, not disk** | correctness — lets an interrupted run resume exactly where it paused, for as long as the process lives |
| **Artifacts** | Human-readable structured state | `var/projects/{project_id}/*.json` | auditability — inspectable, diffable, matches `CLAUDE.md` §9 |

**Both are still what runs.** The database is connected and the schema is
designed, but no Postgres repository or Postgres checkpointer has been written,
so these two remain the live implementation — see §5.0 for exactly how far the
migration has got, and §5.0.1 for the decision that blocks the next step.

Of the two, only the **artifacts** are superseded outright by
`docs/database.md` §5 #1, which keeps them as the local export/review
representation rather than deleting them. The checkpointer is a straight
replacement by LangGraph's Postgres saver.

The interface they sit behind does not change.

Artifact files, written after each stage completes:

```text
var/projects/{project_id}/
├── project_state.json        # full ProjectState (minus node_scratch)
├── design_spec.json          # approved design system
├── component_tree.json       # approved component architecture
├── iteration_history.json    # evaluation findings + fixes per iteration
├── reference/                # uploaded reference images
└── workspace/                # the generated website (docs/architecture.md §5)
```

Rules:

- **The checkpointer is authoritative for resumption; artifacts are authoritative for
  human review.** On any disagreement, the checkpointer wins and the artifacts are
  rewritten from it.
- Artifacts are written **atomically** (temp file + `os.replace`) so a crash mid-write
  cannot corrupt them.
- `var/` is runtime data. It must be gitignored and must never live inside the
  builder's source tree or `frontend/`.
- **Builder persistence is builder infrastructure.** It does not contradict the
  frontend-first rule for *generated* websites (`skills.md` §7, §22). The generated
  website still gets no database, auth or backend.

#### Checkpointer: decided, with a known limitation

**Decided:** the interim checkpointer is LangGraph's **`InMemorySaver`**, wired at the
composition root in `backend/main.py`. It ships inside `langgraph`, so it adds no
dependency.

A checkpointer is **not optional**, and the way it fails without one is worth
recording: the first gate still pauses and still returns `__interrupt__`, so a run
*looks* healthy — but answering it raises
`RuntimeError: Cannot use Command(resume=...) without checkpointer`. The gate is
reachable and unanswerable.

**Known limitation, accepted for development:** `InMemorySaver` is per-process. A
restart **loses every pending HITL gate**, so §5.2 below describes the target
behaviour rather than current behaviour. Until a durable saver is wired, a restart
means in-flight projects cannot be resumed and must be started again.

This is interim only. Replacing it is a one-line change at the composition root and
touches nothing else — which is the reason it is injected there rather than
constructed inside `build_workflow`. The durable choice is deferred with the rest of
Supabase/PostgreSQL (§5.0), and a local durable saver remains an option if durability
is needed before the schema exists; either requires one additional dependency and
explicit approval. See `docs/runtime.md` §2.1.

### 5.2 Surviving a process restart

> **Target behaviour, not current behaviour.** This section describes what a durable
> checkpointer must deliver. The interim `InMemorySaver` (§5.1) does **not**: it is
> per-process, so a restart drops every pending gate. Treat this as the contract the
> durable saver has to satisfy, and do not write code that assumes it holds today.

```text
restart
  -> API starts, opens the configured checkpointer
  -> GET /projects/{id}/status reads the latest checkpoint for thread_id=project_id
  -> if the checkpoint holds a pending interrupt:
         run_status = awaiting_hitl
         GET /projects/{id}/hitl returns the same checkpoint_id as before the restart
  -> POST /hitl resumes the graph from that exact interrupt
```

Nothing is replayed through the model, and no context is rebuilt from a transcript.
A `checkpoint_id` issued before a restart stays valid afterwards.

### 5.3 Identifiers & lifetime

| | `project_id` | `session_id` |
|---|---|---|
| Format | `proj_` + `uuid4().hex` | `sess_` + `uuid4().hex` |
| Created by | server, on `POST /projects` | server, on project creation or re-attach |
| Scope | the durable unit of work | one client attachment to a project |
| Cardinality | 1 project : N sessions over time | one active at a time |
| Lifetime | until explicitly deleted | idle TTL, default 24h (`SESSION_TTL_MINUTES`) |
| Survives restart | **yes** | yes, if within TTL; otherwise a new session attaches to the same project |

A session carries no approved decisions. Losing a session never loses project state —
that separation is the point.

## 6. Scoped Context Construction

```text
ProjectState
  -> ContextBuilder (per-role allowlist)
  -> minimal context slice
  -> Agent
```

The ContextBuilder is an explicit allowlist per role. An agent receives **only** the
listed fields — never the whole state, never the conversation transcript, never other
agents' raw outputs, never all tool schemas (`CLAUDE.md` §8, `skills.md` §11).

| Agent role | Receives |
|---|---|
| Requirement Analyzer | `user_request`, project constraints, `approved_decisions` |
| Planner | `normalized_requirements`, `requirement_conflicts`, `requirement_gaps`, `approved_decisions` |
| Design Director | approved `plan`, design constraints, `approved_reference_direction`, `approved_decisions` |
| Reference Analyzer | the reference image, `design_spec` (for conflict detection), `approved_decisions` |
| Frontend Architect | approved `plan`, approved `design_spec` |
| Builder | approved `plan`, `design_spec`, `component_tree`, **only the affected files**, acceptance criteria |
| Visual & UX Critic | rendered preview, `design_spec`, acceptance criteria |
| Anti-Slop Reviewer | rendered preview, `design_spec`, `approved_decisions` (to avoid "fixing" intent) |
| Accessibility Reviewer | rendered preview, changed files |
| Code Reviewer | changed files only |
| Fixer | the specific findings, the affected files, `approved_decisions` |
| GitHub Operator | the approved action plan, the file manifest |

Additional rules:

- **Never make an agent re-think an approved decision.** `approved_decisions` is
  passed as constraints, not as an open question.
- Summarize older context when full detail is no longer useful.
- Never pass hidden or private reasoning as application state.

## 7. Iteration Tracking

- Every automated correction increments `iteration_count`.
- Automatic correction stops at **3** (`MAX_AUTO_ITERATIONS`).
- User-initiated changes and custom HITL instructions do **not** consume the budget;
  the counter resets when the user approves a new stage result.
- `iteration_history.json` records, per iteration: findings, files changed, the fix
  rationale, and the re-evaluation outcome.
