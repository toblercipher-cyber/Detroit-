<!-- Purpose: Define the technical architecture of the AI Website Builder, including where generated websites live, how they are built and previewed, and the boundaries that keep implementation aligned with the approved stack. -->

# Architecture

## 1. Goal

A controlled Lovable-style AI website builder using
**FastAPI + Pydantic + LangGraph + LangChain + Agent Harness**, with scoped context,
HITL gates, evaluation and MCP as an optional external capability layer.

## 2. High-Level Flow

```text
User
  ↓
Chatbot Page (/chat)
  ↓
FastAPI API                (thin routes, Pydantic in/out)
  ↓
Services / Orchestration
  ↓
LangGraph Workflow         (state, transitions, HITL interrupts, iteration loops)
  ↓
Agent Harness              (agent selection, scoped context, tool permissions)
  ↓
ProjectState               (checkpointer + JSON artifacts)
  ↓
Planning → HITL → Design → HITL → Reference Analysis → HITL
  ↓
Frontend Architecture → Code Generation
  ↓
Generated Workspace → Build → Preview
  ↓
Evaluation → Targeted Fix → Re-evaluation → Final Validation
  ↓
Optional MCP Action        (approval required)
```

## 3. Core Layers

### 3.1 Frontend
Landing Page (`/`), Pricing Page (`/pricing`), Chatbot Page (`/chat`). The Chatbot
Page hosts the HITL review UI, stage/build status and the preview surface. See
`docs/frontend.md`.

### 3.2 FastAPI
Owns HTTP only. Route handlers stay thin: validate, delegate to a service, return a
typed response. No agent reasoning in route functions. Contract: `docs/api.md`.

### 3.3 LangGraph
Owns workflow state, stage transitions, conditional routing, HITL interrupt/resume,
iteration loops and checkpointing. Graph shape: `docs/state.md` §4.

### 3.4 Agent Harness
The runtime coordination layer around agents:

```text
select agent -> build scoped context -> expose allowed tools -> execute
  -> capture result -> validate -> update state -> decide next action
```

It is **not** "run every agent". Only roles relevant to the current stage are
activated. Contracts: `docs/agents.md`.

### 3.5 LangChain
Model abstraction for Anthropic Claude, prompt composition, structured output
helpers, and tool wrappers where they genuinely help. Do not force LangChain
abstractions where plain Python is clearer.

### 3.6 MCP
Optional external capability layer (GitHub, Lovable, browser, deployment). The core
build workflow must function with **no** MCP server connected. See `docs/mcp.md`.

## 3.7 Two Distinct Routers

The word "routing" appears in two unrelated layers of this project. They must never be
conflated in code, naming or documentation.

```text
FRONTEND ROUTER  (TypeScript, browser)        AGENT / WORKFLOW ROUTER  (Python, server)
─────────────────────────────────────         ──────────────────────────────────────────
React Router                                  LangGraph + Agent Harness
routes URL paths                              routes workflow nodes and agent roles
  /         -> Landing Page                     planning        -> Design Director
  /pricing  -> Pricing Page                     evaluation      -> 4 reviewers (parallel)
  /chat     -> Chatbot Page                     conditional edge-> targeted_fix | final
state: browser location                       state: ProjectState + LangGraph checkpoint
spec: docs/frontend.md §4                     spec: §3.4 above + docs/state.md §4
```

- React Router has **no** role in agent selection, stage transitions or HITL.
- The Agent Harness has **no** role in URLs, pages or navigation.
- Agent/stage transitions are never implemented in frontend code.
- A LangGraph node is not a "route"; a URL path is not a "node".

## 3.8 Database — Supabase / PostgreSQL

**Decided:** the project's database is **Supabase / PostgreSQL**, and DETROIT is
an **authenticated multi-user product** using **Supabase Auth**. This is the
builder's own persistence and identity layer; it does not contradict the
frontend-first rule for *generated* websites, which still get no backend, no
database and no auth of their own.

**`docs/database.md` is the authority** for every auth and persistence decision:
the ownership model, the RLS requirement, the `auth.users` / application-table /
LangGraph-checkpoint boundary, and which Supabase key may reach a browser.

This section previously said schema and access patterns were "not decided" and
forbade adding a driver. **Both of those are now spent.** Current position:

| | |
|---|---|
| Schema | **designed** (`database.md` §3) — though that DDL is superseded by the ownership and RLS requirements (§3.0) |
| Connection | **live and verified** — PostgreSQL 17.11 via the Supabase session pooler |
| Driver | `psycopg` 3.3.6 in `.venv`; **not yet declared** in `requirements.txt` |
| Tables, migrations, RLS, auth code | **none written** |

Consequences for implementation:

- The architecture is a **third layer**, not two: `auth` (Supabase), `public`
  (ours, RLS-protected), `langgraph` (the library's, never hand-designed).
- Keep persistence behind the narrow interface in `backend/memory/`. Postgres
  arrives as a second implementation behind it, not as a rewrite.
- **One open decision blocks implementation:** which database role the backend
  connects as (`database.md` §5.2 #7). Connecting as a superuser bypasses RLS
  and makes the policy layer decorative.
- Interim behavior for state persistence: `docs/state.md` §5.

## 3.9 Zapier — Optional Automation Layer (deferred)

Zapier is an **optional automation layer for later workflows, logging and
notifications**. Nothing in v1 depends on it.

It is **not** the database, **not** the orchestrator, and **not** a workflow engine
for this product's stages. When enabled it sits at the edge, downstream of a completed
workflow step, subject to the same external-action approval gate as any other external
integration. See `docs/mcp.md` §2.5.

## 4. Backend Layering

```text
backend/
├── main.py            # FastAPI entry point; app creation + router wiring only
├── config/            # Pydantic Settings, env loading, validation
├── api/               # HTTP routers (thin)
├── schemas/           # Pydantic request/response models
├── graph/             # LangGraph workflow, nodes, edges, checkpointer
├── agents/            # agent role definitions + runtime logic
├── context/           # ContextBuilder — per-role state slicing
├── services/          # application services (projects, sessions, uploads, preview)
├── tools/             # internal tool wrappers (file I/O, build runner)
├── evaluation/        # anti-slop, a11y, UX, code review orchestration
├── mcp/               # MCP client + capability discovery
├── memory/            # persistence interface (artifacts now; Supabase/Postgres when schema is defined)
└── utils/             # small generic helpers only
```

**Do not create every folder up front.** Under the one-file gate, create only what the
current approved step requires (`skills.md` §23).

## 5. Generated-Project Execution & Preview

This is the highest-risk subsystem: it builds code that a model wrote. The design
below constrains what the model can influence.

### 5.1 Where generated websites live

```text
var/projects/{project_id}/workspace/
├── package.json          # builder-owned, from a fixed template
├── vite.config.ts        # builder-owned, from a fixed template
├── index.html            # builder-owned, from a fixed template
├── tsconfig*.json        # builder-owned, from a fixed template
└── src/                  # THE ONLY MODEL-WRITABLE AREA
```

- `var/` is runtime data: outside the builder source tree, outside `frontend/`, and
  gitignored.
- One workspace per project. Workspaces never share a directory.
- A generated site is **frontend-only** — no server, database, auth or payment code is
  generated (`skills.md` §22).

### 5.2 The template constraint (the primary isolation mechanism)

Every generated project is instantiated from a **fixed, builder-owned template**:

- The Builder agent may write **only** under `workspace/src/` plus a small allowlist
  of asset paths.
- `package.json`, `vite.config.ts`, `index.html` and `tsconfig*.json` are written by
  the builder from the template and are **not** model-writable.
- Dependencies come from a **fixed allowlist** in the template. The model cannot add,
  change or pin a dependency. A requested dependency outside the allowlist is a HITL
  decision, not an autonomous action.

This matters because a build executes project code (config and plugins). Freezing the
config and the dependency set removes the model's ability to introduce executable
build-time code.

### 5.3 How a project is built

```text
1. write/patch files under workspace/src/
2. npm install --ignore-scripts        (allowlisted deps only; lifecycle scripts off)
3. npx tsc --noEmit                    (type check)
4. npx vite build                      (static output to workspace/dist/)
```

Every step is:

- invoked as an **argument list**, never a shell string, and never assembled from
  model output
- run with a timeout (`PREVIEW_BUILD_TIMEOUT_SECONDS`) and killed on overrun
- run with the workspace as CWD
- run with a minimal environment — no builder secrets are inherited
- captured with output **sanitized** before it reaches state or the API

### 5.3a GitHub synchronisation (implemented 2026-10-11)

```text
workspace/src/   ->  GitHub branch   (REST adapter, approval-gated, push only)
```

`backend/services/github_gateway.py` owns it; `backend/tools/github_rest.py`
is the transport. Push builds ONE commit through the Git Data API —
blobs → tree → commit → ref — so a generated project lands atomically instead
of as dozens of per-file commits. The ref moves without force.

MCP is a separate path, used by the **GitHub Operator agent** rather than by
the sync, because discovered tool names carry no meaning that deterministic
code can rely on (`docs/mcp.md` §2.1).

### 5.4 How preview is produced

**v1 serves a static build, not a dev server.**

```text
workspace/dist/  ->  GET /preview/{token}/...  (static, read-only)
```

The first path segment is a **signed five-minute capability token**, not a
project id. The static route cannot authenticate a caller — a browser tab or an
iframe sends no `Authorization` header — so the authorization travels in the
URL. It is minted only by the owner-scoped
`GET /api/v1/projects/{id}/preview`. Relative asset URLs keep working because
the generated project is built with `base: "./"`, so every sub-resource
inherits the token prefix automatically. See `docs/security.md` §9.1 and
`docs/api.md` §2.5.

Rationale: a per-project long-lived dev server would mean N model-driven processes
holding ports with HMR and a plugin pipeline. A one-shot build plus static serving has
a much smaller attack surface and is enough for review-and-iterate.

Serving rules:

- Read-only. Paths resolve inside `workspace/dist/` only; traversal returns `404`.
- A valid token is required, and ownership is re-checked on every request.
- Served with `X-Content-Type-Options: nosniff` and a restrictive CSP whose
  `frame-ancestors` comes from the CORS allowlist — never `*`.
- Rendered in a **sandboxed iframe** on the Chatbot Page.
- `preview_status` is `stale` when files changed after the last successful build.

### 5.5 How the Chatbot receives preview & status

```text
build step      -> build_progress   (SSE)
build finishes  -> preview_ready { preview_url, built_at }  (SSE)
client          -> GET /projects/{id}/preview  (authoritative URL + freshness)
                -> renders it in a sandboxed iframe
```

Failures surface as a sanitized `build_failed` error plus findings; raw build output
is logged server-side, never returned.

## 6. Architectural Boundaries

The **builder backend is required**. The **generated website stays frontend-first**.
Never confuse the two (`skills.md` §7).

Do not automatically invent, for a generated website: databases, authentication
backends, payment systems, microservices, cloud infrastructure or production APIs.

## 7. Design Principles

Prefer: small specialized agents · explicit typed state · scoped context · staged
workflow · targeted changes · deterministic helpers.

Avoid: one giant prompt · all-agent execution · hidden global state · full-project
regeneration for a local change · abstraction for one-off behavior.
