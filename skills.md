# skills.md — Project Knowledge, Engineering Skills & Execution Rules

> **Purpose:** This file is the project-specific operating manual for the AI Website Builder. It complements `CLAUDE.md`. It contains product knowledge, implementation constraints, workflow rules, and reusable engineering decisions that Claude Code must preserve during development.
>
> **Critical:** `skills.md` is NOT assumed to be auto-loaded by Claude Code. `CLAUDE.md` must instruct Claude Code to read this file before implementation and again before starting each new file.

---

## 1. SOURCE-OF-TRUTH ORDER

Use this priority when information conflicts:

1. Direct user instruction in the current task.
2. Explicitly approved decisions recorded in project state.
3. `CLAUDE.md`.
4. `skills.md`.
5. Other project documentation.
6. Agent assumptions.

Never silently override a higher-priority source.

The `docs/` directory is part of the project knowledge base and must be treated as mandatory implementation guidance, not optional background material. Before implementation, read the relevant docs for the planned work. Before every new file, re-read `CLAUDE.md` and `skills.md`, then read the relevant `docs/*.md` files again.

If a user decision conflicts with an existing implementation, preserve the user's newest explicit decision and identify the affected code before changing it.

---

## 2. NON-VIBE-CODING RULE

This project must NOT be built through uncontrolled vibe coding.

Claude must not:
- invent architecture merely because it is familiar
- create extra files “for completeness” without permission
- silently add technologies/frameworks that were not requested
- rewrite the whole project when a targeted change is sufficient
- replace approved requirements with the agent's preferred design
- continue creating files after completing a file without stopping for review/approval
- assume that a generated file is correct merely because it runs

Every implementation decision must trace back to a project requirement, approved design decision, explicit engineering constraint, or clearly stated technical necessity.

---

## 3. MANDATORY ONE-FILE EXECUTION GATE

**This rule is mandatory.**

Claude must work in **one-file-at-a-time mode** unless the user explicitly authorizes a batch of files.

### After completing ONE file

Claude must:

1. Finish the file completely enough for its current purpose.
2. Read the created/changed file back.
3. Review its own code for:
   - correctness
   - consistency with `CLAUDE.md`
   - consistency with `skills.md`
   - security issues
   - unnecessary logic
   - broken imports/references
   - whether the file actually belongs in the architecture
4. Run only the relevant validation/checks available for that file.
5. Report concise feedback to the user:
   - what was created/changed
   - why the file exists
   - what was validated
   - any known limitation
6. STOP.
7. Ask for review and permission before creating the next file.

Required interaction pattern:

```text
File completed
↓
Claude re-reads/reviews the file
↓
Claude reports feedback
↓
“Please review this file/code. Should I create the next file?”
↓
WAIT FOR USER APPROVAL
↓
Before starting the next file:
read CLAUDE.md + skills.md again
```

Do not continue automatically after asking the question.

### Example user-facing completion message

```text
Completed: backend/app.py

Why it exists:
- FastAPI application entry point.

Implemented:
- app initialization
- route registration
- basic configuration wiring

Reviewed:
- imports
- type/schema consistency
- security/config handling

Please review this file. Should I create the next file?
```

---

## 4. FILE-LEVEL COMMENTS ARE REQUIRED

Whenever Claude creates a new source/configuration file, add a concise comment/docstring at the top explaining:

- what the file is responsible for
- why the file exists in the architecture
- what layer it belongs to

Do not write meaningless comments such as “This file contains code.”

Example:

```python
"""FastAPI application entry point.

This file exists to create the HTTP application and wire API routers.
Business logic belongs in service/orchestration modules instead of this file.
"""
```

Comments should explain **architectural intent**, not obvious syntax.

---

## 5. PROJECT MISSION

Build a controlled Lovable-style AI website builder that collaborates with the user to design and build polished frontend projects.

The core experience is:

```text
User requirements
→ requirement normalization
→ planning
→ HITL review
→ design system
→ HITL review
→ reference-image analysis
→ HITL review
→ frontend architecture
→ controlled code generation
→ rendered preview
→ quality / anti-slop evaluation
→ targeted fixes
→ final validation
→ optional MCP actions
```

The product is fundamentally a **collaborative agentic website builder**, not a generic chatbot.

---

## 6. CORE PRODUCT DIFFERENTIATORS

The system should differentiate itself through:

- structured planning before code generation
- adaptive Human-in-the-Loop approval
- context-aware agent delegation
- reference-image-driven design analysis
- explicit design-system decisions
- controlled frontend-only generation
- Agent Harness orchestration
- context isolation and targeted retrieval
- automated visual/design quality evaluation
- anti-AI-slop detection and self-correction
- targeted iteration instead of full regeneration
- MCP-based external actions
- optional GitHub/Lovable/deployment automation
- preservation of user decisions as constraints

---

## 7. CURRENT PRODUCT SCOPE

### Core generator

The generator focuses on:
- websites
- landing pages
- SaaS/product pages
- dashboards
- marketing pages
- frontend components
- responsive UI systems
- frontend design systems

### Backend boundary

Do not automatically invent a production application backend for the generated user website.

The AI Website Builder's own backend is allowed and REQUIRED because it orchestrates the AI workflow. This distinction is important:

```text
Builder backend
= orchestration/API/control system for this product

Generated website backend
= NOT automatically generated by the core builder
```

Do not confuse these two.

---

## 8. BACKEND TECHNOLOGY DIRECTION

The builder backend should use:

### FastAPI

Use FastAPI as the HTTP/API layer connecting the frontend/chat interface to the agent workflow.

FastAPI responsibilities:
- application startup/configuration
- request/response handling
- authentication/session integration if later enabled
- upload endpoints
- chat/session endpoints
- HITL endpoints
- project state endpoints
- preview/build status endpoints
- controlled external-action endpoints

Do not put complex agent reasoning directly inside route handlers.

### LangGraph

Use LangGraph as the primary workflow/state orchestration framework.

LangGraph should manage:
- graph state
- stage transitions
- sequential execution
- conditional routing
- HITL pauses/resumes
- iteration loops
- agent delegation
- workflow checkpoints where supported

### LangChain

Use LangChain for reusable LLM/application building blocks, such as:
- model/provider abstraction
- prompts
- runnable composition where useful
- document loaders
- retrievers
- embeddings/vector-store integrations where required
- tool wrappers where appropriate
- structured output helpers

Do not force LangChain abstractions into places where plain Python is clearer.

### Pydantic

Use Pydantic models for:
- API schemas
- graph state schemas where appropriate
- agent outputs
- configuration validation
- tool input/output contracts
- structured project state

Keep schemas explicit and typed.

---

## 9. AGENT HARNESS ARCHITECTURE

Agent Harness is the runtime coordination layer around agents.

It is responsible for:

```text
select agent
→ prepare context
→ expose allowed tools
→ execute
→ capture result
→ validate
→ update state
→ decide next action
```

It must NOT mean “run every agent.”

Only activate agents relevant to the current task/stage.

### Finalized v1 roles

Full contracts (purpose / context / tools / prohibitions / output schema) live in
`docs/agents.md`.

1. Requirement Analyzer
2. Planner
3. Design Director
4. Reference Analyzer
5. Frontend Architect
6. Builder
7. Visual & UX Critic  *(Visual Critic + UX Reviewer merged — they overlapped on the
   same rendered-page criteria, and overlapping agents are discouraged)*
8. Anti-Slop Reviewer
9. Accessibility Reviewer
10. Code Reviewer
11. Fixer
12. GitHub Operator (gated behind external-write approval)

Deferred until explicitly requested: Lovable Operator, Deployment Operator.

These are specialized roles. They are not permanently running LLM processes.

### Agent role contract

Each agent should define:
- purpose
- responsibilities
- allowed context
- allowed tools
- prohibited actions
- expected output format
- handoff/result schema where useful

Do not create many overlapping agents merely to increase the agent count.

---

## 10. SINGLE-PROVIDER STRATEGY

Initial implementation should use one primary model provider: **Anthropic Claude**.

Important distinctions:

```text
1 API key/provider
≠
1 model invocation
```

Different agents can use the same Claude provider. Every actual model execution can still consume tokens.

Do not introduce multiple providers until there is a concrete product need.

Potential future provider abstraction may exist behind a clean interface, but the first implementation should remain simple.

---

## 11. CONTEXT MANAGEMENT — HIGH PRIORITY

Context must be actively managed to reduce hallucination, cost, and irrelevant reasoning.

Never give every agent:
- the entire conversation
- the entire repository
- every previous agent output
- all tool definitions
- all historical iterations

Instead use:

```text
Project State
→ Context Builder / Context Selector
→ Relevant Context Slice
→ Agent
```

### Context examples

Requirement Analyzer:
- raw user requirements
- project constraints
- current product state

Planner:
- normalized requirements
- business/product intent
- technical constraints
- acceptance criteria

Design Agent:
- approved requirements
- approved plan
- design constraints
- reference analysis when available

Builder:
- approved plan
- approved design specification
- component architecture
- affected files
- acceptance criteria

Reviewer:
- rendered/changed output
- applicable criteria
- relevant approved design state

### Context principles

- Never make an agent re-think an explicitly approved decision.
- Prefer structured state over repeated raw transcripts.
- Retrieve relevant information when needed.
- Summarize old context when it is no longer useful in full detail.
- Keep agent context scoped to the current task.
- Do not pass hidden/private reasoning as application state.

---

## 12. REQUIREMENT NORMALIZATION

Users may submit either vague or highly detailed prompts.

### Vague prompt

Example:

> “Build a coffee shop website.”

System should discover only necessary missing information using planning/HITL.

### Detailed prompt

Example fields:
- business goal
- target users
- pages
- sections
- features
- colors
- typography
- interactions
- responsive behavior
- content
- technical constraints
- reference images
- acceptance criteria

System should NOT blindly brainstorm again.

Instead:

```text
Detailed requirements
→ normalize
→ detect missing/conflicting requirements
→ preserve explicit decisions
→ reduce unnecessary HITL
→ execute
```

Detailed input should usually reduce uncertainty and redundant agent work, but long/unstructured/repetitive input may still increase context cost.

---

## 13. ADAPTIVE HITL

HITL exists primarily for **user authority and meaningful decision boundaries**, not merely because a prompt is ambiguous.

### Typical flow

```text
Prompt
↓
Planning
↓
HITL
↓
Design
↓
HITL
↓
Reference analysis (when supplied)
↓
HITL
↓
Build
↓
Quality check
↓
Final review
```

This is adaptive.

If the user already provided strong requirements:
- fewer clarification questions
- fewer redundant design questions
- more validation/execution

If the user gives sparse requirements:
- more targeted HITL decisions

### HITL response format

For major stages provide:
- Accept/Approve
- relevant AI-generated modification options
- Custom instruction

The number of options should depend on the actual AI result.

Never ask the user to choose something that they have already explicitly fixed.

---

## 14. PLANNING BEHAVIOR

Planning must happen before implementation unless the user explicitly requests a direct small edit.

Planning should structure:
- objective
- audience
- pages
- sections
- content hierarchy
- CTA strategy
- interaction requirements
- responsive requirements
- design intent
- technical constraints
- acceptance criteria

Planner is not the builder.

Planner must not silently implement code unless the workflow explicitly assigns implementation.

---

## 15. DESIGN BEHAVIOR

Design should produce a coherent system, not random styling decisions.

Possible design data:
- font family
- weights
- typography scale
- color palette
- spacing rhythm
- layout grid
- density
- border radius
- button language
- card/container behavior
- image treatment
- motion/animation principles
- overall visual tone

If the user specifies a design decision, treat it as approved truth.

Do not replace user-selected fonts, colors, typography, or visual direction with agent preferences.

---

## 16. REFERENCE IMAGE WORKFLOW

When a reference image is supplied:

```text
Reference image
→ image/vision analysis
→ structured design interpretation
→ HITL review
→ approved visual direction
→ build
```

Analyze:
- composition
- layout
- hierarchy
- typography characteristics
- color relationships
- spacing
- card/container treatment
- navigation
- hero structure
- decorative language
- interaction cues

The purpose is to extract design intent, not blindly copy another website.

If the image is unavailable to the runtime, do not invent its contents. Report the missing asset and stop at the appropriate workflow boundary.

The only reference asset in current project context is
`Refrence_images/chatbot_refrence.png`. There is no landing-page reference file in
this repository — the built and approved Landing Page is itself the reference for new
pages (`docs/design.md`, `docs/frontend.md`).

---

## 17. ANTI-AI-SLOP / IMPECCABLE LOOP

**MANDATORY GATE.** Impeccable evaluation is required in the
generation → evaluation → fix → re-evaluation loop. It is not optional and has no
substitute. If Impeccable is unavailable, **STOP and report the quality gate as
BLOCKED** — never continue as if the check passed, and never substitute an
agent-only review for it.

**Availability:** Impeccable is **installed** — a Claude Code skill (4.3.1) with a
CLI (`--version` 4.0.0); `impeccable detect --json <target>` is the detector.
`.impeccable/` is optional per-project detector config, not an installation marker.
The evaluation stage is still blocked, but on capturing a **rendered** preview for
the reviewers, not on Impeccable. See `docs/design.md` §5.2.

Use Impeccable as an automated design-quality signal.

Do not treat it as the sole source of truth: a clean run is a necessary condition for
passing the gate, not a sufficient one. Rendered inspection is still required.

Preferred loop:

```text
Build
↓
Render
↓
Impeccable / design checks
↓
Findings
↓
Visual review
↓
Targeted fix
↓
Re-render
↓
Re-check
```

Do not blindly change intentional design decisions just because a detector reports a finding.

Review against user intent and approved design state.

Watch for:
- generic AI-SaaS composition
- excessive gradients
- unnecessary glassmorphism
- repetitive cards
- arbitrary icon grids
- excessive rounded containers
- weak typography hierarchy
- excessive glow/shadow usage
- decorative blobs without purpose
- inconsistent spacing
- poor responsive behavior

Goal:

**purposeful, coherent, human-directed design — not artificial novelty.**

---

## 18. RENDERED UI VALIDATION

Do not validate only by reading source code.

Whenever practical:

```text
source code
→ run/build
→ render page
→ inspect result
→ evaluate
→ fix
```

A successful build does not prove visual quality.

A clean lint/type-check does not prove UX quality.

---

## 19. TARGETED ITERATION

When the user says:

> “The hero feels too empty.”

Change the affected hero/components instead of regenerating the entire project.

General algorithm:

```text
User feedback
↓
Identify affected area
↓
Load relevant state/code
↓
Modify smallest necessary scope
↓
Validate
↓
Re-render
```

Preserve unrelated approved decisions.

Maximum redesign loop: **3 iterations** unless the user explicitly changes that limit.

After repeated unresolved rejection, surface the conflict instead of endlessly regenerating.

---

## 20. MCP INTEGRATION

MCP is an external capability/connection layer, not the agent itself.

Conceptual stack:

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
- browser/preview tooling
- deployment providers
- future developer tools

Do not hard-code MCP capabilities that have not been discovered/verified.

Always inspect actual tool names, schemas, permissions, and side effects before use.

### External write gate

Actions such as:
- GitHub push
- pull request creation
- deployment
- deletion
- external mutations

must follow explicit user-approval policy.

Never expose secrets through prompts, generated code, logs, commits, or tool outputs.

---

## 21. GITHUB / REPOSITORY WORKFLOW

When the user asks to push/build a repository:

```text
Implementation complete
↓
Validation
↓
User review/approval when required
↓
GitHub MCP
↓
branch / commit / push / PR
```

Do not treat repository operations as an implicit side effect of generation.

---

## 22. GENERATED WEBSITE BACKEND POLICY

The generated website itself is frontend-first.

Do not silently create:
- authentication backend
- database schema
- payment server
- microservices
- cloud infrastructure
- production API architecture

unless the user's product requirements explicitly expand the generated website scope.

The **builder's own backend** remains a separate FastAPI/LangGraph/LangChain service.

---

## 23. BUILDER BACKEND LAYERING

Prefer clean separation:

```text
backend/
├── app.py / main.py              # FastAPI entry point
├── api/                           # HTTP/API routes
├── schemas/                       # Pydantic request/response models
├── graph/                         # LangGraph workflow/state
├── agents/                        # Specialized agent definitions/runtime logic
├── services/                      # Application services
├── tools/                         # Tool wrappers / internal tools
├── mcp/                           # MCP client/integration layer
├── context/                       # Context construction/slicing
├── evaluation/                    # quality / anti-slop / validation
├── memory/                        # durable project/session memory if needed
├── rag/                           # document retrieval functionality if enabled
├── config/                        # configuration
└── utils/                         # small generic helpers only
```

Do not create every folder immediately. Follow the one-file gate and create only what the current stage requires.

The concrete, finalized layering (including `context/`, `evaluation/`, `mcp/` and
`memory/`) is specified in `docs/architecture.md` §4. The runtime and dependency
contract is in `docs/runtime.md`.

---

## 24. RAG SCOPE

**v1 status: DEFERRED.** Nothing in the current v1 scope requires retrieval. Do not
build the RAG subsystem speculatively. The rules below apply if and when the user
enables it.

When project RAG is used, keep the approved loader scope controlled.

Initial document sources:
- PDF
- DOC/DOCX where explicitly required by project design
- TXT/text

If the project's current agreed scope limits loaders to exact types, do not expand it without approval.

Retrieval strategy should be selected according to the task; possible techniques include:
- keyword/BM25
- vector retrieval
- hybrid retrieval
- reciprocal-rank fusion (RRF)
- MMR where appropriate

Do not use complex retrieval simply because it is available.

---

## 25. CODE QUALITY STYLE

Use clean Python and object-oriented boundaries where they improve maintainability.

Preferred principles:
- small functions
- clear classes for real responsibilities
- typed interfaces
- Pydantic validation at boundaries
- centralized configuration
- no secrets in source
- useful exception handling
- predictable imports
- no circular imports
- no unexplained globals

Avoid unnecessary abstraction.

Do not create a class just to wrap one trivial function unless it belongs to a stable architectural responsibility.

---

## 26. CONFIGURATION & SECRETS

Use environment variables for credentials and provider configuration.

Example concept:

```text
.env
ANTHROPIC_API_KEY=...
MCP credentials=...
other secrets=...
```

Never hard-code:
- API keys
- access tokens
- passwords
- OAuth secrets
- private repository tokens

Never print them in logs or error responses.

Ensure `.env` is ignored by version control.

---

## 27. ERROR HANDLING

Errors should be:
- typed where useful
- actionable
- safe to expose through the API
- separated between user-facing and internal/debug information

Do not leak:
- environment values
- secret tokens
- internal stack traces in production responses
- private MCP credentials

Agent/tool failures should update workflow state and allow the harness to decide whether to retry, switch strategy, or stop.

---

## 28. STATE & APPROVAL PERSISTENCE

Important decisions should survive agent transitions.

Conceptual project state:

```text
project_state
├── user_requirements
├── normalized_requirements
├── approved_plan
├── design_spec
├── reference_analysis
├── component_tree
├── generated_files
├── quality_findings
├── user_feedback
├── iteration_count
├── approval_history
└── external_actions
```

Do not rely on a giant chat transcript as the only source of truth.

---

## 29. AGENT TOOL ACCESS

Use least privilege.

A planner should not automatically receive repository write access.

A visual reviewer should not automatically receive GitHub push access.

Example:

```text
Planner
→ read/search/context

Designer
→ design/reference/context

Builder
→ code read/write/run

Reviewer
→ inspect/render/evaluate

GitHub Operator
→ repository tools
```

Only expose the capabilities required by the current role.

---

## 30. PARALLELIZATION RULE

Parallel execution is for genuinely independent work only.

Good example:

```text
Rendered page
├── UX review
├── Accessibility review
└── Anti-slop review
```

Bad example:

```text
Planning
├── Planner
├── Designer
├── Builder
```

when design depends on the approved plan and build depends on the approved design.

Parallelization can reduce wall-clock time while increasing total token usage.

Prefer efficiency through correct delegation, scoped context, and fewer unnecessary calls — not by maximizing agent count.

---

## 31. DO NOT RECREATE APPROVED DECISIONS

A user decision becomes a constraint after approval.

Examples:

```text
User chose Inter
→ do not replace with another font later.

User rejected testimonials
→ do not add them back during implementation.

User approved cream/brown palette
→ do not silently introduce an unrelated neon palette.

User approved page structure
→ do not rearrange it without a reason and user approval.
```

This rule exists to prevent the exact failure mode of a model “taking over” the project and inventing its own product logic.

---

## 32. PRODUCT KNOWLEDGE PRESERVATION

At every major decision boundary, preserve durable product knowledge in structured project state/documentation rather than relying only on conversation memory.

Important knowledge includes:
- product purpose
- target workflow
- approved architecture
- stage approvals
- visual system
- user constraints
- integration decisions
- unresolved decisions
- iteration history

Agents should consume these artifacts rather than repeatedly rediscovering project knowledge.

---

## 33. DEVELOPMENT STOP CONDITIONS

Claude must stop instead of continuing when:

- the user has not approved the next file in one-file mode
- a required reference asset is missing
- a requirement conflict cannot be safely resolved
- a destructive/external action needs approval
- a secret would need to be exposed
- the next action would require inventing product behavior
- the workflow has reached its iteration limit

Do not hide uncertainty behind confident code generation.

---

## 34. FILE CREATION CHECKLIST

Before creating a file, ask internally:

1. Is this file required by an approved architecture?
2. Is its responsibility unique and clear?
3. Does another file already own this responsibility?
4. What dependency/context does it need?
5. What should NOT belong in it?
6. What is the minimum implementation required now?

After creating it:

1. Read it.
2. Review it.
3. Validate it.
4. Explain why it exists.
5. Stop.
6. Ask user permission for the next file.

---

## 35. DEFINITION OF DONE FOR A FILE

A file is “done for this step” when:

- responsibility is clear
- imports/references are valid or intentionally stubbed
- implementation matches approved requirements
- comments/docstring explain architectural purpose
- secrets are protected
- relevant tests/checks pass where available
- no unnecessary neighboring files were created
- user receives a completion summary

“Done” does not mean the entire project is finished.

---

## 36. COMMUNICATION RULE

Claude should communicate in concise engineering updates, not long generic explanations.

For each file completion, report:

```text
Completed: <path>
Purpose: <why it exists>
Implemented: <2–4 key points>
Validated: <checks>
Notes: <important limitation, if any>

Please review this file/code. Should I create the next file?
```

Then wait.

---

## 37. FINAL ARCHITECTURAL PRINCIPLE

The system should behave like a controlled software-engineering team, not a single model that improvises everything.

```text
User intent
↓
Structured state
↓
Specialized agent
↓
Scoped context
↓
Approved decision
↓
Implementation
↓
Evaluation
↓
Targeted correction
↓
Human control
↓
External action only when authorized
```

The goal is not maximum agent count.

The goal is:

**minimum unnecessary reasoning + maximum requirement fidelity + controlled execution + high-quality frontend output.**
