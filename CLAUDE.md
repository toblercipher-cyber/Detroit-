# CLAUDE.md — AI Website Builder / Lovable-Style Frontend Agent

## 1. Project Mission

This project is a controlled Lovable-style AI website builder. Its purpose is to turn a user's natural-language requirements into a polished frontend/UI implementation through a structured, human-guided agent workflow.

This is **not** a generic chatbot and it is **not** a backend/application generator by default.

Primary differentiators:
- structured planning before implementation
- adaptive Human-in-the-Loop (HITL) approval gates
- reference-image-driven design analysis
- explicit design-system decisions
- Agent Harness orchestration and context isolation
- automated visual/design quality checks
- anti-AI-slop evaluation and self-correction
- MCP-based external tool integrations
- targeted iterative changes instead of unnecessary full-project regeneration
- optional repository/deployment actions only through explicit tool permissions

## 1A. Mandatory Project Knowledge Loading

`skills.md` is a required project knowledge and execution-rules document. Claude Code must read it before implementation and must read it again before starting each new file.

The entire `docs/` directory is also required project documentation. Before implementing the backend or making architectural decisions, Claude Code must inspect and read the relevant documentation files in `docs/`, including `architecture.md`, `workflow.md`, `agents.md`, `state.md`, `api.md`, `runtime.md`, `frontend.md`, `design.md`, `mcp.md`, and `security.md`. Do not treat the docs folder as optional reference material. `docs/README.md` carries the authority order and a map of which document owns which decision.

`CLAUDE.md` defines the high-level project architecture and non-negotiable engineering constraints. `skills.md` contains the detailed product knowledge, workflow gates, context rules, file-by-file execution protocol, and implementation conventions. The `docs/` directory contains the detailed technical specifications that must be followed during implementation.

Before starting any new file, Claude Code must re-read `CLAUDE.md` and `skills.md`, then read the relevant `docs/*.md` files for that file's layer or responsibility. If a change affects multiple layers, read all affected docs before editing.

Never proceed to a new implementation file using conversation memory alone when these project documents are available.

### Feature-batch development gate

Related files may be batched in one turn when they form one feature (component + page + route + nav wiring). No orphan components left behind. After completing a file:

1. Re-read the file.
2. Review the code against `CLAUDE.md` and `skills.md`.
3. Run the relevant checks.
4. Tell the user what was done and why the file exists.
5. Stop and ask: **“Please review this code. Should I start the next feature?”**
6. Do not start another feature until the user explicitly approves.
7. Before starting the next file, read `CLAUDE.md` and `skills.md` again.

Every newly created source/config file should include a concise top-level comment/docstring explaining its architectural purpose and why it exists.

## 2. Non-Negotiable Product Boundary

### Frontend-first scope
The core generator builds:
- websites
- landing pages
- dashboards
- marketing/product pages
- frontend components
- responsive UI systems
- visual design systems

Do **not** automatically invent or generate a full production backend architecture, database schema, authentication server, payment backend, microservices, or infrastructure unless the product scope is explicitly expanded later.

The user may connect or implement their own backend separately.

> **This rule is about the websites DETROIT GENERATES, not about DETROIT.**
> DETROIT itself has a backend (FastAPI + LangGraph), a database
> (Supabase/PostgreSQL) and, as of 2026-10-09, authentication (Supabase Auth) —
> all approved builder infrastructure under §9A. A generated coffee-shop
> landing page still gets none of those.

### Preserve user decisions
Never make the user re-decide something they have already explicitly specified or approved.

If a requirement is explicit, treat it as a constraint.
If a requirement is ambiguous, ask only the smallest necessary question.
If a decision is already approved, do not silently replace it during later stages.

## 3. Core Runtime Architecture

Use the following conceptual architecture:

```text
User
  ↓
Chat / Start Interface
  ↓
Agent Harness
  ↓
Orchestrator / LangGraph Workflow
  ↓
Structured Project State
  ↓
Planning → HITL
  ↓
Design → HITL
  ↓
Reference Analysis → HITL
  ↓
Frontend Architecture / Component Plan
  ↓
Code Generation
  ↓
Visual + Quality Evaluation
  ↓
Targeted Fix / Iteration
  ↓
Final Preview
  ↓
Optional MCP Actions (GitHub / Lovable / deployment)
```

The workflow is intentionally staged. Do not collapse all stages into one giant model call unless there is a concrete reason and the resulting behavior remains auditable.

## 3A. Two Distinct Routers — Do Not Confuse Them

This project contains **two unrelated things called "routing"**. They live in
different languages, different layers, and different repositories of concern.

| | **Frontend Router** | **Agent / Workflow Router** |
|---|---|---|
| What | React Router | Python + LangGraph / Agent Harness |
| Language | TypeScript | Python |
| Layer | Browser UI | Backend orchestration |
| Routes between | URL paths: `/`, `/pricing`, `/chat` | Workflow nodes and agent roles |
| Decides | which **page** the user sees | which **agent/node** runs next, and the stage transition |
| State | browser location | `ProjectState` + LangGraph checkpoint |
| Spec | `docs/frontend.md` §4 | `docs/architecture.md` §3.4, `docs/state.md` §4 |

**React Router has nothing to do with agent selection.** The Agent Harness has nothing
to do with URLs. Never describe one in terms of the other, never let one's naming leak
into the other's modules, and never implement agent/stage transitions in frontend code.

## 4. Agent Harness Responsibilities

The Agent Harness is the runtime coordination layer around the agents. It is responsible for:

- choosing which agent should act
- controlling when an agent is activated
- passing only relevant context to that agent
- controlling tool availability and permissions
- maintaining project/session state
- coordinating sequential and parallel work
- enforcing HITL checkpoints
- collecting agent outputs
- invoking validation/evaluation
- controlling retries and iteration limits
- preventing unnecessary repeated work
- recording important decisions and outcomes

The harness is **not** equivalent to the list of agent definitions.

A project can have many specialized agent definitions while only a small subset is active for a given task.

## 5. Agent Model

Prefer a small number of highly specialized agents over a large number of overlapping agents.

Finalized v1 roles (full contracts in `docs/agents.md`):

1. Requirement Analyzer Agent
2. Planner Agent
3. Design Director Agent
4. Reference Analyzer Agent
5. Frontend Architect Agent
6. Builder Agent
7. Visual & UX Critic Agent *(Visual Critic and UX Reviewer are merged — their
   responsibilities overlapped, and overlapping agents are discouraged)*
8. Anti-Slop Reviewer Agent
9. Accessibility Reviewer Agent
10. Code Reviewer Agent
11. Fixer Agent
12. GitHub Operator Agent *(gated; external writes require approval)*

Deferred until explicitly requested: Lovable Operator, Deployment Operator.

These are roles, not permanently running processes.

Agents may share infrastructure while receiving different role instructions, scoped context, and tool permissions.

## 6. Agent Delegation Rules

Do not invoke every agent for every request.

Use the minimum set of agents required to complete the current stage.

Typical delegation:

```text
Planning request      → Planner
Design decisions      → Design Director
Reference image       → Reference Analyzer
UI implementation     → Frontend Architect + Builder
Visual / UX review     → Visual & UX Critic
AI-slop detection     → Impeccable (mandatory) + Anti-Slop Reviewer
Accessibility         → Accessibility Reviewer
Code quality           → Code Reviewer
Targeted corrections   → Fixer
Repository action      → GitHub Operator
Deployment action      → Deployment Operator
```

### Sequential work
Use sequential execution when a later stage depends on an approved earlier result.

Example:

```text
Planning → HITL approval → Design → HITL approval → Build
```

### Parallel work
Parallelize only when tasks are genuinely independent.

Example:

```text
Rendered UI
   ├── Visual & UX review
   ├── Accessibility review
   └── Anti-slop review
```

Parallel agents increase speed but can increase total token usage. Do not parallelize simply because parallel execution is available.

## 7. One-Provider Model Strategy

The initial runtime should support a single primary model provider rather than requiring a different API for every agent.

Preferred initial approach:

```text
One Claude API integration
       ↓
Agent Harness
       ↓
Specialized agent roles
```

Separate API keys are not required per agent.

Actual model invocations still consume tokens individually. Agent definitions sitting on disk do not consume model tokens by themselves.

Future provider abstraction may be added, but do not create unnecessary multi-provider complexity during the first implementation.

## 8. Context Management

Context management is a first-class architecture concern.

Never pass the entire conversation, entire repository, every agent output, every tool schema, and every previous iteration to every agent.

Use a context builder/slicer between state and agent execution.

```text
Global Project State
       ↓
Context Builder
       ↓
Relevant Context Slice
       ↓
Agent
```

### Example context boundaries

Planner receives:
- original user requirements
- product/business intent
- explicit constraints
- relevant project metadata

Design Agent receives:
- approved requirements
- approved planning state
- visual requirements
- relevant reference analysis

Builder receives:
- approved plan
- approved design specification
- component architecture
- relevant files/code
- acceptance criteria

Reviewer receives:
- rendered result or changed files
- applicable design/quality criteria
- approved design specification

Do not force every agent to understand the whole project history.

## 9. Structured Project State

Prefer structured state over repeatedly passing raw chat transcripts.

Recommended conceptual state:

```text
project_state
├── user_requirements
├── requirement_status
├── approved_plan
├── design_spec
├── reference_analysis
├── component_tree
├── implementation_status
├── quality_findings
├── user_feedback
├── iteration_count
├── approval_history
└── external_actions
```

Store durable decisions separately from conversational noise.

Recommended artifacts when appropriate:

```text
project_state.json
design_spec.json
component_tree.json
iteration_history.json
```

Do not duplicate the same source of truth across many files without a clear reason.

## 9A. Builder Persistence, Database & Authentication

### Decided

The project's database is **Supabase / PostgreSQL**, and DETROIT is an
**authenticated multi-user product** built on **Supabase Auth**. Both are the
builder's own infrastructure — neither contradicts §2, which forbids inventing a
backend, database or auth server for a *generated* website.

**`docs/database.md` is the authority** for every authentication and persistence
decision: the ownership model, the RLS requirement, the `auth.users` /
application-table / LangGraph-checkpoint boundary, and which Supabase key may
reach a browser. Read it before touching either subject.

### The former prohibition is lifted

This section used to forbid adding a driver "until the schema is defined and
approved". **That condition is met.** The schema is designed (`docs/database.md`
§3) and the connection to Supabase PostgreSQL is live and verified. The
prohibition is history, not standing policy.

### Status (verified 2026-10-09)

| | |
|---|---|
| Connection | **live** — PostgreSQL 17.11 via the Supabase session pooler |
| `DATABASE_URL` + four `SUPABASE_*` vars | declared in `settings.py`, secrets as `SecretStr` |
| `psycopg` 3.3.6 + `langgraph-checkpoint-postgres` 3.1.2 | installed and declared |
| Migrations 0001 + 0002 | **applied** — 6 tables, RLS forced, 14 policies |
| Backend app role | **`detroit_app`**, `NOBYPASSRLS`; `postgres` only for migrations/checkpointer |
| Auth | Supabase Auth, ES256/JWKS verified server-side; every project route owner-scoped |

### Still binding

- The DDL in `docs/database.md` §3 is **superseded** — it predates ownership and
  RLS. Do not run it (§3.0).
- **No ORM.** The `StateRepository` Protocol plus psycopg is the whole stack; an
  ORM would add a second schema definition to keep in sync with the Pydantic
  models.
- **Never hand-write LangGraph's checkpoint tables.** They belong to
  `langgraph-checkpoint-postgres` (§1.2).
- Keep persistence behind the narrow interface in `backend/memory/`. Postgres is
  a second implementation behind it, not a rewrite.
- **Ownership and RLS land together.** A tenant column without RLS is worse than
  neither, because the schema then looks multi-tenant while enforcing nothing.
- The backend connects as **`detroit_app`** for application queries. Never use
  the `postgres` DSN for them: it carries `BYPASSRLS` and would silently
  disable every policy (`docs/database.md` §5.2, resolved).

See `docs/state.md` §5 for what still runs today — the interim filesystem
repository and `InMemorySaver` are both still live.

### Not the database

**Zapier is not a database** and not the orchestrator. See §19A.

## 10. Strong Prompt / Requirement Handling

Users may provide:

### Sparse requirements
Example:
> Build me a coffee shop landing page.

The system should discover missing decisions through structured planning and adaptive HITL.

### Highly detailed requirements
Example:
- exact pages
- sections
- typography
- colors
- interactions
- content hierarchy
- responsive behavior
- component expectations
- reference image
- technical constraints

Do not ask the user to repeat information that already exists.

When requirements are highly detailed:
- normalize them into structured state
- validate conflicts/missing dependencies
- preserve explicit decisions
- reduce unnecessary brainstorming
- reduce redundant HITL questions
- focus agents on validation and execution

Detailed does not automatically mean efficient. Avoid repetitive, contradictory, or irrelevant context.

## 11. Adaptive HITL

HITL is for user authority and important decision boundaries, not merely for ambiguous prompts.

The number of HITL steps should adapt to the quality and completeness of the requirements.

### Sparse request
More clarification/approval may be required.

### Detailed request
Use fewer, higher-value HITL checkpoints.

### Typical checkpoints

1. Planning approval
2. Design direction approval
3. Reference-analysis approval when a reference is supplied
4. Final or pre-external-action review

The exact number may vary based on the request.

### HITL options

Each review should present:
- approve / accept
- one or more relevant modification choices generated from the AI result
- custom instruction

The custom option must allow the user to override or refine the current result directly.

Never hide a destructive or externally mutating action behind a normal autonomous step.

## 12. Planning Phase

Planning happens before code generation.

Planning should identify, when applicable:
- project/page goal
- target audience
- content hierarchy
- page structure
- sections
- CTA strategy
- required interactions
- responsive considerations
- design intent
- explicit constraints
- acceptance criteria

Planning output must be structured enough for later stages to consume without reinterpreting the user's original request from scratch.

## 13. Design Phase

The design stage should establish a coherent visual system, not a collection of disconnected styling choices.

Capture, when applicable:
- font family
- typography hierarchy
- font weights
- color palette
- spacing rhythm
- layout system
- border radius
- component treatment
- button treatment
- card treatment
- motion/animation principles
- density
- visual tone

When the user explicitly specifies a font, palette, or typography system, preserve it unless the user approves a change.

## 14. Reference Image Workflow

When the user provides a reference image:

```text
Reference Image
      ↓
Reference Analyzer
      ↓
Structured Design Interpretation
      ↓
HITL Confirmation / Correction
      ↓
Approved Design Direction
```

Analyze for:
- composition
- layout
- hierarchy
- typography characteristics
- color relationships
- spacing
- card/container treatment
- navigation patterns
- hero structure
- decorative language
- interaction cues
- responsive implications where inferable

Use the reference to extract design intent. Do not blindly reproduce copyrighted or brand-specific assets.

## 15. Anti-AI-Slop / Impeccable Quality Layer

Use Impeccable as a quality/evaluation subsystem, not as a replacement for the project's own design reasoning.

**Impeccable anti-AI-slop evaluation is MANDATORY** in the
generation → evaluation → fix → re-evaluation loop. It is not optional and has no
substitute.

**If Impeccable is unavailable: STOP and report the quality gate as BLOCKED.**
Do not advance the workflow, do not substitute an agent-only review for it, and
**never report the check as completed when it did not run.**

**Current availability:** Impeccable is **installed** — a Claude Code skill (4.3.1)
with a CLI (`--version` 4.0.0) whose `detect --json` is the detector. `.impeccable/`
is optional per-project detector config, NOT an installation marker; earlier docs
conflated the two. The evaluation stage is still blocked, but on capturing a
rendered preview for the reviewers. See `docs/design.md` §5.2.

Current Impeccable documentation describes deterministic detection for AI-generated frontend anti-patterns, live/browser iteration, design-system artifacts, and hook-based feedback for supported coding harnesses. Use the installed/current project version rather than assuming a fixed rule count or command set.

Preferred quality loop:

```text
Generated UI
    ↓
Impeccable detector / design review
    ↓
Findings
    ↓
Fix instructions
    ↓
Targeted modification
    ↓
Re-evaluate
```

Important:
- a detector finding is evidence, not proof that the whole design is bad
- a clean detector run is not proof of visual quality
- always inspect the rendered result and real interaction path
- do not blindly “fix” intentional design decisions without checking project intent

Avoid generic AI-generated patterns such as:
- unnecessary gradients
- excessive glassmorphism
- repetitive rounded cards
- arbitrary icon tiles
- weak typography hierarchy
- excessive decorative blobs
- excessive glow/shadow effects
- generic SaaS template composition
- inconsistent spacing
- poor responsive behavior

The goal is not “make it different for the sake of being different.” The goal is coherent, purposeful design.

## 16. Design-System Artifacts

Where Impeccable or the project establishes durable product/design truth, prefer explicit artifacts such as:

```text
PRODUCT.md
DESIGN.md
```

**Current status of these artifacts (do not assume they are live truth):**

- `PRODUCT.md` — **legacy**. Written for the original Landing Page build only.
  Retained for historical/design context. Not authoritative for backend, current
  frontend scope, or architecture. See the notice at the top of that file.
- `DESIGN.md` — **does not exist yet**. Until it does, the approved Landing Page
  implementation plus `docs/design.md` carry the design truth. Do not wait on it
  and do not fabricate it.

Keep durable product facts separate from surface-specific visual decisions.

If a design system exists, reuse its tokens/components rather than inventing local one-off values.

## 17. Frontend Generation Rules

Generate the project in controlled layers:

```text
Design Specification
      ↓
Page Architecture
      ↓
Component Tree
      ↓
Component Implementation
      ↓
Responsive Layer
      ↓
Interaction / Motion Layer
      ↓
Quality Evaluation
```

Prefer targeted edits over full-project regeneration.

If the user says:
> Make the hero less empty.

Do not regenerate the entire application. Identify the affected surface/component and modify only the necessary scope.

## 18. Iteration Rules

When a user rejects a result:

```text
Current State
   ↓
User Feedback
   ↓
Affected Area Detection
   ↓
Targeted Change
   ↓
Validation
```

Do not discard approved decisions unless the new feedback explicitly requires them to change.

Maximum iterative redesign attempts: **3**, unless the project configuration explicitly changes this limit.

After repeated rejection, surface the unresolved conflict rather than endlessly regenerating the same design.

## 19. MCP Integration Layer

MCP is the external capability layer.

Conceptually:

```text
Agent Harness
     ↓
MCP Client
     ↓
MCP Server
     ↓
External System
```

Potential integrations:
- GitHub
- Lovable
- browser/preview tools
- deployment providers
- other developer tools

Do not hard-code assumptions about an MCP server's available tools. Discover and validate the actual server capabilities before invoking them.

### External write actions
Repository writes, deployment, or other destructive/external mutations require explicit permission/approval according to the tool's capabilities and project policy.

Never expose API keys, access tokens, OAuth secrets, `.env` values, or credentials in model prompts, source files, logs, generated UI, commits, or tool output.

## 19A. Zapier — Optional Automation Layer

Zapier is an **optional automation layer for later workflows, logging and
notifications**. It is deferred; nothing in v1 depends on it.

Zapier is explicitly **NOT**:
- the database (that is Supabase/PostgreSQL — §9A)
- the orchestrator (that is LangGraph + the Agent Harness — §3A, §4)
- a workflow engine for this product's stages
- a required dependency of any core stage

When it is eventually enabled, it sits at the edge, downstream of a completed
workflow step, and is subject to the same external-action approval rules as any other
external integration (§19). Do not route product logic, state or agent decisions
through it. See `docs/mcp.md` §2.5.

## 20. Repository / GitHub Workflow

When the user requests repository synchronization:

```text
Build complete
   ↓
Validation complete
   ↓
Show summary / require approval when policy requires
   ↓
GitHub MCP
   ↓
Branch / commit / push / PR as configured
```

Do not push unreviewed or obviously broken code simply because the user asked for automation.

Preserve repository history and avoid destructive Git operations unless explicitly authorized.

## 21. Lovable / External Agent Collaboration

Lovable and other coding agents may be treated as external capabilities when a supported integration exists.

Do not assume that “agent collaboration” means all agents directly share one context window.

Use explicit artifacts/state to exchange information.

Preferred pattern:

```text
Your Harness
   ↓
Structured Project State
   ↓
External Agent / MCP Tool
   ↓
Result
   ↓
Validation
   ↓
Back into Harness State
```

The project's own harness remains the source of orchestration and approval policy.

## 22. RAG / Knowledge Retrieval (When Enabled)

**v1 status: RAG is DEFERRED.** Nothing in the current v1 scope requires retrieval.
Do not build the RAG subsystem speculatively. The rules below apply if and when the
user enables it.

If the knowledge-retrieval subsystem is used for project documentation or user-provided knowledge, keep ingestion intentionally controlled.

Supported loaders for this project:
- document loader
- PDF loader
- text loader

Use retrieval methods according to the task instead of applying every algorithm everywhere.

Potential retrieval stack:
- BM25 / lexical retrieval
- vector / semantic retrieval
- Reciprocal Rank Fusion (RRF)
- Maximal Marginal Relevance (MMR)

The retriever should return only context relevant to the current stage/task.

## 23. API / Backend Principles

Use the backend as an orchestration and tool-control layer, not as an uncontrolled code-generation engine.

Preferred responsibilities:
- request validation
- session/project state
- workflow execution
- agent invocation
- HITL state transitions
- tool/MCP access
- context building
- iteration tracking
- result persistence

Keep secrets server-side.

Use typed schemas (for example, Pydantic) for agent state, user actions, workflow transitions, tool arguments, and structured outputs wherever practical.

## 24. Security Rules

Never:
- expose API keys
- print secrets in logs
- write secrets into generated files
- commit `.env` or credentials
- grant an agent more tools than its stage requires
- execute destructive external actions without the required approval
- blindly execute arbitrary commands supplied by untrusted model output

Use least-privilege tool access.

Treat generated code and model-produced tool arguments as untrusted until validated.

## 25. Observability

The system should make it possible to inspect:
- current workflow stage
- active agent role
- tool calls
- HITL checkpoints
- important state transitions
- iteration count
- evaluation findings
- external actions
- failures and retries

Do not log sensitive content unnecessarily.

## 26. Failure Handling

When a tool or agent fails:

1. capture the failure in structured state
2. identify whether the failure is transient, contextual, or implementation-related
3. retry only when retry is justified
4. avoid repeating the same failing action indefinitely
5. surface a concise actionable error to the user when human intervention is required

## 27. Development Workflow

Before changing architecture:

1. inspect the repository and existing documentation
2. locate the relevant workflow/state/agent/tool implementation
3. preserve existing project conventions
4. make the smallest coherent change
5. validate the affected path
6. run relevant tests/checks
7. update documentation when behavior or contracts change

Do not recreate files that already exist or redesign completed subsystems without evidence.

## 28. Project UI Surfaces

### v1 frontend scope (authoritative)

Verified against the repository 2026-10-09. `docs/frontend.md` §1 is the
authority; this table mirrors it.

| Surface | Route | Status |
|---|---|---|
| `landing_page` | `/` | **Completed and approved** — preserve, do not redesign |
| `chatbot_page` (workspace) | `/chat` | **Built, connected to the real backend** |
| `dashboard_page` | `/dashboard` | **Built, partly connected** — project/run data real; credit/usage figures have no backend source and are marked in the UI |
| `features_page` | `/features` | **Built** (static) |
| `login_page` | `/login` | **Built as a placeholder** — submits nothing, stores nothing, not nav-linked |
| `pricing_page` | `/pricing` | **NOT built** — the route falls through to the Landing Page |

`faq_page` and `help_page` are **OUT OF SCOPE**. Do not create, plan, link
to, or design toward them unless the user explicitly requests them later.

**Authentication will make `/chat` and `/dashboard` protected routes.** Nothing
of that is implemented; `react-router-dom` is still not installed and `App.tsx`
uses a temporary `location.pathname` bridge.

Routing approach is specified in `docs/frontend.md`.

### Reference assets

The only reference asset in current project context is:

```text
Refrence_images/chatbot_refrence.png
```

There is **no landing-page reference file in the repository**. The Landing Page is
already built and approved; the built page itself — not an image — is the design
reference for new pages (see `docs/design.md`).

Use the project's actual files as the source of truth. Do not fabricate missing
reference assets. If a required reference asset is unreadable at runtime, report it
and stop at the appropriate workflow boundary (`skills.md` §16, §33).

## 29. Code Quality Principles

Prefer:
- small cohesive modules
- clear class/function responsibility
- typed interfaces
- Pydantic validation
- explicit workflow/state transitions
- dependency injection where useful
- deterministic helper functions where possible
- clear exception handling
- reusable components
- testable logic

Avoid:
- giant orchestrator files
- hidden global state
- duplicated prompts across many modules
- passing entire histories everywhere
- arbitrary agent-to-agent chatter
- automatic full-project rewrites
- unnecessary abstraction for one-off behavior

## 30. Core Engineering Principle

The project should behave like a controlled software-building system, not a single-shot chatbot.

The guiding sequence is:

```text
Understand
→ Structure
→ Approve
→ Design
→ Approve
→ Build
→ Evaluate
→ Correct
→ Validate
→ Integrate
```

Most importantly:

> Never make an agent re-think a decision that the user has already explicitly made and approved.

> Never activate an agent merely because it exists. Activate it because its specialization is relevant to the current task.

> Never give an agent more context or tool access than it needs to perform its current responsibility.

> Prefer controlled iteration over unnecessary regeneration.
