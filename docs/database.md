<!-- Purpose: Own the authentication and database architecture decision for DETROIT, and design the PostgreSQL/Supabase schema that follows from it. Design only — no migrations, no client, no connection settings beyond the DSN. -->

# Database & Authentication Architecture

**Status: MULTI-USER, AUTHENTICATED. Approved 2026-10-09.**

This supersedes the single-user decision this document previously recorded. The
product is no longer a local single-user service: **Supabase Auth is the
authentication system, every application resource is user-scoped, and
row-level security enforces that scoping in the database.**

This file is the authority for that decision. Where another document still
describes v1 as unauthenticated or single-user, this one wins and that document
is stale — the list of known contradictions is in §7.

**What is approved, and what is not:**

| | State |
|---|---|
| The architecture decision in §1 and §1.3 | **approved** |
| Connection settings (`DATABASE_URL` in `backend/config/settings.py`) | **implemented** |
| The DDL in §3 | **superseded — predates ownership, see §3.0** |
| Migrations, RLS policies, auth middleware, any code | **not started** |

**Durable persistence target: PostgreSQL, provided by Supabase.** LangGraph uses
its official PostgreSQL checkpointer; Supabase supplies the Postgres instance.
This supersedes the interim `InMemorySaver` recorded in `docs/state.md` §5.1,
which stays in place until the implementation lands.

Every table below maps to something the backend already has. Nothing is designed
for a feature that does not exist.

## 1. Four separate concerns

They are listed together because they are easy to conflate, and conflating them
is how secrets end up in the wrong place.

| | Concern | Owner | Where it lives | Who may read it |
|---|---|---|---|---|
| **1** | Application data | this document | schema `public` | the owning user, through RLS |
| **2** | LangGraph checkpoints | **LangGraph** | schema `langgraph` — *we do not design these tables* | **backend only** — never a browser |
| **3** | Auth / user identity | **Supabase Auth** | schema `auth` — **the identity source** | Supabase; we read `auth.uid()` only |
| **4** | Live approval tokens | `ExternalActionService` | **in-memory only — never the database** | the issuing process, for one action |

The fourth column is new and is the point of the table. Three of these concerns
live in the same Postgres instance, which is reachable from a browser through
Supabase's data API — and of those three, **only `public` should ever answer a
request from one.** `langgraph` is not exposed today because Supabase's data
API serves an explicit schema list; that is a setting, which means it is
something to verify rather than assume. Getting it wrong is how a checkpoint
containing another user's prompt becomes readable with a publishable key.

Concern 3 is the exception that proves the rule: the browser does talk to
Supabase Auth, but to its **API**, never to the `auth` schema's tables.

### 1.2 LangGraph checkpoints are not ours to design

`langgraph-checkpoint-postgres` creates and migrates its own tables
(`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`).
Designing them here would be inventing a schema for a library that owns one.

Two facts matter for us:

- **`thread_id` is the `project_id`** (`docs/state.md` §3). That is the join
  between a checkpoint and a row in `projects`.
- The checkpointer is **authoritative for resumption**; application tables are
  authoritative for human review (`docs/state.md` §5.1). On disagreement the
  checkpoint wins.

Put them in their own schema so ownership is visible and a migration of ours can
never touch them.

### 1.3 Supabase Auth — the identity source

**Decided: adopted.** Supabase Auth owns identity. DETROIT does not implement
password hashing, session issuance, email verification or password reset,
because Supabase already does all of it and a second implementation would be
strictly worse.

#### 1.3.1 No `public.users` table

**Decided: none.** `auth.users.id` (a `uuid`) **is** the user identity.
Application tables reference it directly.

A `public.profiles` table is the usual Supabase pattern, and it is deliberately
not adopted here, because a profile table is only worth its join when there is
profile data to put in it. Today there is none: `auth.users` already carries
the email, and the display name, plan and billing fields currently rendered on
the Dashboard are placeholder content with no backend source behind them.

**The trigger to revisit:** the first product requirement for a user attribute
that Supabase does not already store — a display name the user can edit, a
plan, a team, a preference. At that point `public.profiles` is added with
`user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE`, and it
holds only the new attributes. It never duplicates the email.

#### 1.3.2 The ownership model

Every row of application data belongs to exactly one user.

- **`projects` is the ownership root.** It gains an owner column referencing
  `auth.users(id)`. That column is `NOT NULL`: a project with no owner is
  unreachable under RLS and would be invisible, undeletable garbage.
- **Every child table inherits ownership through `project_id`.** The audit
  tables and `reference_images` already cascade from `projects`, so they do
  **not** get their own owner column. A second copy of ownership is a second
  thing to keep consistent, and the two copies disagreeing is a silent
  authorization bug.
- **Ownership is immutable in v1.** There is no transfer, no sharing and no
  team. Those are a later design, and each one needs its own policy work.
- **Ownership is assigned server-side**, from the verified token, never from a
  field in the request body. A client that can name its own owner can claim
  anyone's project.

#### 1.3.3 RLS is required, not optional

**Every table in `public` has row-level security enabled, with a policy keyed
on `auth.uid()`.** This is a hard requirement, for a specific reason:

> Ownership and RLS must land in the same change. A tenant column without RLS
> is worse than no tenant column, because the schema now *looks* multi-tenant
> and every reader assumes isolation that is not being enforced.

Two consequences that are easy to get wrong:

- **RLS must be enabled on child tables too.** A policy on `projects` alone
  does not protect `hitl_records`; Postgres evaluates policies per table, and a
  child table with RLS off is readable by anyone with a key.
- **The backend's own connection must not quietly bypass RLS.** The service
  role key and the `postgres` superuser both ignore policies. Which identity
  the backend connects as — and therefore whether RLS is a real boundary or
  decoration — is an open decision, recorded in §5 #7.

#### 1.3.4 Key handling: publishable in the browser, secrets on the server

| Credential | Lives | May reach a browser |
|---|---|---|
| `SUPABASE_URL` | frontend + backend | **yes** — not a secret |
| `SUPABASE_PUBLISHABLE_KEY` (anon) | frontend + backend | **yes** — it is designed for this, and is safe *only because RLS is enabled* |
| `SUPABASE_SERVICE_ROLE_KEY` | **backend only** | **never** — it bypasses RLS entirely |
| `SUPABASE_DB_PASSWORD` / `DATABASE_URL` | **backend only** | **never** |

The publishable key is public by design: it identifies the project, not the
caller, and the caller's rights come from their JWT evaluated against RLS. That
is exactly why §1.3.3 is non-negotiable — **with RLS off, the publishable key
is a full read of every user's data**, and it is sitting in a JavaScript
bundle.

The service role key is the mirror image: it bypasses RLS completely, so it
must never be bundled, never be sent to the browser, and never be used to serve
a user-facing read that RLS could have served instead
(`docs/security.md` §2, §7).

#### 1.3.5 Where the token is verified

The frontend authenticates against Supabase Auth and receives a JWT. It sends
that JWT to the DETROIT backend, which **verifies it** and derives the user id
from it.

The backend does not trust a user id sent in a path, a query string or a body.
It derives identity from the verified token and from nothing else. How that
verification is implemented — Supabase's JWKS, a shared secret, or the Supabase
client library — is an implementation decision for `docs/api.md`, not this
file; what this file fixes is that identity comes from the token.

### 1.4 Approval tokens never reach the database

`backend/services/external_actions.py` holds live plans in process memory and
stores the token only as a SHA-256 digest. That stays. A short-TTL single-use
token written to a durable table would outlive its TTL in backups and logs, and
`docs/mcp.md` §5 treats approval as a per-action, momentary thing.

The database records the **outcome** of an external action, never permission to
perform one.

## 2. Why `ProjectState` is not shredded into columns

`docs/state.md` §3: *"`ProjectState` **is** the LangGraph graph state. There is
no second model and no translation layer."*

Decomposing all 33 fields into relational columns would create exactly that
second model: every stage output would need a table, a mapper and a migration
whenever a Pydantic field changed, and the two definitions would drift.

So the design splits on **why a field is read**:

| Kind | Storage | Reason |
|---|---|---|
| Identity and status the API filters or sorts by | **columns** | `GET /projects` must list without parsing JSON |
| Structured stage outputs (plan, design spec, architecture…) | **`jsonb`** | one source of truth — the Pydantic model |
| Append-only audit lists | **child tables** | insert-only is enforceable, and audit is queried |

`schema_version` is stored so a row written by an older model is detectable
rather than silently reinterpreted — the same guarantee
`SchemaVersionMismatchError` gives today.

## 3. Application schema

### 3.0 The DDL below is SUPERSEDED — read this first

**The SQL in §3.1–§3.3 predates the multi-user decision and must not be
executed as written.** It was designed for a single-user backend, so:

- `projects` has **no owner column**, which §1.3.2 now requires.
- **No table has RLS enabled**, which §1.3.3 now requires on all of them.

No corrected DDL is written here, deliberately. This step records the
architecture decision; the schema revision is a separate, approved piece of
work, and a half-updated `CREATE TABLE` sitting next to the real one is how the
wrong migration gets run.

What the revision must produce, so the requirement is unambiguous:

1. `projects` gains a non-null owner column referencing `auth.users(id)`, with
   an index on it, since every list query filters by it.
2. `ALTER TABLE … ENABLE ROW LEVEL SECURITY` on **every** table in `public` —
   `projects` and all four child tables and `reference_images`.
3. A policy per table keyed on `auth.uid()`: direct on `projects`, and via an
   `EXISTS` against the owning project for the children.
4. The decision in §5 #7 resolved first, because connecting as a role that
   bypasses RLS makes steps 2 and 3 cosmetic.

Everything else in §3.1–§3.3 — the column set, the `jsonb` split, the
append-only child tables, the cascade behaviour — stands unchanged.

### 3.1 `projects`

One row per `ProjectState`. Columns are the fields `ProjectSummary` and
`StatusResponse` actually read.

```sql
CREATE TABLE projects (
    project_id      text PRIMARY KEY,          -- 'proj_' + 32 hex
    session_id      text        NOT NULL,
    title           text        NOT NULL,
    user_request    text        NOT NULL,
    schema_version  integer     NOT NULL,
    current_stage   text        NOT NULL,      -- WorkflowStage value
    run_status      text        NOT NULL,      -- RunStatus value
    iteration_count integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,

    -- Structured stage outputs, each the dump of one Pydantic model.
    normalized_requirements jsonb,
    plan                    jsonb,
    design_spec             jsonb,
    reference_analysis      jsonb,
    frontend_architecture   jsonb,
    component_tree          jsonb,
    evaluation_results      jsonb,
    build_status            jsonb NOT NULL,
    preview_status          jsonb NOT NULL,
    pending_checkpoint      jsonb,             -- null unless awaiting HITL
    last_error              jsonb,

    -- Lists that are REPLACED wholesale each run, not appended.
    requirement_conflicts   jsonb NOT NULL DEFAULT '[]',
    requirement_gaps        jsonb NOT NULL DEFAULT '[]',
    generated_files         jsonb NOT NULL DEFAULT '[]',
    file_versions           jsonb NOT NULL DEFAULT '{}',
    approved_reference_direction text,
    next_action             text
);

CREATE INDEX projects_updated_at_idx ON projects (updated_at DESC);
CREATE INDEX projects_run_status_idx ON projects (run_status);
```

`node_scratch` is **absent by design**. It is excluded from serialization today
(`exclude=True`) and `docs/state.md` §2 forbids persisting it.

### 3.2 Append-only audit tables

`docs/state.md` §3 names `hitl_history`, `approved_decisions`,
`rejected_options` and `mcp_action_history` as append-only. As tables, "append
only" becomes something the database can enforce rather than a convention.

```sql
CREATE TABLE hitl_records (
    project_id    text        NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    checkpoint_id text        NOT NULL,
    stage         text        NOT NULL,
    action        text        NOT NULL,       -- HitlAction value
    option_id     text,
    instruction   text,
    decided_at    timestamptz NOT NULL,
    PRIMARY KEY (project_id, checkpoint_id, decided_at)
);

CREATE TABLE approved_decisions (
    project_id  text        NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    decision_id text        NOT NULL,
    stage       text        NOT NULL,
    statement   text        NOT NULL,
    origin      text        NOT NULL,         -- HitlAction value
    decided_at  timestamptz NOT NULL,
    PRIMARY KEY (project_id, decision_id)
);

CREATE TABLE rejected_options (
    project_id    text        NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    checkpoint_id text        NOT NULL,
    option_id     text        NOT NULL,
    stage         text        NOT NULL,
    rejected_at   timestamptz NOT NULL,
    PRIMARY KEY (project_id, checkpoint_id, option_id)
);

CREATE TABLE external_action_records (
    project_id   text        NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    action_id    text        NOT NULL,
    action_type  text        NOT NULL,        -- ExternalActionType value
    status       text        NOT NULL,        -- 'succeeded' | 'failed'
    performed_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, action_id)
);
```

`hitl_records` is keyed on `decided_at` as well as `checkpoint_id` because one
checkpoint can legitimately be answered more than once across a revise loop.

`external_action_records` carries **no token, no credential and no repository
URL** — exactly the four fields `McpActionRecord` has today (`docs/mcp.md` §6:
log action type, target, outcome, timestamp, never the credential).

### 3.3 `reference_images`

Metadata only. Bytes stay out of the database.

```sql
CREATE TABLE reference_images (
    project_id   text        NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    image_id     text        NOT NULL,        -- 'img_' + 32 hex
    content_type text        NOT NULL,        -- magic-byte verified
    size_bytes   integer     NOT NULL,
    sha256       text        NOT NULL,
    uploaded_at  timestamptz NOT NULL,
    PRIMARY KEY (project_id, image_id)
);
```

## 4. What stays on the filesystem

Not everything belongs in Postgres, and saying so is part of the design.

| Data | Stays where | Why |
|---|---|---|
| `workspace/` — generated site source and `dist/` | local filesystem | `npm`, `tsc` and `vite` need a real directory; a build cannot run against a table |
| Reference image **bytes** | **local filesystem (decided for v1)** | binary blobs in rows bloat backups, and the checksum is already in the table. Supabase Storage is not introduced |
| `var/projects/{id}/*.json` artifacts | **local filesystem (decided for v1)** | kept as the local export/review representation; the database does not replace them |

## 5. Decisions

Each entry records what was decided and what follows from it, so a later reader
does not reopen a settled question. #7 is **open** and blocks implementation.

| # | Question | Decision | Consequence |
|---|---|---|---|
| 1 | JSON artifacts | **Keep for v1** | They remain the local export/review representation. Database rows do not replace them, and nothing deletes them |
| 2 | Multi-user / auth | **ADOPTED — Supabase Auth** | Identity is `auth.users.id`. No `public.users` table (§1.3.1). `projects` is owner-scoped, children inherit, RLS on every `public` table (§1.3.2, §1.3.3). Supersedes the previous "defer" |
| 3 | Checkpoint retention | **Defer** | LangGraph's Postgres checkpointer stays authoritative and keeps its own history. No pruning is designed |
| 4 | Optimistic locking | **Defer** | Still one writer *per project*: ownership is exclusive and there is no sharing, so concurrent users do not contend. See the revised assumption in §5.1 |
| 5 | Reference images | **Filesystem for v1** | Bytes stay local. Supabase Storage is not introduced; only metadata is in `reference_images`. Multi-user adds a condition — see §5.1 |
| 6 | Connection model | **PostgreSQL via Supabase** | Approved as the durable production target. LangGraph uses its official PostgreSQL checkpointer; Supabase provides the instance |
| **7** | **Which database role the backend connects as** | **RESOLVED 2026-10-09 — a dedicated `detroit_app`** | Migration 0002. `NOBYPASSRLS`, granted only on the six `public` tables, claims set per transaction. `anon`/`authenticated` lose all table access. Verified against the live database |

### 5.1 What each deferral assumes

Deferrals are conditional, and the conditions are worth stating:

- **Single-writer (4)** survives the move to multi-user, but for a narrower
  reason than before. It no longer holds because there is one user; it holds
  because **ownership is exclusive and nothing is shared**, so two people
  cannot write the same project. Sharing, teams, or a worker pool each break it
  independently. Those are the triggers, not user count.
- **No retention (3)** means a long-running project's checkpoint history grows
  without bound. Multi-user makes this arrive sooner — the growth is now
  per-user — but it is still a storage question before a correctness one.
- **Keeping artifacts (1)** means the same data exists in two places. The
  precedence rule in `docs/state.md` §5.1 already settles disagreements: the
  checkpointer wins for resumption, artifacts are for human review.
- **Filesystem images and workspaces (5)** were safe when one person owned the
  machine. They are now **outside RLS**: a path is protected by whatever
  resolves it, not by a policy. The existing containment check
  (`docs/security.md` §3.4) keeps a request inside *a* project directory; what
  it does not yet do is confirm the requester owns that project. That check
  belongs in the API layer and must land with ownership.

### 5.2 Decision #7 — RESOLVED: a dedicated `detroit_app` role

**Decided 2026-10-09 and implemented in migration 0002.** Option 3 below was
chosen, with one simplification the frontend decision unlocked: because the
browser never queries Postgres directly, policies target `detroit_app` itself
rather than `authenticated`, so no per-transaction `SET ROLE` is needed — only
the claim. The history is kept because the trade-offs still explain the shape.

RLS is evaluated against the connected role and `auth.uid()`. The backend
currently connects as `postgres` via the session pooler — a superuser, which
**ignores every policy**. Three ways forward, each with a cost:

1. **Backend enforces, RLS is defence in depth.** The backend keeps its
   privileged connection and filters by owner in every query. Simplest, and it
   makes RLS a safety net rather than the boundary — one forgotten `WHERE`
   leaks data.
2. **Backend assumes the user's identity per request** (`SET LOCAL ROLE` /
   `request.jwt.claims`). RLS becomes the real boundary. Costs connection
   hygiene: a pooled connection that keeps a previous request's identity is a
   cross-user leak, so the reset must be airtight.
3. **A dedicated non-superuser role** for the backend, with RLS applying to it.
   Cleanest separation; needs role and grant management that does not exist yet.

Whichever is chosen, **the frontend's publishable-key path always goes through
RLS** (§1.3.4), so policies are required regardless of #7. #7 only decides
whether the *backend's* path is also covered.

## 6. What this design does not change

- The `StateRepository` Protocol in `backend/memory/`. Postgres arrives as a
  second implementation behind the same eight methods; the graph and the API do
  not learn it exists (`docs/state.md` §5.0).
- The LangGraph graph, its nodes, or HITL interrupt/resume.
- Where approval tokens live. They stay in process memory as a SHA-256 digest
  (§1.4). Multi-user does not change this; it strengthens the reason, since a
  durable token table would now span users.
- The separation in §1: application tables and LangGraph checkpoints remain
  distinct concerns with distinct owners. Checkpoints are **not** given an
  owner column and **not** given RLS, because they are LangGraph's tables
  (§1.2) — they are protected by never being reachable from a browser at all.
  The join to a user is `thread_id` → `projects.project_id` → owner.

### 6.1 What it DOES change, and is not yet resolved

Stated plainly so it is not mistaken for settled:

- **`ProjectState` and the repository interface.** Listing "my projects"
  requires the owner to reach the query. Whether the owner lives on
  `ProjectState`, or is passed beside it as a caller identity, is a
  `docs/state.md` decision — not this file's. It is open.
- **The eight `StateRepository` methods** keep their signatures only if the
  identity travels some other way. If it must become a parameter, the Protocol
  changes, and §6's first bullet weakens.

## 7. Contradictions this decision creates elsewhere

### 7.1 Documentation — RESOLVED 2026-10-09

Every documentation contradiction listed here has been fixed. The
documentation set now describes the authenticated multi-user architecture and
the real implementation status.

| File | What was fixed |
|---|---|
| `docs/README.md` | Authority table gained a `database.md` row. The status table was rewritten from scratch: it claimed "Backend — no code written" and "Frontend routing — not implemented", both long false. Now split into Frontend / Backend / Database sections with observed status |
| `docs/api.md` §1.1 | "No authentication in v1" replaced by the Supabase Auth contract: bearer JWT, server-side verification, owner-scoped `/projects`, and **`404` not `403`** for another user's project, so the id is not a membership oracle |
| `docs/api.md` §2, §5, §6 | Added an implementation-status banner naming which routes exist; added `unauthenticated` (401) and `forbidden` (403) codes; replaced "only the Chatbot Page calls the backend", which `/dashboard` had already made false |
| `docs/security.md` §2 | `.env` gitignore item marked done, with the trap that the pattern does not cover `.env.bak`. Added the four-key table of what may reach a browser, and the percent-encoding rule for DSN passwords |
| `docs/security.md` §9 | Reframed: loopback is a *consequence* of no auth, and lifting it needs all three of auth implemented, RLS enabled, and §5.2 #7 resolved |
| `docs/state.md` §5.0 | "Schema undecided, no database code" removed. Added verified status, the open interface decision (§5.0.1), and the Windows `ProactorEventLoop` constraint (§5.0.2) |
| `docs/state.md` §5.1 | Retitled: the interim layers are **still what runs**, which the old "until the schema is defined" framing obscured |
| `docs/architecture.md` §3.8 | "Not decided" and the driver prohibition removed; records the three-schema architecture and names `database.md` as authority |
| `docs/runtime.md` §2, §2.1 | Dependency tables corrected — `psycopg` and `langgraph-checkpoint-postgres` moved from deferred to unblocked-but-undeclared; ORM explicitly refused; Supabase client still deferred |
| `docs/runtime.md` §3.1, §3.2.1 | `.gitignore` marked done; added the five database variables with validation rules, the session-pooler requirement, and the Windows note |
| `docs/frontend.md` §1, §4.1, §4.6, §6 | Scope table rebuilt around the six real routes; §1.1 added for the auth work; the SSE and `POST /messages` gaps recorded; four new debt items |
| `CLAUDE.md` §2 | Added a note that the frontend-first rule governs **generated sites**, not DETROIT's own backend, database and auth |
| `CLAUDE.md` §9A | Retitled to include authentication. The driver prohibition is marked lifted, with current status and what remains binding |
| `CLAUDE.md` §28 | Surface table rebuilt with the six real routes and their true status |
| `skills.md` §8, §22 | FastAPI auth responsibility moved from "if later enabled" to in-scope; §22 scoped explicitly to generated sites |

`PRODUCT.md` needed no change: it already carries a legacy banner and is
explicitly non-authoritative.

### 7.2 Code — OUTSTANDING

None of this has been touched. Each entry is a separate approved change.

| Location | What it asserts | Needs |
|---|---|---|
| `backend/api/projects.py` | every route takes `project_id` with no caller identity | an auth dependency and owner filtering |
| `backend/main.py` | CORS `allow_credentials=False`, justified by "no authentication in v1" | a bearer token in a header needs no credentialed CORS, so this may stand — but as a decision, not an oversight |
| `backend/config/settings.py` | `startup_warnings()` cites "no authentication in v1" | wording, once auth exists |
| `backend/memory/` | `StateRepository` has no notion of an owner | blocked on the open decision in §6.1 / `docs/state.md` §5.0.1 |
| `backend/requirements.txt` | `psycopg` absent; "Impeccable — REQUIRED but not installed" is **factually wrong**, it is installed and running; the checkpointer is listed as undecided | not a `.md` file, so out of scope for this pass — fix it with the persistence work |
| `frontend/src/lib/api.ts` | "no authentication in v1 … no `Authorization` header" | attach the JWT |
| `frontend/src/pages/chat.tsx`, `dashboard.tsx` | account blocks read "Local workspace / No sign-in required" | the real signed-in user |
| `frontend/src/components/ui/auth-ui.tsx`, `pages/login.tsx` | deliberate placeholder: "submits nothing and stores nothing", not nav-linked | wire to Supabase Auth; remove the notices |

### 7.3 What has NOT changed and must not drift

- LangGraph's checkpoint tables stay LangGraph's (§1.2). No owner column, no
  RLS, no hand-written DDL.
- Approval tokens stay in memory (§1.4).
- Generated sites still get no invented backend (`CLAUDE.md` §2). DETROIT
  having auth does not mean the pages it builds do.
