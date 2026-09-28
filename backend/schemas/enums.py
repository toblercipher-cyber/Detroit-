"""Shared vocabulary for the backend: workflow stages, run status, HITL actions,
agent roles and finding severity.

WHY THIS FILE EXISTS
    These five vocabularies are used by three layers at once — the LangGraph workflow,
    the Pydantic API models, and the Chatbot page that renders them. Defining them
    once here means the graph, the API contract and the frontend cannot drift apart.
    Anything that would otherwise be a bare string literal in two places belongs here.

LAYER
    Schemas (``backend/schemas/``). Deliberately dependency-free: it imports only the
    standard library, so every other layer can import it without creating a cycle.
    It does not import settings, and settings does not import it.

WHAT THIS FILE IS NOT
    - It is not the agent registry. Agent behaviour, context allowlists and tool
      permissions live in ``backend/agents/``; ``AgentRole`` is only the identifier.
    - It is not the graph. Node wiring and transitions live in ``backend/graph/``;
      ``WorkflowStage`` is only the stage name.

NAMING
    Values are the exact lowercase strings in the API contract. They are part of the
    wire format: changing one is a breaking API change, not a rename.

Spec: docs/api.md 3 (authoritative for the values), docs/workflow.md 1,
      docs/agents.md 2, docs/state.md 4.
"""

from __future__ import annotations

from enum import Enum


class WorkflowStage(str, Enum):
    """A stage of the canonical pipeline.

    One-to-one with ``docs/workflow.md`` 1 and with the LangGraph node set in
    ``docs/state.md`` 4. Declared in pipeline order.

    This is the **Agent/Workflow Router** vocabulary — server-side stage transitions.
    It has nothing to do with React Router or URL paths (CLAUDE.md 3A).
    """

    REQUIREMENT_ANALYSIS = "requirement_analysis"
    PLANNING = "planning"
    DESIGN = "design"
    # Conditional: entered only when a reference image was supplied.
    REFERENCE_ANALYSIS = "reference_analysis"
    FRONTEND_ARCHITECTURE = "frontend_architecture"
    BUILD = "build"
    PREVIEW = "preview"
    EVALUATION = "evaluation"
    TARGETED_FIX = "targeted_fix"
    RE_EVALUATION = "re_evaluation"
    FINAL_VALIDATION = "final_validation"
    # Optional: entered only on explicit request plus approval.
    EXTERNAL_ACTION = "external_action"


class RunStatus(str, Enum):
    """What the workflow is doing right now.

    Orthogonal to :class:`WorkflowStage` — the two are always reported together. The
    Chatbot page renders the pair, so ``stage=evaluation, status=running`` displays
    as "Evaluating" (docs/api.md 3.2).
    """

    IDLE = "idle"
    RUNNING = "running"
    AWAITING_HITL = "awaiting_hitl"
    COMPLETED = "completed"
    # A mandatory gate cannot run — e.g. Impeccable is unavailable at Evaluation.
    # This is NOT a failure and must never be reported as a pass: the workflow stops
    # and the user is told the gate is blocked (CLAUDE.md 15, docs/design.md 5).
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HitlAction(str, Enum):
    """What the user decided at a HITL checkpoint.

    ``CUSTOM`` carries a free-text instruction that outranks any prior agent output
    for that stage; the stage re-runs in place rather than advancing, and it does not
    consume the automatic-correction budget (docs/workflow.md 3).
    """

    APPROVE = "approve"
    SELECT_OPTION = "select_option"
    CUSTOM = "custom"
    REJECT = "reject"


class AgentRole(str, Enum):
    """Identifier for one of the 12 v1 agent roles.

    Roles are activated on demand for the current stage, never all at once
    (CLAUDE.md 6). Full contracts — purpose, allowed context, allowed tools,
    prohibited actions, output schema — are in ``docs/agents.md`` 4.

    Deferred roles (Lovable Operator, Deployment Operator) are intentionally absent:
    adding an identifier before the role exists would invite speculative wiring.
    """

    REQUIREMENT_ANALYZER = "requirement_analyzer"
    PLANNER = "planner"
    DESIGN_DIRECTOR = "design_director"
    REFERENCE_ANALYZER = "reference_analyzer"
    FRONTEND_ARCHITECT = "frontend_architect"
    BUILDER = "builder"
    # Visual Critic and UX Reviewer are merged: one role, one finding set.
    VISUAL_UX_CRITIC = "visual_ux_critic"
    # Requires Impeccable, which is a mandatory evaluator with no substitute.
    ANTI_SLOP_REVIEWER = "anti_slop_reviewer"
    ACCESSIBILITY_REVIEWER = "accessibility_reviewer"
    CODE_REVIEWER = "code_reviewer"
    FIXER = "fixer"
    # Gated: external writes require an approval token.
    GITHUB_OPERATOR = "github_operator"


class Severity(str, Enum):
    """Severity of a single evaluation finding.

    Declared most severe first. A finding's severity is evidence for triage, not
    proof that a change is required: a finding that conflicts with an approved
    decision goes to the user rather than to the Fixer (docs/design.md 5.5).
    """

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"