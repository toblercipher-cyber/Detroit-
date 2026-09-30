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
from backend.agents.builder import BuilderAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.agents.planner import PlannerAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.context.builder import (
    ContextBuilder,
    ContextPermissionError,
    ReferenceImagePayload,
)
from backend.memory.artifacts import StateRepository, StateRepositoryError
from backend.tools.build_runner import BuildRunner
from backend.tools.template import scaffold_workspace
from backend.tools.workspace import WorkspaceFiles
from backend.schemas.api import ErrorCode, HitlDecisionRequest
from backend.schemas.enums import AgentRole, HitlAction, RunStatus, WorkflowStage
from backend.schemas.state import (
    ApprovedDecision,
    BuildStatus,
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

# Where a stage parks a custom instruction until it re-runs. One key per stage,
# so a refinement meant for the plan can never be picked up by the design pass.
_INSTRUCTION_KEYS: dict[WorkflowStage, str] = {
    WorkflowStage.PLANNING: "planning_instruction",
    WorkflowStage.DESIGN: "design_instruction",
    WorkflowStage.REFERENCE_ANALYSIS: "reference_instruction",
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
    reference_analyzer: ReferenceAnalyzerAgent
    frontend_architect: FrontendArchitectAgent
    # A factory, not an agent: the Builder holds a handle to ONE workspace, so
    # it is built per project rather than shared (backend/agents/builder.py).
    build_builder: Callable[[WorkspaceFiles], BuilderAgent]
    build_runner: BuildRunner
    # Reference Analysis needs the image BYTES, which state does not carry;
    # this is the only reason the graph touches persistence at all.
    repository: StateRepository


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


async def reference_analysis_node(
    state: ProjectState, deps: WorkflowDependencies
) -> dict[str, Any]:
    """Read design intent out of an uploaded reference image.

    Owns: ``reference_analysis``. Optional — the router decides whether this runs
    at all; by the time it does, an image is guaranteed to exist
    (docs/workflow.md 1).

    THE IMAGE IS LOADED HERE, NOT CARRIED IN STATE
        ``ProjectState`` holds a ``ReferenceImageRef``: id, type, size, checksum.
        Analysis needs pixels, and a checksum cannot be looked at. The bytes are
        read from the repository for this one call and discarded, so base64 never
        enters ``project_state.json`` (docs/state.md 5.1).

    WHICH IMAGE
        The most recent upload. Uploading again is how a user replaces a
        reference, so the newest is the one they mean; the analyzer takes one
        image and inventing a merge across several would be fabrication.

    The image is untrusted. Text visible inside it is data, never instruction —
    the agent's own prompt carries that guard (docs/security.md 5).
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
    """Decide what components exist and how they nest, before anything is written.

    Owns: ``frontend_architecture`` and ``component_tree``.

    NO GATE AFTER THIS STAGE
        ``docs/workflow.md`` 1 marks it "no". The user has already approved the
        plan, the design system and — when there was one — the reference reading.
        Architecture follows from those three; asking for a fourth approval of a
        component list would be a checkpoint without a decision in it
        (CLAUDE.md 11).

    WHY TWO FIELDS ARE WRITTEN
        The agent returns one ``FrontendArchitecture``, which nests the tree.
        ``ProjectState`` keeps ``component_tree`` at the top level as well,
        because the Builder's context grant names that field directly. Lifting it
        here is what makes the Builder's slice resolvable; leaving it nested would
        hand the Builder an empty tree while the data sat one level down.

    COMPONENT INVENTORY
        Supplied only when the project has files. On a first pass there are none,
        and the agent is told so explicitly rather than left to infer it. On a
        later pass the inventory is what makes "reuse before new" enforceable.
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
    """Write the approved architecture into a workspace and compile it.

    Owns: ``generated_files`` and ``build_status``. No gate after it
    (``docs/workflow.md`` 1) — nothing here is a decision the user makes; a build
    either compiles or it does not, and what they review is the preview.

    THREE THINGS HAPPEN, IN THIS ORDER
        1. Scaffold. Builder-owned config is rewritten every time, so a model that
           somehow altered one does not get to build against its own version
           (docs/architecture.md 5.2).
        2. The Builder agent writes under ``src/``. It holds a handle to this one
           workspace and can express nothing outside it.
        3. The toolchain runs: install, typecheck, build. Real exit codes decide.

    A FAILED BUILD IS A RESULT, NOT AN EXCEPTION
        Generated code failing to compile is the ordinary case this pipeline
        exists to detect and fix. It is recorded in ``build_status`` with a
        sanitized message so the Fixer has something to act on, and the stage is
        marked failed so the workflow stops rather than previewing nothing
        (CLAUDE.md 26).

    WHAT IS RECORDED IS WHAT HAPPENED
        ``generated_files`` comes from the writes the workspace tool actually
        performed, not from what the model said it wrote. The agent's own
        ``BuildResult`` is assembled the same way, which is why the two cannot
        disagree.
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
    """Publish the built output and record where it can be seen.

    Owns: ``preview_status``. No agent and no gate (``docs/workflow.md`` 1) —
    there is no judgement here, only the fact of what was built. It is
    synchronous because nothing in it awaits anything.

    THE URL IS RELATIVE, DELIBERATELY
        ``/preview/{project_id}/`` rather than an absolute URL. The backend does
        not know the origin it is reached on — behind a proxy, a tunnel, or a
        different port, ``api_host``/``api_port`` describe where it *listens*,
        not where a browser finds it. Baking those into stored state would
        produce a value that is wrong everywhere except one deployment, and
        wrong in a field that is persisted. The client resolves it against the
        origin it already used.

    FRESHNESS
        ``fresh`` here, and that is a statement about this moment: the preview
        was just produced from the current files. It becomes ``stale`` when
        source changes afterwards — which happens in the Targeted Fix stage, so
        the transition belongs there, with the code that changes the files.
        ``PreviewStatus`` carries no snapshot to compare against, so this node
        cannot compute staleness and does not pretend to.

    A MISSING dist/ IS A FAILURE, NOT AN EMPTY PREVIEW
        The Build node already treats "succeeded but produced nothing" as a
        failure, so reaching here without output means something removed it
        between stages. Recording a preview URL that serves 404 would send the
        user, and the Visual & UX Critic, to look at nothing.
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
    """Record a build failure in both ``build_status`` and the stage error.

    Two places because they answer different questions: ``build_status`` says
    which step broke, and ``last_error`` is what the API surfaces. The message is
    already sanitized by the runner; nothing raw reaches either
    (docs/architecture.md 5.5).
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

    One implementation for every gate. The gates differ only in which stage they
    belong to and what they are reviewing; the mechanics — raising a checkpoint,
    interrupting, recording the decision — are identical, and writing them twice is
    how two gates end up behaving differently by accident.

    This is the HITL boundary. It owns ``pending_checkpoint``, ``hitl_history`` and
    ``approved_decisions``, and is the only path permitted to write the latter two
    (docs/state.md 3).

    Requires a checkpointer: ``interrupt()`` resumes by re-running the node, which is
    only possible if the paused run was persisted (docs/state.md 5.1).

    WHICH CHECKPOINT ID GETS RECORDED
        Resuming re-executes this node from the top, so the ``HitlCheckpoint`` below
        is rebuilt with a **fresh** id before ``interrupt()`` returns the stored
        decision. Recording that rebuilt id would file the decision against a gate
        the user never saw, and the audit trail would not join back to the review.

        The id the user answered survives on the decision itself, so the records use
        ``decision.checkpoint_id``. It is not taken on trust: the service verifies it
        against the pending checkpoint before resuming, and a mismatch is rejected as
        ``checkpoint_already_resolved`` rather than reaching this node.
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


def route_after_design_gate(state: ProjectState) -> str:
    """Approval goes to Reference Analysis only when there is an image to read.

    This is the conditional in `docs/workflow.md` 1. With no upload the stage is
    skipped **entirely** — not run against nothing, and not stubbed — because an
    analysis with no image would be invention, and a gate asking the user to
    approve it would be worse.
    """
    decision = route_after_gate(state)
    if decision == "approved" and not state.reference_images:
        return "skip_reference"
    return decision


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
    graph.add_edge(NODE_PREVIEW, END)

    if checkpointer is None:
        logger.warning(
            "workflow compiled WITHOUT a checkpointer: runs are not durable, and the "
            "gates cannot pause and resume across a restart (docs/state.md 5.1)"
        )

    return graph.compile(checkpointer=checkpointer)