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
| `runtime.md` | Python version, dependencies, `.env`, Claude model configuration, config validation |
| `frontend.md` | v1 frontend scope, the Landing Page rule, **React Router** specification |
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

| Area | Status |
|---|---|
| Landing Page | built and approved |
| Pricing Page | specified, not built |
| Chatbot Page | specified, not built |
| Frontend routing | specified, **not installed or implemented** |
| Backend | fully specified, **no code written** |
| Database | **Supabase / PostgreSQL decided**; schema undecided by design — no DB code or driver until the user defines it |
| MCP | specified, **no server connected** |
| Impeccable | **MANDATORY gate, not installed** → the Evaluation stage is **BLOCKED** until it is installed (`design.md` §5) |
| Zapier | optional automation layer, **deferred** — not the database, not the orchestrator |
