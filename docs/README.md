<!-- Purpose: Provide a quick map of the project documentation and state the authority order between documents. -->

# Project Documentation

## Authority Order

When documents disagree, this order decides:

```text
1. Direct user instruction in the current task
2. Explicitly approved decisions recorded in project state
3. CLAUDE.md
4. skills.md
5. docs/*.md
6. Agent assumptions   (lowest — never overrides the above)
```

`PRODUCT.md` is **legacy** — written for the original Landing Page build only. It is
retained for historical/design context and is **not** authoritative for backend
scope, current frontend scope or architecture. See the notice at the top of that file.

`DESIGN.md` does not exist yet. Until it does, the approved Landing Page
implementation plus `docs/design.md` carry the design truth.

## Root

- `../CLAUDE.md` — project constitution and runtime constraints.
- `../skills.md` — product knowledge and mandatory development behavior.
- `../PRODUCT.md` — legacy; historical context only.

## Specifications

| File | Owns |
|---|---|
| `architecture.md` | technical architecture, backend layering, generated-project execution & preview |
| `workflow.md` | the staged pipeline, adaptive HITL, iteration limit, stop conditions, one-file gate |
| `agents.md` | the 12 v1 agent roles and their full contracts |
| `state.md` | canonical `ProjectState`, LangGraph relationship, persistence, scoped context |
| `api.md` | the concrete FastAPI/Pydantic contract |
| **`database.md`** | **authentication & database architecture — Supabase Auth, ownership, RLS, the PostgreSQL schema.** Authoritative for every auth and persistence decision |
| `runtime.md` | Python version, dependencies, `.env`, Claude model configuration, config validation |
| `frontend.md` | frontend scope, the Landing Page rule, **React Router** specification |
| `design.md` | approved design system, reference-conflict procedure, anti-slop gate |
| `mcp.md` | MCP capability layer, discovery, least privilege, external-write approval |
| `security.md` | secrets, generated-code isolation, uploads, prompt injection, logging |

## Two Routers — Do Not Confuse Them

| | Frontend Router | Agent / Workflow Router |
|---|---|---|
| What | React Router (TypeScript) | LangGraph + Agent Harness (Python) |
| Routes | URL paths `/`, `/pricing`, `/chat` | workflow nodes and agent roles |
| Spec | `frontend.md` §4 | `architecture.md` §3.7, `state.md` §4 |

## Current Status

Last verified 2026-10-09. Statuses here are observed, not planned: each one was
checked against the repository or a live run.

### Frontend

| Surface | Route | Status |
|---|---|---|
| Landing Page | `/` | **built and approved** — preserve |
| Chatbot workspace | `/chat` | **built, and connected to the real backend** |
| Dashboard | `/dashboard` | **built, partly connected** — project/run data is real; every credit and usage figure has **no backend source** and is marked as such in the UI |
| Product / features | `/features` | **built** (static) |
| Login | `/login` | **built and wired to Supabase Auth** — real sign-in/sign-up, honours `?next=`; still not linked from the nav |
| Pricing Page | `/pricing` | **NOT built.** The route currently falls through to the Landing Page |

| Area | Status |
|---|---|
| Frontend routing | `react-router-dom` **still not installed**. `App.tsx` carries a temporary `location.pathname` bridge (`frontend.md` §4) |
| Backend transport | `frontend/src/lib/api.ts` — typed client, validated field-by-field against the backend's OpenAPI output |
| Live progress | **polling `GET /status`.** SSE is specified but **not implemented** in the backend |

### Backend

| Area | Status |
|---|---|
| Backend | **implemented and tested** — 48 suites pass. FastAPI app, LangGraph workflow, 12 agent roles, build runner, preview capture, MCP discovery |
| Routes implemented | health; projects create/list/get/status/delete; preview; reference-image upload; HITL get/submit/history; static `/preview/{id}/…` |
| Routes specified but **absent** | SSE `/events`, `POST /messages`, `/build`, `/files`, `/evaluation`, `/evaluation/fixes`, reference-image list/fetch, all `/external-actions` |
| Impeccable | **MANDATORY gate, INSTALLED** (skill 4.3.1, CLI 4.0.0) and running |
| MCP | discovery client exists; **no server connected** |
| Zapier | optional automation layer, **deferred** — not the database, not the orchestrator |

### Database & authentication

| Area | Status |
|---|---|
| Architecture | **Supabase Auth + PostgreSQL, multi-user, RLS-enforced.** Approved 2026-10-09 — `database.md` is the authority |
| Connection | **LIVE AND VERIFIED.** PostgreSQL 17.11 over the Supabase session pooler (port 5432); authenticated query succeeded, prepared statements supported |
| Configuration | `DATABASE_URL` plus four `SUPABASE_*` variables declared in `backend/config/settings.py`, all secrets as `SecretStr` |
| Driver | `psycopg` 3.3.6 **installed in `.venv` only** — deliberately **not yet in `backend/requirements.txt`** |
| Migrations | `0001_application_schema.sql` and `0002_app_role_and_policies.sql` — **both applied** |
| Tables | `public`: 6 application tables, RLS enabled and forced, 14 policies. `langgraph`: the checkpointer's own 4, in a separate schema with no grants to `anon`/`authenticated` |
| Auth implementation | **backend DONE.** ES256/JWKS verification, caller-identity dependency, owner-scoped service and repository, all 10 project routes protected |
| Backend DB role | **`detroit_app`** (`NOBYPASSRLS`) for application queries; `postgres` only for migrations and the checkpointer |
| Frontend auth | **DONE AND ACTIVE** (verified 2026-10-11) — `frontend/.env` present with both `VITE_` keys, gitignored and untracked. Guards, bearer token and sign-out all live |
| Isolation | verified: 34/34 assertions, two real users, real RLS, over the real HTTP API |
