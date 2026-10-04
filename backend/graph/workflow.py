"""The LangGraph workflow: the Agent/Workflow Router.

Owns stage transitions and HITL pauses. This is the server-side router for
nodes and agent roles — nothing to do with React Router (CLAUDE.md 3A).

``ProjectState`` IS the graph state. Each node returns only the fields it owns
and reads its inputs through the ContextBuilder (docs/state.md 3).

11 of 12 stages are wired; ``external_action`` is not, because it needs MCP.
Missing stages are never stubbed — a placeholder returning success would let a
run report progress through stages that never happened.

The checkpointer is injected, never constructed here. With ``None`` the graph
has no durability and pending gates are lost on restart.

Spec: docs/state.md 3, 4, 5.1; docs/workflow.md 1; docs/architecture.md 3.3, 3.7.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.agents.base import AgentFailure, AgentFailureKind
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent
from backend.agents.builder import BuilderAgent
from backend.agents.code_reviewer import CodeReviewerAgent
from backend.agents.fixer import FixerAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.agents.planner import PlannerAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.visual_ux_critic import VisualUxCriticAgent
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.context.builder import (
    ChangedFilePayload,
    ContextBuilder,
    ContextPermissionError,
    ReferenceImagePayload,
    RenderedPreviewPayload,
)
from backend.memory.artifacts import StateRepository, StateRepositoryError
from backend.tools.build_runner import BuildRunner
from backend.tools.impeccable import ImpeccableStatus
from backend.tools.template import scaffold_workspace
from backend.tools.workspace import WorkspaceFiles
from backend.schemas.api import ErrorCode, HitlDecisionRequest
from backend.schemas.enums import (
    AgentRole,
    HitlAction,
    RunStatus,
    Severity,
    WorkflowStage,
)
from backend.schemas.state import (
    ApprovedDecision,
    BuildStatus,
    EvaluationFinding,
    EvaluationReport,
    ErrorBody,
    HitlCheckpoint,
    FileRef,
    HitlRecord,
    PreviewStatus,
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
NODE_REFERENCE_ANALYSIS = WorkflowStage.REFERENCE_ANALYSIS.value
NODE_REFERENCE_GATE = "reference_gate"
NODE_FRONTEND_ARCHITECTURE = WorkflowStage.FRONTEND_ARCHITECTURE.value
NODE_BUILD = WorkflowStage.BUILD.value
NODE_PREVIEW = WorkflowStage.PREVIEW.value
NODE_EVALUATION = WorkflowStage.EVALUATION.value
NODE_TARGETED_FIX = WorkflowStage.TARGETED_FIX.value
NODE_RE_EVALUATION = WorkflowStage.RE_EVALUATION.value
NODE_FINAL_VALIDATION = WorkflowStage.FINAL_VALIDATION.value
NODE_FINAL_GATE = "final_validation_gate"

# Where a stage parks a custom instruction until it re-runs. One key per stage,
# so a refinement meant for the plan can never be picked up by the design pass.
_INSTRUCTION_KEYS: dict[WorkflowStage, str] = {
    WorkflowStage.PLANNING: "planning_instruction",
    WorkflowStage.DESIGN: "design_instruction",
    WorkflowStage.REFERENCE_ANALYSIS: "reference_instruction",
    # The final gate reviews finished output; a revision there means another
    # correction pass, which is driven by findings rather than a stored note.
    WorkflowStage.FINAL_VALIDATION: "final_instruction",
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
    """Injected collaborators, so nodes reach for nothing global."""

    context_builder: ContextBuilder
    requirement_analyzer: RequirementAnalyzerAgent
    planner: PlannerAgent
    design_director: DesignDirectorAgent
    reference_analyzer: ReferenceAnalyzerAgent
    frontend_architect: FrontendArchitectAgent
    # A factory, not an agent: the Builder holds a handle to ONE workspace, so
    # it is built per project rather than shared (backend/agents/builder.py).
    build_builder: Callable[[WorkspaceFiles], BuilderAgent]
    # Same reason as the Builder: the Fixer holds a handle to ONE workspace.
    build_fixer: Callable[[WorkspaceFiles], FixerAgent]
    build_runner: BuildRunner
    visual_ux_critic: VisualUxCriticAgent
    anti_slop_reviewer: AntiSlopReviewerAgent
    accessibility_reviewer: AccessibilityReviewerAgent
    code_reviewer: CodeReviewerAgent
    # Answers 'is Impeccable installed'. Injected so a test can drive both
    # outcomes without touching the filesystem (CLAUDE.md 15).
    impeccable_probe: Callable[[], ImpeccableStatus]
    # The automatic-correction budget (docs/workflow.md 4). Injected rather
    # than hard-coded so configuration owns the limit, not the graph.
    max_auto_iterations: int
    # Reference Analysis needs the image BYTES, which state does not carry;
    # this is the only reason the graph touches persistence at all.
    repository: StateRepository
    # Runs the detector. Last because it is the only optional collaborator:
    # None until Impeccable is installed and its real interface has been
    # observed, and None means the gate BLOCKS — it never means "skip".
    impeccable_detector: Callable[[Path], list[EvaluationFinding]] | None = None
    # Captures the RENDERED preview -- DOM and screenshot -- for the three
    # reviewers granted it. None until a headless browser is approved; None
    # means the gate BLOCKS, because a URL is not something an agent can look
    # at and an unrendered index.html is not evidence.
    preview_capture: Callable[[Path, str], RenderedPreviewPayload] | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _failure_update(stage: WorkflowStage, failure: AgentFailure) -> dict[str, Any]:
    """Record a failure in state rather than raising, so the reason survives."""
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


async def reference_analysis_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """Read design intent from the most recent uploaded reference image.

    The bytes are loaded here and discarded: state holds only a checksum, and
    base64 must never enter project_state.json. Optional stage — the router
    decides whether it runs, so an image is guaranteed by the time it does.
    """
    stage = WorkflowStage.REFERENCE_ANALYSIS
    refinement = state.node_scratch.get(_INSTRUCTION_KEYS[stage])

    if not state.reference_images:
        # Unreachable via the router, which checks first. Kept because a node that
        # is asked for an analysis it cannot ground must refuse rather than invent.
        return _failure_update(
            stage,
            AgentFailure(
                AgentFailureKind.CONTRACT_VIOLATION,
                "Reference analysis was reached with no uploaded image.",
            ),
        )

    ref = state.reference_images[-1]
    try:
        content = deps.repository.load_reference_image(state.project_id, ref.image_id)
    except StateRepositoryError as exc:
        logger.warning(
            "reference image unreadable project_id=%s image_id=%s",
            state.project_id,
            ref.image_id,
        )
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    payload = ReferenceImagePayload(
        image_id=ref.image_id,
        content_type=ref.content_type,
        data_base64=base64.b64encode(content).decode("ascii"),
    )

    task = (
        "Describe the design intent visible in this reference image, and how it "
        "relates to the approved design direction."
    )
    if refinement:
        task = (
            f"{task}\n\nThe user reviewed your previous reading of this reference "
            f"and asked for this change, which takes precedence over your earlier "
            f"output:\n{refinement}"
        )

    try:
        context = deps.context_builder.build(
            state,
            AgentRole.REFERENCE_ANALYZER,
            task=task,
            reference_image=payload,
        )
    except ContextPermissionError as exc:
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    result = await deps.reference_analyzer.run(context)
    if not result.ok:
        return _failure_update(stage, result.failure)

    logger.info(
        "reference analysis complete project_id=%s image_id=%s refined=%s",
        state.project_id,
        ref.image_id,
        bool(refinement),
    )
    return {
        "reference_analysis": result.output,
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


async def frontend_architecture_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """Decide what components exist and how they nest. No gate (docs/workflow.md 1).

    Writes ``component_tree`` as well as ``frontend_architecture``: the Builder's
    context grant names the top-level field, so leaving the tree nested inside
    the architecture would hand the Builder an empty one.
    """
    stage = WorkflowStage.FRONTEND_ARCHITECTURE

    # Paths only. The architect decides structure, not implementation, so it needs
    # to know what exists — not what is in it (docs/agents.md 4.5).
    inventory = [ref.path for ref in state.generated_files] or None

    try:
        context = deps.context_builder.build(
            state,
            AgentRole.FRONTEND_ARCHITECT,
            task=(
                "Define the component architecture for the approved plan and "
                "design system."
            ),
            component_inventory=inventory,
        )
    except ContextPermissionError as exc:
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    result = await deps.frontend_architect.run(context)
    if not result.ok:
        return _failure_update(stage, result.failure)

    architecture = result.output
    logger.info(
        "frontend architecture complete project_id=%s pages=%d reused=%d new=%d",
        state.project_id,
        len(architecture.pages),
        len(architecture.reused_components),
        len(architecture.new_components),
    )
    return {
        "frontend_architecture": architecture,
        "component_tree": architecture.component_tree,
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


async def build_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Scaffold, let the Builder write src/, then compile. No gate.

    Scaffolding runs every time, so builder-owned config cannot be built against
    a model-altered version. A failed build is recorded rather than raised, and
    ``generated_files`` is read from disk, not from what the model claimed.
    """
    stage = WorkflowStage.BUILD
    started = _utcnow()
    workspace_dir = deps.repository.workspace_dir(state.project_id)

    try:
        scaffold_workspace(workspace_dir)
    except OSError as exc:
        logger.error("scaffold failed project_id=%s: %s", state.project_id, exc.strerror)
        return _build_failure(
            stage, started, "build", "The project workspace could not be prepared."
        )

    workspace = WorkspaceFiles(workspace_dir)

    try:
        context = deps.context_builder.build(
            state,
            AgentRole.BUILDER,
            task=(
                "Implement the approved component architecture in this workspace, "
                "following the approved design system."
            ),
        )
    except ContextPermissionError as exc:
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    result = await deps.build_builder(workspace).run(context)
    if not result.ok:
        return _failure_update(stage, result.failure)

    written = result.output
    logger.info(
        "builder wrote project_id=%s created=%d modified=%d",
        state.project_id,
        len(written.files_written),
        len(written.files_modified),
    )

    outcome = deps.build_runner.run(workspace_dir)

    # Recorded from the filesystem, not from the agent's claim: the manifest has to
    # describe what is actually there for Preview and the reviewers to be honest.
    generated = _workspace_manifest(workspace_dir)

    if not outcome.ok:
        update = _build_failure(
            stage, started, outcome.failed_step or "build", outcome.message
        )
        update["generated_files"] = generated
        return update

    return {
        "generated_files": generated,
        "build_status": BuildStatus(
            status="succeeded",
            step="build",
            started_at=started,
            finished_at=_utcnow(),
        ),
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


def preview_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Record where the built output can be seen. No agent, no gate.

    The URL is relative: the backend does not know the origin a browser reaches
    it on, so an absolute one would be wrong outside a single deployment.
    Staleness is not computed here — nothing changes files until Targeted Fix.
    """
    stage = WorkflowStage.PREVIEW
    dist_dir = deps.repository.workspace_dir(state.project_id) / "dist"

    if not dist_dir.is_dir() or not (dist_dir / "index.html").is_file():
        logger.warning("preview has no build output project_id=%s", state.project_id)
        return _failure_update(
            stage,
            AgentFailure(
                AgentFailureKind.CONTRACT_VIOLATION,
                "The build produced no output to preview.",
            ),
        )

    now = _utcnow()
    logger.info("preview ready project_id=%s", state.project_id)
    return {
        "preview_status": PreviewStatus(
            preview_url=f"/preview/{state.project_id}/",
            built_at=now,
            status="fresh",
        ),
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": now,
    }


async def evaluation_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """The first pass of the mandatory quality gate."""
    return await _evaluate(state, deps, WorkflowStage.EVALUATION)


async def re_evaluation_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """The same gate after a Targeted Fix; only the recorded stage differs.

    Delegates rather than duplicating: two copies of a mandatory gate is two
    places for it to drift.
    """
    return await _evaluate(state, deps, WorkflowStage.RE_EVALUATION)


async def _evaluate(
    state: ProjectState, deps: WorkflowDependencies, stage: WorkflowStage
) -> dict[str, Any]:
    """Run the mandatory quality gate, or report that it cannot run.

    Blocking with no substitute (CLAUDE.md 15): if Impeccable is unavailable,
    the detector unwired, the preview uncapturable, or any reviewer fails, the
    gate reports BLOCKED. An agent-only review may never stand in for it.
    """
    status = deps.impeccable_probe()

    if not status.available:
        logger.warning(
            "quality gate BLOCKED project_id=%s reason=%s",
            state.project_id,
            status.reason,
        )
        return _blocked_gate(state, status.version, status.reason, stage)

    if deps.impeccable_detector is None:
        # Installed but not wired. Reported as blocked for the same reason:
        # findings that were never produced cannot be interpreted.
        reason = "Impeccable is installed but its detector is not wired."
        logger.warning("quality gate BLOCKED project_id=%s reason=%s",
                       state.project_id, reason)
        return _blocked_gate(state, status.version, reason, stage)

    if deps.preview_capture is None:
        # Three reviewers are granted the RENDERED preview, and
        # RenderedPreviewPayload refuses a URL alone: a link is not something an
        # agent can look at. Without capture the only honest inputs would be the
        # built index.html -- an empty <div id="root"> before React runs -- and
        # reviewers passing a gate on that would be the substitute the ruling
        # forbids, dressed up as evidence.
        reason = "The rendered preview cannot be captured, so it cannot be reviewed."
        logger.warning("quality gate BLOCKED project_id=%s reason=%s",
                       state.project_id, reason)
        return _blocked_gate(state, status.version, reason, stage)

    workspace = deps.repository.workspace_dir(state.project_id)
    try:
        detector_findings = deps.impeccable_detector(workspace)
    except Exception:
        logger.exception("impeccable detector failed project_id=%s", state.project_id)
        return _blocked_gate(
            state, status.version,
            "The Impeccable detector could not be run.", stage,
        )

    reviews, review_failed = await _run_reviewers(state, deps, detector_findings)
    findings = [*detector_findings, *reviews]

    if review_failed:
        return _blocked_gate(
            state, status.version,
            "A reviewer failed, so the gate is inconclusive.", stage,
        )

    blocking = [f for f in findings if f.severity is Severity.BLOCKER]
    report = EvaluationReport(
        iteration=state.iteration_count,
        impeccable_available=True,
        impeccable_version=status.version,
        gate_status="failed" if blocking else "passed",
        findings=findings,
    )
    logger.info(
        "evaluation complete project_id=%s gate=%s findings=%d blockers=%d",
        state.project_id,
        report.gate_status,
        len(findings),
        len(blocking),
    )
    return {
        "evaluation_results": report,
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


async def targeted_fix_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """Correct what the gate found, touching only what the findings name.

    The only node that may increment ``iteration_count``, which is what makes
    the limit enforceable from one place. The counter goes up even when the fix
    FAILS — otherwise a failing Fixer loops forever on an unmoved budget.
    """
    stage = WorkflowStage.TARGETED_FIX
    findings = list(state.evaluation_results.findings) if state.evaluation_results else []

    if not findings:
        # Unreachable through the router, which only sends failures here. A fix
        # with nothing to fix would be an invitation to invent work.
        return _failure_update(
            stage,
            AgentFailure(
                AgentFailureKind.CONTRACT_VIOLATION,
                "Targeted Fix was reached with no findings to address.",
            ),
        )

    workspace_dir = deps.repository.workspace_dir(state.project_id)
    workspace = WorkspaceFiles(workspace_dir)
    changed = [
        ChangedFilePayload(path=ref.path, content=_read_source(workspace_dir, ref.path))
        for ref in state.generated_files
    ]

    try:
        context = deps.context_builder.build(
            state,
            AgentRole.FIXER,
            task=(
                "Address these findings in the smallest scope that resolves "
                "them. Leave everything else exactly as it is."
            ),
            findings=findings,
            changed_files=changed,
        )
    except ContextPermissionError as exc:
        return _failure_update(
            stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, str(exc))
        )

    # Incremented regardless of outcome, so it is set before any early return.
    iteration = state.iteration_count + 1
    result = await deps.build_fixer(workspace).run(context)
    if not result.ok:
        update = _failure_update(stage, result.failure)
        update["iteration_count"] = iteration
        return update

    fix = result.output
    logger.info(
        "targeted fix complete project_id=%s iteration=%d modified=%d unresolved=%d",
        state.project_id,
        iteration,
        len(fix.files_modified),
        len(fix.unresolved),
    )
    return {
        "iteration_count": iteration,
        "generated_files": _workspace_manifest(workspace_dir),
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


def _read_source(workspace_dir: Path, relative: str) -> str:
    """One generated file's text, or empty when unreadable.

    The Fixer is editing this code, so it needs content rather than a path. One
    missing file should not abandon the fix for the others.
    """
    try:
        return (workspace_dir / relative).read_text(encoding="utf-8")
    except OSError:
        logger.warning("could not read %s for the fixer", relative)
        return ""


def final_validation_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """Summarise what was produced, for the user's final review.

    No agent (docs/workflow.md 1): everything here is already known, and asking
    a model to restate it only adds a place for it to be restated wrongly.
    """
    stage = WorkflowStage.FINAL_VALIDATION
    report = state.evaluation_results

    if report is None or report.gate_status != "passed":
        # The router does not send unpassed work here, so reaching this means a
        # defect. Refusing is better than presenting unreviewed output as final.
        return _failure_update(
            stage,
            AgentFailure(
                AgentFailureKind.CONTRACT_VIOLATION,
                "Final Validation was reached without a passing quality gate.",
            ),
        )

    logger.info(
        "final validation ready project_id=%s files=%d iterations=%d",
        state.project_id,
        len(state.generated_files),
        state.iteration_count,
    )
    return {
        "current_stage": stage,
        "run_status": RunStatus.RUNNING,
        "last_error": None,
        "updated_at": _utcnow(),
    }


def final_validation_gate_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """The final review gate.

    Built from facts the harness already holds, not from a model's description
    of its own output: how many files, how many corrections, and what the
    mandatory gate concluded.
    """
    report = state.evaluation_results
    findings = len(report.findings) if report else 0
    summary = (
        f"{len(state.generated_files)} file(s) generated, "
        f"{state.iteration_count} automatic correction(s), "
        f"quality gate {report.gate_status if report else 'unknown'} "
        f"with {findings} finding(s)."
    )
    return _gate_node(
        state,
        stage=WorkflowStage.FINAL_VALIDATION,
        title="Final review",
        result_ref="generated_files",
        summary=summary,
    )


def _blocked_gate(
    state: ProjectState,
    version: str | None,
    reason: str,
    stage: WorkflowStage = WorkflowStage.EVALUATION,
) -> dict[str, Any]:
    """The blocked outcome, recorded so it cannot be mistaken for a result.

    BLOCKED rather than FAILED: nothing is broken, a required tool is missing,
    and that tells the user whether to fix code or install something.
    """
    return {
        "evaluation_results": EvaluationReport(
            iteration=state.iteration_count,
            impeccable_available=False,
            impeccable_version=version,
            gate_status="blocked",
            findings=[],
        ),
        "current_stage": stage,
        "run_status": RunStatus.BLOCKED,
        "last_error": ErrorBody(
            code=ErrorCode.QUALITY_GATE_BLOCKED.value, message=reason
        ),
        "updated_at": _utcnow(),
    }


async def _run_reviewers(
    state: ProjectState,
    deps: WorkflowDependencies,
    detector_findings: list[EvaluationFinding],
) -> tuple[list[EvaluationFinding], bool]:
    """Run the four reviewers concurrently; each gets only what its role grants.

    Returns the merged findings and whether any reviewer failed.
    """
    assert deps.preview_capture is not None  # checked by the caller
    preview = deps.preview_capture(
        deps.repository.workspace_dir(state.project_id) / "dist",
        state.preview_status.preview_url or "",
    )
    changed = [
        ChangedFilePayload(path=ref.path, content="")
        for ref in state.generated_files
    ]

    plans: list[tuple[AgentRole, Any, dict[str, Any]]] = [
        (AgentRole.VISUAL_UX_CRITIC, deps.visual_ux_critic,
         {"rendered_preview": preview}),
        (AgentRole.ANTI_SLOP_REVIEWER, deps.anti_slop_reviewer,
         {"rendered_preview": preview, "findings": detector_findings}),
        (AgentRole.ACCESSIBILITY_REVIEWER, deps.accessibility_reviewer,
         {"rendered_preview": preview, "changed_files": changed}),
        (AgentRole.CODE_REVIEWER, deps.code_reviewer,
         {"changed_files": changed}),
    ]

    async def run_one(role: AgentRole, agent: Any, inputs: dict[str, Any]):
        context = deps.context_builder.build(
            state, role, task="Review the built result against your remit.", **inputs
        )
        return await agent.run(context)

    results = await asyncio.gather(
        *(run_one(role, agent, inputs) for role, agent, inputs in plans),
        return_exceptions=True,
    )

    merged: list[EvaluationFinding] = []
    failed = False
    for (role, _, _), result in zip(plans, results):
        if isinstance(result, BaseException):
            logger.warning("reviewer raised role=%s: %s", role.value, result)
            failed = True
            continue
        if not result.ok:
            logger.warning("reviewer failed role=%s", role.value)
            failed = True
            continue
        merged.extend(result.output.findings)
    return merged, failed


def _workspace_manifest(workspace_dir: Path) -> list[FileRef]:
    """Every model-writable file actually present, with its size and checksum.

    Only ``src/`` is walked. Builder-owned config is identical in every project by
    definition, and ``node_modules`` and ``dist`` are generated output — listing
    either would bury the generated site in thousands of irrelevant entries.
    """
    src = workspace_dir / "src"
    if not src.is_dir():
        return []

    refs: list[FileRef] = []
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        data = path.read_bytes()
        refs.append(
            FileRef(
                path=path.relative_to(workspace_dir).as_posix(),
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return refs


def _build_failure(
    stage: WorkflowStage, started: datetime, step: str, message: str
) -> dict[str, Any]:
    """Record a build failure in both ``build_status`` and ``last_error``.

    They answer different questions: which step broke, versus what the API
    surfaces. The runner already sanitized the message.
    """
    update = _failure_update(
        stage, AgentFailure(AgentFailureKind.CONTRACT_VIOLATION, message)
    )
    update["build_status"] = BuildStatus(
        status="failed",
        step=step,
        started_at=started,
        finished_at=_utcnow(),
        error=ErrorBody(code=ErrorCode.BUILD_FAILED.value, message=message),
    )
    return update


def _gate_node(
    state: ProjectState,
    *,
    stage: WorkflowStage,
    title: str,
    result_ref: str,
    summary: str,
) -> dict[str, Any]:
    """Pause for the user to approve, redirect or reject a stage result.

    One implementation for every gate, so two gates cannot drift apart. Owns
    ``pending_checkpoint``, ``hitl_history`` and ``approved_decisions``, and
    requires a checkpointer because ``interrupt()`` resumes by re-running.

    Records ``decision.checkpoint_id``, not the local one: resuming re-runs this
    node from the top and mints a fresh id the user never saw.
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

    # The gate the user actually reviewed, not the one rebuilt by this re-execution.
    answered_id = decision.checkpoint_id

    record = HitlRecord(
        checkpoint_id=answered_id,
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
                decision_id=answered_id,
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


def reference_gate_node(state: ProjectState, deps: WorkflowDependencies) -> dict[str, Any]:
    """Review gate for the reference reading.

    Reached only when Reference Analysis actually ran: `docs/workflow.md` 1 makes
    both the stage and its gate conditional on an uploaded image, so a project
    without one is never asked to approve an analysis that does not exist.
    """
    analysis = state.reference_analysis
    summary = "A reading of your reference image is ready for review."
    if analysis is not None:
        # ReferenceAnalysis has no single headline field — every field is optional
        # and any subset may be filled. The first populated one, in the order the
        # model declares them, is the most structural thing the analyzer saw.
        summary = next(
            (
                value
                for value in (
                    analysis.composition,
                    analysis.layout,
                    analysis.hierarchy,
                    analysis.typography,
                )
                if value
            ),
            summary,
        )
    return _gate_node(
        state,
        stage=WorkflowStage.REFERENCE_ANALYSIS,
        title="Review the reference interpretation",
        result_ref="reference_analysis",
        summary=summary,
    )


def route_after_preview(state: ProjectState) -> str:
    """First pass goes to Evaluation; a pass after a fix goes to Re-evaluation.

    The two run identical work (:func:`_evaluate`); only the recorded stage
    differs. Branching here rather than inside the node keeps "which pass is
    this" a property of the path taken, not of a flag the node has to interpret.
    """
    return "re_evaluate" if state.iteration_count > 0 else "evaluate"


def route_after_evaluation(state: ProjectState, max_iterations: int) -> str:
    """Decide what a quality verdict means for the run.

    passed -> validate; blocked -> stop, never continue as though it passed
    (CLAUDE.md 15); failed -> fix while under the limit, otherwise stop and
    surface the findings rather than producing another variation.
    """
    report = state.evaluation_results
    if report is None or report.gate_status == "blocked":
        return "stop"
    if report.gate_status == "passed":
        return "validate"
    if state.iteration_count >= max_iterations:
        logger.warning(
            "correction limit reached project_id=%s iterations=%d; surfacing findings",
            state.project_id,
            state.iteration_count,
        )
        return "exhausted"
    return "fix"


def route_after_design_gate(state: ProjectState) -> str:
    """Approval reaches Reference Analysis only when an image was uploaded.

    With no upload the stage and its gate are skipped entirely: an analysis
    with no image would be invention (docs/workflow.md 1).
    """
    decision = route_after_gate(state)
    if decision == "approved" and not state.reference_images:
        return "skip_reference"
    return decision


def route_after_gate(state: ProjectState) -> str:
    """Advance on approval, re-run the stage on a revision, stop on a failure.

    Shared by every gate; where "revise" goes is decided by the edge map. A
    revision re-runs the stage in place rather than advancing.
    """
    if state.run_status is RunStatus.FAILED:
        return "stop"
    last = state.hitl_history[-1] if state.hitl_history else None
    if last is not None and last.action is HitlAction.APPROVE:
        return "approved"
    return "revise"


def build_workflow(deps: WorkflowDependencies, *, checkpointer: Any | None = None):
    """Compile the workflow graph.

    Invoke with ``config={"configurable": {"thread_id": project_id}}``. With no
    checkpointer the graph has no durability and pending gates are lost.
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

    async def _reference_analysis(state: ProjectState) -> dict[str, Any]:
        return await reference_analysis_node(state, deps)

    def _reference_gate(state: ProjectState) -> dict[str, Any]:
        return reference_gate_node(state, deps)

    async def _frontend_architecture(state: ProjectState) -> dict[str, Any]:
        return await frontend_architecture_node(state, deps)

    async def _build(state: ProjectState) -> dict[str, Any]:
        return await build_node(state, deps)

    def _preview(state: ProjectState) -> dict[str, Any]:
        return preview_node(state, deps)

    async def _evaluation(state: ProjectState) -> dict[str, Any]:
        return await evaluation_node(state, deps)

    async def _targeted_fix(state: ProjectState) -> dict[str, Any]:
        return await targeted_fix_node(state, deps)

    async def _re_evaluation(state: ProjectState) -> dict[str, Any]:
        return await re_evaluation_node(state, deps)

    def _final_validation(state: ProjectState) -> dict[str, Any]:
        return final_validation_node(state, deps)

    def _final_gate(state: ProjectState) -> dict[str, Any]:
        return final_validation_gate_node(state, deps)

    graph.add_node(NODE_REQUIREMENT_ANALYSIS, _requirement_analysis)
    graph.add_node(NODE_PLANNING, _planning)
    graph.add_node(NODE_PLAN_GATE, _plan_gate)
    graph.add_node(NODE_DESIGN, _design)
    graph.add_node(NODE_DESIGN_GATE, _design_gate)
    graph.add_node(NODE_REFERENCE_ANALYSIS, _reference_analysis)
    graph.add_node(NODE_REFERENCE_GATE, _reference_gate)
    graph.add_node(NODE_FRONTEND_ARCHITECTURE, _frontend_architecture)
    graph.add_node(NODE_BUILD, _build)
    graph.add_node(NODE_PREVIEW, _preview)
    graph.add_node(NODE_EVALUATION, _evaluation)
    graph.add_node(NODE_TARGETED_FIX, _targeted_fix)
    graph.add_node(NODE_RE_EVALUATION, _re_evaluation)
    graph.add_node(NODE_FINAL_VALIDATION, _final_validation)
    graph.add_node(NODE_FINAL_GATE, _final_gate)

    graph.add_edge(START, NODE_REQUIREMENT_ANALYSIS)
    graph.add_edge(NODE_REQUIREMENT_ANALYSIS, NODE_PLANNING)
    graph.add_edge(NODE_PLANNING, NODE_PLAN_GATE)
    graph.add_edge(NODE_DESIGN, NODE_DESIGN_GATE)
    graph.add_edge(NODE_REFERENCE_ANALYSIS, NODE_REFERENCE_GATE)

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
    # Approval branches on whether a reference image exists. With none, the
    # Reference stage and its gate are skipped entirely (docs/workflow.md 1).
    graph.add_conditional_edges(
        NODE_DESIGN_GATE,
        route_after_design_gate,
        {
            "approved": NODE_REFERENCE_ANALYSIS,
            "skip_reference": NODE_FRONTEND_ARCHITECTURE,
            "revise": NODE_DESIGN,
            "stop": END,
        },
    )

    graph.add_conditional_edges(
        NODE_REFERENCE_GATE,
        route_after_gate,
        {
            "approved": NODE_FRONTEND_ARCHITECTURE,
            "revise": NODE_REFERENCE_ANALYSIS,
            "stop": END,
        },
    )

    # Architecture, build and preview have no gates (docs/workflow.md 1), so they
    # run straight through. Preview terminates for now: `evaluation` is next and
    # is BLOCKED on Impeccable (CLAUDE.md 15), so the graph must not claim it.
    graph.add_edge(NODE_FRONTEND_ARCHITECTURE, NODE_BUILD)
    graph.add_edge(NODE_BUILD, NODE_PREVIEW)

    # A fix changes src/, so the correction loop runs back through build and
    # preview: re-evaluating a stale dist/ would review the previous version
    # (docs/workflow.md 5, "modify -> validate -> re-render -> re-evaluate").
    # Which evaluation it lands in is decided by whether a fix has happened.
    graph.add_conditional_edges(
        NODE_PREVIEW,
        route_after_preview,
        {"evaluate": NODE_EVALUATION, "re_evaluate": NODE_RE_EVALUATION},
    )

    # Both passes share one verdict router; only the destinations are written
    # twice, because the stages they belong to differ.
    def _verdict(state: ProjectState) -> str:
        return route_after_evaluation(state, deps.max_auto_iterations)

    # "stop" and "exhausted" both END, deliberately as separate labels: a
    # blocked gate and an exhausted budget are different situations for the
    # user, and collapsing them would hide which one occurred.
    verdict_targets = {
        "validate": NODE_FINAL_VALIDATION,
        "fix": NODE_TARGETED_FIX,
        "stop": END,
        "exhausted": END,
    }
    graph.add_conditional_edges(NODE_EVALUATION, _verdict, verdict_targets)
    graph.add_conditional_edges(NODE_RE_EVALUATION, _verdict, verdict_targets)

    graph.add_edge(NODE_TARGETED_FIX, NODE_BUILD)
    graph.add_edge(NODE_FINAL_VALIDATION, NODE_FINAL_GATE)

    # Approval ends v1. `external_action` is next and is gated on MCP, which is
    # not built, so the graph does not claim it. A revision at the final gate
    # re-runs the correction loop rather than regenerating the project.
    graph.add_conditional_edges(
        NODE_FINAL_GATE,
        route_after_gate,
        {"approved": END, "revise": NODE_TARGETED_FIX, "stop": END},
    )

    if checkpointer is None:
        logger.warning(
            "workflow compiled WITHOUT a checkpointer: runs are not durable, and the "
            "gates cannot pause and resume across a restart (docs/state.md 5.1)"
        )

    return graph.compile(checkpointer=checkpointer)