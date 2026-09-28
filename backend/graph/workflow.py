"""The LangGraph workflow: the Agent/Workflow Router.

WHY THIS FILE EXISTS
    This is where stage transitions live. It decides which node runs next, carries
    ``ProjectState`` between them, and is where HITL interrupts will pause the run.
    Keeping that in one graph — rather than as control flow scattered through service
    functions — is what makes the pipeline auditable and resumable.

    To be unambiguous: this is the **Agent/Workflow Router**. It routes workflow nodes
    and agent roles on the server. It has nothing to do with React Router, which
    routes URL paths in the browser (CLAUDE.md 3A).

LAYER
    Graph (``backend/graph/``). Depends on agents, context and schemas. It imports
    nothing from api or main; the API drives the graph, not the reverse.

STATE
    ``ProjectState`` IS the graph state — there is no second model. Each node returns
    a **partial update containing only the fields it owns**, and the node reads its
    inputs through the ContextBuilder rather than helping itself to the whole object
    (docs/state.md 3).

CURRENT EXTENT — deliberately incomplete
    Only ``requirement_analysis`` is wired, because it is the only role implemented.
    The remaining stages in ``docs/state.md`` 4 are NOT stubbed: a placeholder node
    that returns success would let a run report progress through stages that never
    happened, which is precisely the silent downgrade CLAUDE.md 26 forbids. Each
    stage is added when its agent exists.

CHECKPOINTER — an open decision, deliberately not made here
    ``docs/state.md`` 5.1 records that the interim checkpointer is undecided pending
    the Supabase persistence architecture, and that neither option may be assumed.
    So this module **accepts** a checkpointer and never constructs one. Passing
    ``None`` compiles a graph with no durability: pending HITL gates will not survive
    a restart. That is acceptable for development and must not ship.

Spec: docs/state.md 3, 4, 5.1; docs/workflow.md 1; docs/architecture.md 3.3, 3.7;
      CLAUDE.md 3A, 26.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.agents.base import AgentFailure, AgentFailureKind
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.planner import PlannerAgent
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.context.builder import ContextBuilder, ContextPermissionError
from backend.schemas.api import ErrorCode, HitlDecisionRequest
from backend.schemas.enums import AgentRole, HitlAction, RunStatus, WorkflowStage
from backend.schemas.state import (
    ApprovedDecision,
    ErrorBody,
    HitlCheckpoint,
    HitlRecord,
    ProjectState,
)

logger = logging.getLogger(__name__)

# Node names are the stage values, so the graph topology and the status the Chatbot
# page renders can never drift apart.
NODE_REQUIREMENT_ANALYSIS = WorkflowStage.REQUIREMENT_ANALYSIS.value
NODE_PLANNING = WorkflowStage.PLANNING.value

# The gate is not a pipeline stage — it is the pause that belongs to the planning
# stage, so it carries a distinct node name but reports WorkflowStage.PLANNING.
NODE_PLAN_GATE = "plan_gate"
NODE_DESIGN = WorkflowStage.DESIGN.value
NODE_DESIGN_GATE = "design_gate"

# Where a stage parks a custom instruction until it re-runs. One key per stage,
# so a refinement meant for the plan can never be picked up by the design pass.
_INSTRUCTION_KEYS: dict[WorkflowStage, str] = {
    WorkflowStage.PLANNING: "planning_instruction",
    WorkflowStage.DESIGN: "design_instruction",
}

# An agent failure is recorded in state with a stable, safe code. Upstream problems
# and implementation defects are distinguished so the harness can tell a
# retry-worthy failure from a bug that retrying cannot fix (CLAUDE.md 26).
_FAILURE_TO_ERROR_CODE: dict[AgentFailureKind, ErrorCode] = {
    AgentFailureKind.SCHEMA_INVALID: ErrorCode.UPSTREAM_ERROR,
    AgentFailureKind.PROVIDER_ERROR: ErrorCode.UPSTREAM_ERROR,
    AgentFailureKind.TIMEOUT: ErrorCode.UPSTREAM_ERROR,
    AgentFailureKind.CONTRACT_VIOLATION: ErrorCode.INTERNAL_ERROR,
    AgentFailureKind.INTERNAL: ErrorCode.INTERNAL_ERROR,
}


@dataclass(frozen=True)
class WorkflowDependencies:
    """Everything the graph needs, injected rather than constructed.

    Nodes therefore reach for nothing global: a test can build the graph with a stub
    agent and no network, and the composition root stays the only place that knows
    how the pieces fit (CLAUDE.md 29).
    """

    context_builder: ContextBuilder
    requirement_analyzer: RequirementAnalyzerAgent
    planner: PlannerAgent
    design_director: DesignDirectorAgent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _failure_update(stage: WorkflowStage, failure: AgentFailure) -> dict[str, Any]:
    """Record a failed stage in state instead of raising out of the graph.

    The run stops and the user is told; the workflow does not route around the
    failure or pretend the stage completed (CLAUDE.md 26).
    """
    code = _FAILURE_TO_ERROR_CODE.get(failure.kind, ErrorCode.INTERNAL_ERROR)
    logger.warning(
        "stage failed stage=%s kind=%s attempts=%d",
        stage.value,
        failure.kind.value,
        failure.attempts,
    )
    return {
        "current_stage": stage,
        "run_status": RunStatus.FAILED,
        "last_error": ErrorBody(code=code.value, message=failure.message),
        "updated_at": _utcnow(),
    }


async def requirement_analysis_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Normalize the raw request into structured requirements.

    Owns: ``normalized_requirements``, ``requirement_conflicts``,
    ``requirement_gaps``. Returns only those, plus the control fields that record the
    transition — never a whole-state replacement (docs/state.md 3).
    """
    stage = WorkflowStage.REQUIREMENT_ANALYSIS

    try:
        context = deps.context_builder.build(
            state,
            AgentRole.REQUIREMENT_ANALYZER,
            task=(
                "Normalize this website request into structured requirements. "
                "Report conflicts and blocking gaps; do not resolve them."
            ),
        )
    except ContextPermissionError as exc:
        # A context-permission error is an implementation bug, not a model problem,
        # so it is reported as a contract violation rather than retried.
        return _failure_update(
            stage,
            AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc)),
        )

    result = await deps.requirement_analyzer.run(context)
    if not result.ok:
        return _failure_update(stage, result.failure)

    requirements = result.output
    logger.info(
        "requirement analysis complete project_id=%s density=%s conflicts=%d gaps=%d",
        state.project_id,
        requirements.input_density,
        len(requirements.conflicts),
        len(requirements.gaps),
    )
    return {
        "normalized_requirements": requirements,
        "requirement_conflicts": list(requirements.conflicts),
        "requirement_gaps": list(requirements.gaps),
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


async def planning_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Turn approved requirements into a plan.

    Owns: ``plan``. Re-entered rather than advanced when the user gives a custom
    instruction at the gate, so a refinement replaces the plan in place instead of
    stacking a second one (docs/workflow.md 3).
    """
    stage = WorkflowStage.PLANNING

    # A custom instruction is a user decision and outranks any prior agent output for
    # this stage, so it is carried into the task rather than merely appended to state.
    refinement = state.node_scratch.get("planning_instruction")
    task = "Produce the implementation plan from the approved requirements."
    if refinement:
        task = (
            f"{task}\n\nThe user reviewed your previous plan and asked for this "
            f"change, which takes precedence over your earlier output:\n{refinement}"
        )

    try:
        context = deps.context_builder.build(state, AgentRole.PLANNER, task=task)
    except ContextPermissionError as exc:
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    result = await deps.planner.run(context)
    if not result.ok:
        return _failure_update(stage, result.failure)

    logger.info(
        "planning complete project_id=%s pages=%d open_questions=%d refined=%s",
        state.project_id,
        len(result.output.pages),
        len(result.output.open_questions),
        bool(refinement),
    )
    return {
        "plan": result.output,
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


async def design_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Turn the approved plan into a design system.

    Owns: ``design_spec``. Like planning, it is re-entered rather than advanced when
    the user asks for a change at its gate, so a refinement replaces the system in
    place instead of leaving two (docs/workflow.md 3).
    """
    stage = WorkflowStage.DESIGN
    refinement = state.node_scratch.get(_INSTRUCTION_KEYS[stage])
    task = "Define the design system from the approved plan."
    if refinement:
        task = (
            f"{task}\n\nThe user reviewed your previous design direction and asked "
            f"for this change, which takes precedence over your earlier output:\n"
            f"{refinement}"
        )

    try:
        context = deps.context_builder.build(state, AgentRole.DESIGN_DIRECTOR, task=task)
    except ContextPermissionError as exc:
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    result = await deps.design_director.run(context)
    if not result.ok:
        return _failure_update(stage, result.failure)

    logger.info(
        "design complete project_id=%s fonts=%d refined=%s",
        state.project_id,
        len(result.output.font_families),
        bool(refinement),
    )
    return {
        "design_spec": result.output,
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


def _gate_node(
    state: ProjectState,
    *,
    stage: WorkflowStage,
    title: str,
    result_ref: str,
    summary: str,
) -> dict[str, Any]:
    """Pause for the user to approve, redirect or reject a stage result.

    One implementation for every gate. The gates differ only in which stage they
    belong to and what they are reviewing; the mechanics — raising a checkpoint,
    interrupting, recording the decision — are identical, and writing them twice is
    how two gates end up behaving differently by accident.

    This is the HITL boundary. It owns ``pending_checkpoint``, ``hitl_history`` and
    ``approved_decisions``, and is the only path permitted to write the latter two
    (docs/state.md 3).

    Requires a checkpointer: ``interrupt()`` resumes by re-running the node, which is
    only possible if the paused run was persisted (docs/state.md 5.1).
    """
    checkpoint = HitlCheckpoint(
        stage=stage,
        title=title,
        summary=summary,
        result_ref=result_ref,
        # Empty is legitimate: options are derived from the actual result and are
        # never padded to a fixed count (docs/api.md 4, docs/workflow.md 3).
        options=[],
    )

    raw_decision = interrupt(checkpoint.model_dump(mode="json"))

    try:
        decision = HitlDecisionRequest.model_validate(raw_decision)
    except Exception:
        # Not guessed at, and not treated as "not approved" — that would re-run the
        # stage and spend a model call on a payload nobody could read. The API
        # validates the request before resuming, so reaching here is an internal
        # defect.
        logger.warning("malformed HITL decision at the %s gate; stopping", stage.value)
        update = _failure_update(
            stage,
            AgentFailure(
                AgentFailureKind.CONTRACT_VIOLATION,
                "The submitted review decision could not be read.",
            ),
        )
        update["pending_checkpoint"] = checkpoint
        return update

    record = HitlRecord(
        checkpoint_id=checkpoint.checkpoint_id,
        stage=stage,
        action=decision.action,
        option_id=decision.option_id,
        instruction=decision.instruction,
    )
    instruction_key = _INSTRUCTION_KEYS[stage]
    update: dict[str, Any] = {
        "pending_checkpoint": None,
        "hitl_history": [*state.hitl_history, record],
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "updated_at": _utcnow(),
        # Cleared either way: a refinement must not silently apply to a later run.
        "node_scratch": {
            k: v for k, v in state.node_scratch.items() if k != instruction_key
        },
    }

    if decision.action is HitlAction.APPROVE:
        update["approved_decisions"] = [
            *state.approved_decisions,
            ApprovedDecision(
                decision_id=checkpoint.checkpoint_id,
                stage=stage,
                statement=f"Approved {result_ref}: {summary}",
                origin=HitlAction.APPROVE,
            ),
        ]
    elif decision.action is HitlAction.CUSTOM:
        update["node_scratch"] = {
            **update["node_scratch"],
            instruction_key: decision.instruction,
        }

    logger.info(
        "%s gate resolved project_id=%s action=%s",
        stage.value,
        state.project_id,
        decision.action.value,
    )
    return update


def plan_gate_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Review gate for the plan."""
    return _gate_node(
        state,
        stage=WorkflowStage.PLANNING,
        title="Review the plan",
        result_ref="plan",
        summary=(
            state.plan.objective
            if state.plan and state.plan.objective
            else "A plan is ready for review."
        ),
    )


def design_gate_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Review gate for the design system.

    This is the gate that matters most for the reference stage that follows: what the
    user approves here is what a reference image is later checked against
    (docs/design.md 3).
    """
    spec = state.design_spec
    summary = "A design direction is ready for review."
    if spec is not None:
        summary = spec.visual_tone or spec.rationale or summary
    return _gate_node(
        state,
        stage=WorkflowStage.DESIGN,
        title="Review the design direction",
        result_ref="design_spec",
        summary=summary,
    )


def route_after_gate(state: ProjectState) -> str:
    """Advance on approval, re-run the stage on a revision, stop on a failure.

    Shared by every gate. Where "revise" goes is decided by the edge map, not here,
    so one router serves them all.

    A custom instruction or a rejection re-runs the stage **in place** rather than
    advancing, which keeps a refinement a revision instead of a new stage
    (docs/workflow.md 3).
    """
    if state.run_status is RunStatus.FAILED:
        return "stop"
    last = state.hitl_history[-1] if state.hitl_history else None
    if last is not None and last.action is HitlAction.APPROVE:
        return "approved"
    return "revise"


def build_workflow(deps: WorkflowDependencies, *, checkpointer: Any | None = None):
    """Compile the workflow graph.

    Args:
        deps: injected collaborators for the nodes.
        checkpointer: a LangGraph checkpointer, or ``None``. **Not chosen here** —
            the interim checkpointer is an open decision (docs/state.md 5.1). With
            ``None`` the graph has no durability and a pending HITL gate is lost on
            restart.

    Returns:
        The compiled graph. Invoke it with
        ``config={"configurable": {"thread_id": project_id}}`` so a run is addressed
        by its project (docs/state.md 3).
    """
    graph = StateGraph(ProjectState)

    async def _requirement_analysis(state: ProjectState) -> dict[str, Any]:
        return await requirement_analysis_node(state, deps)

    async def _planning(state: ProjectState) -> dict[str, Any]:
        return await planning_node(state, deps)

    def _plan_gate(state: ProjectState) -> dict[str, Any]:
        return plan_gate_node(state, deps)

    async def _design(state: ProjectState) -> dict[str, Any]:
        return await design_node(state, deps)

    def _design_gate(state: ProjectState) -> dict[str, Any]:
        return design_gate_node(state, deps)

    graph.add_node(NODE_REQUIREMENT_ANALYSIS, _requirement_analysis)
    graph.add_node(NODE_PLANNING, _planning)
    graph.add_node(NODE_PLAN_GATE, _plan_gate)
    graph.add_node(NODE_DESIGN, _design)
    graph.add_node(NODE_DESIGN_GATE, _design_gate)

    graph.add_edge(START, NODE_REQUIREMENT_ANALYSIS)
    graph.add_edge(NODE_REQUIREMENT_ANALYSIS, NODE_PLANNING)
    graph.add_edge(NODE_PLANNING, NODE_PLAN_GATE)
    graph.add_edge(NODE_DESIGN, NODE_DESIGN_GATE)

    # Each gate: approval advances, a revision re-runs its own stage, a failure
    # stops. The router is shared; only the destinations differ.
    graph.add_conditional_edges(
        NODE_PLAN_GATE,
        route_after_gate,
        {"approved": NODE_DESIGN, "revise": NODE_PLANNING, "stop": END},
    )

    # The approved design branch terminates for now. `reference_analysis` is next and
    # is conditional on an uploaded image; it is not stubbed, so the graph never
    # claims a stage that has no implementation.
    graph.add_conditional_edges(
        NODE_DESIGN_GATE,
        route_after_gate,
        {"approved": END, "revise": NODE_DESIGN, "stop": END},
    )

    if checkpointer is None:
        logger.warning(
            "workflow compiled WITHOUT a checkpointer: runs are not durable, and the "
            "gates cannot pause and resume across a restart (docs/state.md 5.1)"
        )

    return graph.compile(checkpointer=checkpointer)