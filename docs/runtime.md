<!-- Purpose: Define the backend runtime and configuration contract — Python version, dependency management, environment variables, Anthropic client configuration and config validation. -->

# Runtime & Configuration

> Nothing in this document has been created yet. No dependency file, no `.env`, no
> `.env.example` exists. Creating any of them requires explicit user approval.

## 1. Python

| | |
|---|---|
| Target version | **Python 3.11** |
| Supported | 3.11 – 3.12 |
| Existing environment | `.venv/` at the repository root (currently contains only `pip 24.3.1`) |

3.11 is chosen because LangGraph, LangChain and the Anthropic SDK all support it
well, it is widely available on Windows, and the existing venv is already in place.

## 2. Dependency Management

**Approach: `requirements.txt` + `pip`, installed into the existing `.venv`.**

Chosen over `pyproject.toml`/`uv` for v1 because the venv already exists, the project
is a single application (not a distributable package), and it keeps the Windows
workflow simple. Migration to `pyproject.toml` later is straightforward and does not
change any code.

### Expected direct dependencies

| Package | Why |
|---|---|
| `fastapi` | HTTP layer |
| `uvicorn[standard]` | ASGI server |
| `pydantic` | schemas, agent output validation |
| `pydantic-settings` | typed configuration from environment |
| `python-multipart` | reference-image uploads |
| `langgraph` | workflow graph, interrupts, checkpointing |
| *(checkpointer package)* | **undecided** — see §2.1 |
| `langchain-core` | prompts, structured output, runnables |
| `langchain-anthropic` | Claude provider binding |
| `sse-starlette` | SSE event stream |
| `httpx` | outbound HTTP where needed |

Deliberately **not** included in v1: any second model provider, any vector store, any
document loader, any ORM. RAG is deferred (`CLAUDE.md` §22).

Every package above needs explicit approval before installation. Versions will be
pinned in the manifest when it is created.

### 2.1 Deferred dependencies — do NOT add speculatively

| Dependency | Status | Unblocked by |
|---|---|---|
| Supabase client / Postgres driver / migration tool | **deferred** | the user defining the database schema (`docs/architecture.md` §3.8) |
| Checkpointer package | **undecided** | choosing the interim checkpointer (`docs/state.md` §5.1) |
| Impeccable | **required and installed** (skill 4.3.1, CLI 4.0.0) | — nothing to add; it is not a Python dependency (`docs/design.md` §5.2) |
| Zapier client / webhook tooling | **deferred** | the user enabling the automation layer (`docs/architecture.md` §3.9) |
| Anything else | not in the documented architecture | an explicit decision |

No speculative backend feature or dependency beyond the documented architecture.

## 3. Environment & Secrets

Configuration comes from environment variables, loaded through a single
`pydantic-settings` object in `backend/config/`. No module reads `os.environ`
directly.

### 3.1 Required `.gitignore` change (pre-implementation)

The repository's `.gitignore` currently lists `node_modules`, `dist`, `dist-ssr`,
`*.local`, `.impeccable/mocks`, `.impeccable/config.local.json`, `*.tsbuildinfo`,
`.venv`, `Refrence_images`.

It does **not** ignore `.env`. Before any backend file is created, add:

```text
.env
var/
```

- `.env` — prevents committing secrets (`docs/security.md` §2).
- `var/` — runtime data: checkpointer database, project artifacts, generated
  workspaces, uploaded references.

Separately, `Refrence_images` is currently ignored, which means the approved Chatbot
reference is untracked and absent from a fresh clone. The Reference Analysis stage
depends on it. Recommended: stop ignoring at least
`Refrence_images/chatbot_refrence.png`. This is a user decision.

### 3.2 Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | — | provider credential; **secret** |
| `ANTHROPIC_MODEL` | no | `claude-sonnet-5` | default model id |
| `ANTHROPIC_MODEL_HEAVY` | no | `claude-opus-5` | optional model for heavy-reasoning roles |
| `APP_ENV` | no | `development` | `development` \| `production` |
| `LOG_LEVEL` | no | `INFO` | logging verbosity |
| `API_HOST` | no | `127.0.0.1` | **must stay loopback while there is no auth** |
| `API_PORT` | no | `8000` | API port |
| `ALLOWED_ORIGINS` | no | `http://localhost:5173` | CORS allowlist |
| `BUILDER_DATA_DIR` | no | `./var` | root for checkpoints, artifacts, workspaces |
| `MAX_UPLOAD_BYTES` | no | `10485760` | 10 MB reference-image cap |
| `ALLOWED_UPLOAD_TYPES` | no | `image/png,image/jpeg,image/webp` | upload allowlist |
| `SESSION_TTL_MINUTES` | no | `1440` | session idle lifetime (24h) |
| `MAX_AUTO_ITERATIONS` | no | `3` | automatic correction limit |
| `PREVIEW_BUILD_TIMEOUT_SECONDS` | no | `180` | build step timeout |
| `ENABLE_PROMPT_CACHING` | no | `false` | see §5 |
| `MCP_GITHUB_ENABLED` | no | `false` | GitHub MCP connection |

**Database (deferred — do not add until the schema is defined):** Supabase/Postgres
connection variables are intentionally absent. They are added when the user defines
the schema (`docs/architecture.md` §3.8). Whatever they turn out to be, the
connection string and service key are **secrets** and follow §3.4.

**Impeccable has no enable/disable flag.** It is a mandatory gate, not a feature
toggle (`docs/design.md` §5). The runtime *probes* whether it is available; if it is
not, the Evaluation stage reports `blocked` and stops. A flag that could silently turn
the gate off must never be added.

### 3.3 `.env.example`

An `.env.example` file will be created (on approval) containing every variable above
with **placeholder values only** — never a real key. `.env` is the real, ignored file.

### 3.4 Key loading rules

- Read once at startup into the settings object.
- Typed as a Pydantic `SecretStr` so `repr`/`str` redacts it — a stray log line or
  traceback cannot print it.
- Never included in an API response. `/health` reports `model_configured: bool` only.
- Never passed into a generated project's build environment.
- Never written into generated files, prompts sent as content, or commits.

## 4. Claude Model Configuration

Single provider: **Anthropic Claude**. No multi-provider abstraction in v1
(`CLAUDE.md` §7, `skills.md` §10). One API key serves every role; each invocation
still costs tokens.

### 4.1 Per-role policy

| Role | Temperature | Max tokens | Rationale |
|---|---|---|---|
| Requirement Analyzer | 0.0 | 4 000 | structured extraction — determinism matters |
| Planner | 0.4 | 8 000 | structured but needs judgment |
| Design Director | 0.6 | 8 000 | the most genuinely creative role |
| Reference Analyzer | 0.2 | 8 000 | observation, not invention |
| Frontend Architect | 0.2 | 8 000 | structural decisions |
| Builder | 0.2 | 16 000 | code generation — favour consistency |
| Visual & UX Critic | 0.3 | 6 000 | judgment with consistency |
| Anti-Slop Reviewer | 0.2 | 6 000 | checklist-driven |
| Accessibility Reviewer | 0.0 | 6 000 | objective criteria |
| Code Reviewer | 0.0 | 8 000 | objective criteria |
| Fixer | 0.1 | 12 000 | minimal targeted edits |
| GitHub Operator | 0.0 | 4 000 | tool invocation only |

These are defaults, centralized in config — not scattered across agent modules. Model
selection per role defaults to `ANTHROPIC_MODEL`; a role may opt into
`ANTHROPIC_MODEL_HEAVY` through configuration, never hard-coded.

### 4.2 Structured output

Every agent returns a Pydantic-validated object (`docs/agents.md` §3). A schema
validation failure is a retryable agent failure recorded in state — never silently
accepted and never patched up by guessing.

## 5. Prompt Caching *(optional, off by default)*

`ENABLE_PROMPT_CACHING=false` in v1.

When enabled, the cacheable prefix is the stable part of a request: the role
instruction block, the approved `design_spec`, and the approved `plan` — all of which
repeat unchanged across the evaluation and iteration loops, where the same context is
sent several times.

Not cached: per-call task input, findings, file contents.

This is a cost optimization, not a correctness mechanism. It must never change agent
behavior, and it stays off until measured.

## 6. Configuration Validation

- Validated **once at startup** by the settings model. Fail fast with a clear message
  naming the missing or invalid variable — never the value.
- `ANTHROPIC_API_KEY` absent → the app refuses to start.
- `API_HOST` non-loopback while auth is absent → startup warning (`docs/security.md`
  §9).
- `BUILDER_DATA_DIR` must be creatable and writable; checked at startup.
- `MAX_AUTO_ITERATIONS` must be ≥ 1; the documented default is 3.
- Invalid enum values (`APP_ENV`, `LOG_LEVEL`) fail validation rather than silently
  falling back.

## 7. Running It (once implemented)

```text
backend:   uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
frontend:  npm --prefix frontend run dev        (port 5173, via .claude/launch.json)
```

The frontend proxies or calls `http://127.0.0.1:8000/api/v1` directly, subject to the
CORS allowlist.
