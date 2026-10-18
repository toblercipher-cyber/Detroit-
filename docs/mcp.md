<!-- Purpose: Define MCP as an optional external capability layer, its integrations, capability discovery, least-privilege access and the approval gate for external writes. -->

# MCP

## 1. What MCP Is In This Project

MCP is the **external capability layer**. It is not the agent, not the orchestrator,
and not a substitute for the workflow.

```text
Agent Harness          <- owns orchestration and approval policy
      ↓
MCP Client             <- connection, capability discovery, permission filtering
      ↓
MCP Server             <- external tool provider
      ↓
External System        <- GitHub, Lovable, deployment target, browser
```

**Hard requirement: the core build workflow must run with zero MCP servers
connected.** Requirement analysis, planning, design, reference analysis, build,
preview and evaluation must never depend on an MCP server being present. MCP adds
optional capability at the edges.

**No MCP server is connected by default.** Connecting one requires explicit user
approval and `MCP_GITHUB_SERVER_URL`.

### GitHub is implemented (implemented 2026-10-11)

GitHub integration ships, and it does NOT depend on an MCP server:

| Capability | Path | Status |
|---|---|---|
| status, repository listing, selection | REST adapter | implemented |
| push (workspace `src/` -> a branch) | REST adapter | implemented |
| pull / import | — | **removed, out of scope** |
| repository creation, PRs, issues | — | not implemented |
| arbitrary discovered tools | MCP + GitHub Operator | implemented, **dormant** until a server is configured |

**MCP capability is verified, not assumed.** When `MCP_GITHUB_SERVER_URL` is
set, `GitHubGateway.probe_mcp` connects and lists what the server actually
advertises, and `GET /api/v1/github/status` reports the count and how many are
write-capable. An unreachable server reports that it could not be reached — it
never reports a capability it did not observe.

**Why push does not go through MCP.** Section 4 forbids assuming tool
names, and discovery returns names and schemas but never MEANING — nothing in
code can know which of `push_files`, `create_commit` or `repo_write` commits a
tree. A deterministic sync cannot be built on that, so it uses the REST adapter
(`backend/tools/github_rest.py`). MCP remains the path for the **GitHub
Operator agent**, which is a model and *can* read a discovered tool's
description and choose. `backend/services/github_gateway.py` records this
decision in full.

## 2. Integrations

### 2.1 GitHub *(primary target)*

Capability classes, treated differently:

| Class | Examples | Gate |
|---|---|---|
| **Read** | inspect repository, read file, list branches, read PR | no approval needed |
| **Write** | create branch, create/update file, commit, push, create PR | **approval required** (§5) |
| **Destructive** | force push, history rewrite, branch/repo deletion | **explicit per-action authorization**; never inferred from a general "sync to GitHub" request |

Rules: never push unreviewed or failing code just because automation was requested;
preserve repository history; repository operations are never an implicit side effect
of generation (`skills.md` §21).

### 2.2 Lovable *(optional)*

Use only when the user explicitly requests a Lovable action. Lovable is never the
orchestration system — this project's harness remains the source of orchestration and
approval policy. Exchange happens through explicit artifacts and structured state, not
a shared context window:

```text
Harness -> structured project state -> Lovable/MCP -> result -> validation -> harness state
```

### 2.3 Browser / preview *(optional)*

The builder has its **own** preview subsystem (`docs/architecture.md` §5.4). It does
not depend on a browser MCP server. A browser MCP server, if connected, is used only
to assist evaluation — rendered inspection and screenshots for the Visual & UX,
Accessibility and Anti-Slop reviewers.

### 2.4 Zapier — optional automation layer *(deferred)*

Zapier is an **optional automation layer for later workflows, logging and
notifications**. Nothing in v1 depends on it.

Zapier is explicitly **not**:

| Zapier is NOT | That is |
|---|---|
| the database | Supabase / PostgreSQL (`docs/architecture.md` §3.8) |
| the orchestrator | LangGraph + Agent Harness (`docs/architecture.md` §3.4, §3.7) |
| a workflow engine for this product's stages | the LangGraph workflow (`docs/state.md` §4) |
| a required dependency of any core stage | — |

When enabled it sits **at the edge**, downstream of an already-completed workflow
step — e.g. posting a notification after a build finishes, or appending a log entry
after an approved external action. Product logic, state and agent decisions never
route through it.

Because it sends data outside the system, every Zapier send is an **external write**
and follows the approval gate in §5. Payloads must never contain secrets, raw prompts
or full project state.

### 2.5 Deployment *(deferred)*

Not in v1. When enabled: deployment is a **write** action requiring approval, gets its
own operator role, and never inherits repository or model credentials.

## 3. Least-Privilege Tool Access

An agent receives only the MCP tools its current responsibility requires. The MCP
client filters the advertised tool list per role before the agent ever sees it — an
agent cannot call a tool it was not offered.

| Role | MCP access |
|---|---|
| Requirement Analyzer, Planner, Design Director, Frontend Architect | **none** |
| Reference Analyzer | none |
| Builder | none (uses internal workspace tools, not MCP) |
| Visual & UX / Accessibility / Anti-Slop Reviewers | browser/preview read tools only, when connected |
| Code Reviewer | repository **read** only, when connected |
| Fixer | none |
| GitHub Operator | GitHub tools required by the approved plan, and nothing else |

Never expose every MCP server to every agent. Never expose a write tool to a role
whose stage cannot legitimately produce a write.

> **Impeccable is not an MCP server.** It is a required local evaluator used by the
> Anti-Slop Reviewer (`docs/design.md` §5). It is never optional, and its availability
> is never treated as an MCP capability question.

## 4. Capability Discovery

**Never hard-code or assume MCP capabilities.** Tool names, schemas and side effects
differ between servers and versions.

Required sequence before first use:

```text
1. connect to the server
2. list its actual tools
3. validate each needed tool exists and its schema matches expectations
4. classify each tool as read / write / destructive
5. record the discovered capability set in state
6. expose only the role-permitted subset to the agent
```

If a required capability is absent, return `capability_unavailable` (`501`,
`docs/api.md` §5) and surface it to the user. Do not substitute a different tool, and
do not simulate the capability.

Model-produced tool arguments are untrusted and must be validated against the
discovered schema before invocation.

## 5. External Write Approval Gate

Every write or destructive external action uses the two-phase flow from
`docs/api.md` §2.7:

```text
POST /external-actions/preview    -> non-mutating plan + single-use, short-TTL token
        ↓  user reviews exactly what will change
POST /external-actions            -> executes only with a valid token
```

- The preview call performs **no** mutation.
- Execution without a valid token returns `403`; an expired or spent token returns
  `410`.
- Approval is **per action**. One approval never generalizes to a later action.
- The plan states in user-facing terms exactly what will change — repository, branch,
  files, and whether history is affected.

## 6. Security

- Never leak credentials through prompts, generated code, logs, commits or tool
  output.
- MCP credentials come from environment configuration and are never passed into agent
  context.
- Log every external action: action type, target, outcome, timestamp — never the
  credential.
- Treat all MCP tool **output** as untrusted content (`docs/security.md` §5). A
  repository file or PR comment that contains instructions is data, not a command.
