"""The canonical project state and the typed records it is composed of.

WHY THIS FILE EXISTS
    ``ProjectState`` is the single source of truth for approved decisions and stage
    outputs. There is exactly one canonical state model — it is NOT duplicated across
    a separate "graph state" and "app state". This same object is what flows through
    the LangGraph workflow; nodes return partial updates of the fields they own
    (docs/state.md 1, 3).

    The supporting records live here too, because they are the state's constituent
    parts. Defining them beside it keeps the auditable shape of a project readable in
    one place instead of scattered across modules.

LAYER
    Schemas (``backend/schemas/``). Depends only on the standard library, Pydantic and
    the shared vocabulary in ``enums.py``. It imports nothing from api, graph, agents,
    services or config, so it cannot create a cycle.

WHAT THIS FILE IS NOT
    - It is not the API contract. Request/response wrappers and the public projection
      ``ProjectStateResponse`` belong in ``backend/schemas/api.py``, which will reuse
      these types rather than redefine them.
    - It is not persistence. How state is stored (interim artifacts now,
      Supabase/PostgreSQL once the schema is defined) belongs in ``backend/memory/``.
    - It is not the graph. Node wiring and transitions belong in ``backend/graph/``.

Spec: docs/state.md 2, 2.1, 5.3; docs/api.md 4; docs/agents.md 4; docs/design.md 5.3.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.enums import (
    AgentRole,
    HitlAction,
    RunStatus,
    Severity,
    WorkflowStage,
)

# Bumped whenever a persisted field changes meaning, so stored artifacts can be
# recognised as stale rather than silently misread (docs/state.md 2).
SCHEMA_VERSION = 1

# Status vocabularies that docs/api.md 4 defines inline on their models rather than in
# the shared-enum section. Kept as Literals so they stay typed without widening the
# shared vocabulary in enums.py beyond what docs/api.md 3 lists.
GateStatus = Literal["passed", "failed", "blocked"]
BuildState = Literal["pending", "running", "succeeded", "failed"]
PreviewFreshness = Literal["fresh", "stale"]


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp. All state timestamps are UTC (docs/state.md 2)."""
    return datetime.now(timezone.utc)


def new_project_id() -> str:
    """``proj_`` + uuid4 hex — stable for the project's whole life."""
    return f"proj_{uuid4().hex}"


def new_session_id() -> str:
    """``sess_`` + uuid4 hex — one client attachment; carries no approved decisions."""
    return f"sess_{uuid4().hex}"


def new_checkpoint_id() -> str:
    """``ckpt_`` + uuid4 hex — identifies one HITL gate instance."""
    return f"ckpt_{uuid4().hex}"


class _Model(BaseModel):
    """Shared base: reject unknown fields so drift surfaces instead of being ignored."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Supporting records
# ---------------------------------------------------------------------------


class ErrorBody(_Model):
    """A safe, user-facing failure. Never carries secrets, stack traces or paths."""

    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class FileRef(_Model):
    """One generated file in the project workspace."""

    path: str = Field(description="Workspace-relative, forward slashes.")
    size_bytes: int = Field(ge=0)
    sha256: str
    updated_at: datetime = Field(default_factory=_utcnow)


class ReferenceImageRef(_Model):
    """An uploaded reference image. Untrusted input; never executed."""

    image_id: str = Field(description="Server-generated; the client filename is never reused.")
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    uploaded_at: datetime = Field(default_factory=_utcnow)


class HitlOption(_Model):
    """One AI-generated modification option offered at a gate."""

    option_id: str
    label: str
    description: str
    impact: str = Field(description="What changes if this option is chosen.")


class HitlCheckpoint(_Model):
    """A gate that is currently blocking the workflow.

    ``options`` is derived from the actual generated result and may be empty; it is
    never padded to a fixed count. ``allows_custom`` is always true — the custom path
    must be able to override the stage (docs/workflow.md 3).
    """

    checkpoint_id: str = Field(default_factory=new_checkpoint_id)
    stage: WorkflowStage
    title: str
    summary: str = Field(description="What the agent produced, in user terms.")
    result_ref: str = Field(description="Which state field holds the result.")
    options: list[HitlOption] = Field(default_factory=list)
    allows_custom: Literal[True] = True
    created_at: datetime = Field(default_factory=_utcnow)


class HitlRecord(_Model):
    """An audit entry for a resolved gate."""

    checkpoint_id: str
    stage: WorkflowStage
    action: HitlAction
    option_id: str | None = None
    instruction: str | None = None
    decided_at: datetime = Field(default_factory=_utcnow)


class ApprovedDecision(_Model):
    """A decision the user has fixed. Treated as a constraint, never re-asked.

    Only the HITL resume path or an explicit user instruction may create one; agents
    receive these read-only (docs/state.md 2.1, 6).
    """

    decision_id: str
    stage: WorkflowStage
    statement: str = Field(description="The decision, in user terms.")
    origin: HitlAction | None = Field(
        default=None, description="Which HITL action fixed it, when it came from a gate."
    )
    decided_at: datetime = Field(default_factory=_utcnow)


class RejectedOption(_Model):
    """An option the user turned down. Kept so it is not re-proposed."""

    checkpoint_id: str
    option_id: str
    stage: WorkflowStage
    rejected_at: datetime = Field(default_factory=_utcnow)


class McpActionRecord(_Model):
    """An external action that was actually performed, for audit."""

    action_id: str
    action_type: str
    status: Literal["succeeded", "failed"]
    performed_at: datetime = Field(default_factory=_utcnow)


class BuildStatus(_Model):
    """Outcome of the generated-project build. ``error`` is sanitized."""

    status: BuildState = "pending"
    step: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: ErrorBody | None = None


class PreviewStatus(_Model):
    """Where the built preview is served from, and whether it is current."""

    preview_url: str | None = None
    built_at: datetime | None = None
    status: PreviewFreshness = "stale"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class EvaluationFinding(_Model):
    """A single finding from one reviewer.

    ``conflicts_with_approved_decision`` routes the finding: when true it goes to the
    user, never to the Fixer, because "fixing" an approved decision is forbidden
    (docs/design.md 5.5, docs/agents.md 4.11).
    """

    finding_id: str
    source: AgentRole
    category: str = Field(description='e.g. "anti_slop", "a11y", "ux", "code".')
    severity: Severity
    summary: str
    location: str | None = None
    suggested_fix: str | None = None
    conflicts_with_approved_decision: bool = False


class EvaluationReport(_Model):
    """The result of one evaluation pass.

    Impeccable is a MANDATORY gate with no substitute. This model enforces that
    structurally: a report whose detector did not run cannot claim to have passed
    (CLAUDE.md 15, docs/design.md 5.1-5.3).
    """

    iteration: int = Field(ge=0)
    impeccable_available: bool
    impeccable_version: str | None = Field(
        default=None, description="Actual installed version; never assumed."
    )
    gate_status: GateStatus
    findings: list[EvaluationFinding] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _enforce_blocked_gate(self) -> EvaluationReport:
        """INVARIANT: ``impeccable_available is False`` implies ``gate_status`` is
        ``"blocked"``. A blocked gate may never be recorded as passed or failed."""
        if not self.impeccable_available and self.gate_status != "blocked":
            raise ValueError(
                "Impeccable did not run, so gate_status must be 'blocked'; a gate "
                "that did not run must never be reported as completed"
            )
        return self


class ReviewFindings(_Model):
    """What one reviewer returns: its findings, and nothing else.

    A container exists because structured output needs a model — LangChain rejects a
    bare ``list[EvaluationFinding]``, and :class:`AgentDefinition` requires a
    ``BaseModel`` subclass. It is a technical necessity, not extra structure.

    All four reviewers share it. They are distinguished by the ``source`` on each
    finding, not by four near-identical models.

    WHY THE ANTI-SLOP REVIEWER DOES NOT REPORT GATE STATUS
        ``docs/agents.md`` 4.8 describes that role's output as findings *plus*
        ``impeccable_available``, ``impeccable_version`` and ``gate_status``. Those
        three are deliberately absent here.

        Whether the detector actually ran is a fact the runtime observes, not a claim
        the model should make. If a model could set ``impeccable_available: true``,
        the mandatory gate would be forgeable by the very thing it exists to check —
        and "never report the check as completed when it did not run" (CLAUDE.md 15)
        would depend on the model's honesty rather than on the code.

        So the evaluation layer probes Impeccable, fills those fields on
        :class:`EvaluationReport`, and the reviewer contributes only findings.
    """

    findings: list[EvaluationFinding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent stage outputs
# ---------------------------------------------------------------------------


class NormalizedRequirements(_Model):
    """Requirement Analyzer output. Preserves explicit user decisions verbatim."""

    goal: str
    audience: str | None = None
    surfaces: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    explicit_decisions: list[str] = Field(default_factory=list)
    interactions: list[str] = Field(default_factory=list)
    responsive_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    input_density: Literal["sparse", "detailed"] = Field(
        description="Drives adaptive HITL: detailed input earns fewer questions."
    )


class Plan(_Model):
    """Planner output. Structured enough that later stages never re-read the prompt."""

    objective: str
    audience: str | None = None
    pages: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    content_hierarchy: str | None = None
    cta_strategy: str | None = None
    interactions: list[str] = Field(default_factory=list)
    responsive_requirements: list[str] = Field(default_factory=list)
    design_intent: str | None = None
    technical_constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class DesignSpec(_Model):
    """Design Director output: one coherent visual system, not loose styling choices.

    This is the single holder of the project's visual decisions. A user-specified
    font, palette or direction recorded here is a constraint (skills.md 15, 31).
    """

    font_families: list[str] = Field(default_factory=list)
    typography_scale: dict[str, str] = Field(default_factory=dict)
    weights: list[str] = Field(default_factory=list)
    color_system: dict[str, str] = Field(default_factory=dict)
    spacing_rules: list[str] = Field(default_factory=list)
    layout_grid: str | None = None
    density: str | None = None
    radius: str | None = None
    button_language: str | None = None
    card_language: str | None = None
    image_treatment: str | None = None
    motion_principles: list[str] = Field(default_factory=list)
    visual_tone: str | None = None
    rationale: str | None = None


class ReferenceAnalysis(_Model):
    """Reference Analyzer output: design intent, plus conflicts against the approved
    system. Layout and structure are adoptable by default; color, typography and
    radius are not (docs/design.md 3)."""

    composition: str | None = None
    layout: str | None = None
    hierarchy: str | None = None
    typography: str | None = None
    color_relationships: str | None = None
    spacing: str | None = None
    container_treatment: str | None = None
    navigation: str | None = None
    hero_structure: str | None = None
    decorative_language: str | None = None
    interaction_cues: str | None = None
    responsive_implications: str | None = None
    conflicts_with_approved_system: list[str] = Field(default_factory=list)
    adoptable_without_conflict: list[str] = Field(default_factory=list)


class ComponentNode(_Model):
    """One node of the component tree."""

    name: str
    purpose: str | None = None
    children: list[ComponentNode] = Field(default_factory=list)


class ComponentTree(_Model):
    """The approved component architecture for the generated project."""

    root: list[ComponentNode] = Field(default_factory=list)


class FrontendArchitecture(_Model):
    """Frontend Architect output. Reuse of existing components is preferred over new
    ones (docs/agents.md 4.5)."""

    pages: list[str] = Field(default_factory=list)
    component_tree: ComponentTree | None = None
    reused_components: list[str] = Field(default_factory=list)
    new_components: list[str] = Field(default_factory=list)
    responsive_strategy: str | None = None
    file_plan: list[str] = Field(default_factory=list)


class BuildResult(_Model):
    """Builder output: a report of what it changed, not the content it produced.

    The file contents live in the workspace; this records which paths were touched
    so the next stages know what to evaluate and the user can see the scope of a
    change without reading a diff.

    ``deviations_from_plan`` is the field that matters most. A builder that silently
    departs from the approved architecture leaves nobody able to tell an intentional
    improvement from a mistake — so a departure is reported, not hidden
    (docs/agents.md 4.6, CLAUDE.md 26).
    """

    files_written: list[str] = Field(
        default_factory=list, description="Newly created, workspace-relative paths."
    )
    files_modified: list[str] = Field(
        default_factory=list, description="Existing files changed, same path form."
    )
    notes: list[str] = Field(default_factory=list)
    deviations_from_plan: list[str] = Field(
        default_factory=list,
        description="Anything built differently from the approved architecture.",
    )

    @model_validator(mode="after")
    def _paths_stay_inside_the_workspace(self) -> BuildResult:
        """Reject reported paths that are absolute or climb out of the workspace.

        Containment is enforced by the write tool, which is what actually protects
        the filesystem. This is a second, cheaper check on the *report*: a path shape
        that could not have been written legitimately means the report is wrong, and
        a wrong report is what later stages would evaluate against
        (docs/security.md 3.4).
        """
        for field in ("files_written", "files_modified"):
            for path in getattr(self, field):
                if (
                    not path
                    or path.startswith(("/", "\\"))
                    or ".." in path.replace("\\", "/").split("/")
                    or (len(path) > 1 and path[1] == ":")
                ):
                    raise ValueError(
                        f"{field} contains a path outside the workspace: {path!r}"
                    )
        return self


class FixResult(_Model):
    """Fixer output: what was corrected, what was not, and what the user must decide.

    The three "not fixed" fields are the point of this model. A fix pass that
    silently drops a finding leaves everyone believing it was handled, and the
    re-evaluation then has to rediscover it. Separating them says *why* each one was
    left:

    - ``unresolved`` — attempted or understood, but not fixed. Still the Fixer's
      problem, and a candidate for the next iteration.
    - ``escalated_to_user`` — deliberately not touched, because acting would
      contradict something the user approved. A finding marked
      ``conflicts_with_approved_decision`` must land here and nowhere else: the
      Fixer is forbidden from undoing an approved decision, so quietly "resolving"
      one would be the most damaging thing this role could do
      (docs/agents.md 4.11, docs/design.md 5.5).

    A finding may appear in exactly one of the three lists. Counting the same
    finding as both addressed and unresolved would make the iteration limit
    meaningless, because nobody could tell whether progress was real.
    """

    findings_addressed: list[str] = Field(
        default_factory=list, description="Finding ids actually corrected."
    )
    files_modified: list[str] = Field(
        default_factory=list, description="Workspace-relative paths changed."
    )
    unresolved: list[str] = Field(
        default_factory=list, description="Finding ids not fixed in this pass."
    )
    escalated_to_user: list[str] = Field(
        default_factory=list,
        description="Finding ids that conflict with an approved decision.",
    )
    rationale: str | None = Field(
        default=None,
        description="Why these changes; recorded per iteration in the history.",
    )

    @model_validator(mode="after")
    def _one_outcome_per_finding(self) -> FixResult:
        """No finding may be counted in two outcome lists at once."""
        buckets = {
            "findings_addressed": set(self.findings_addressed),
            "unresolved": set(self.unresolved),
            "escalated_to_user": set(self.escalated_to_user),
        }
        names = list(buckets)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                overlap = sorted(buckets[first] & buckets[second])
                if overlap:
                    raise ValueError(
                        f"finding(s) {overlap} appear in both {first} and {second}; "
                        "each finding has exactly one outcome"
                    )
        return self

    @model_validator(mode="after")
    def _paths_stay_inside_the_workspace(self) -> FixResult:
        """Reject reported paths that could not have been written legitimately.

        The scoped write tool is what protects the filesystem; this checks the
        *report*, because a wrong report is what the next stage evaluates against
        (docs/security.md 3.4).
        """
        for path in self.files_modified:
            if (
                not path
                or path.startswith(("/", "\\"))
                or ".." in path.replace("\\", "/").split("/")
                or (len(path) > 1 and path[1] == ":")
            ):
                raise ValueError(
                    f"files_modified contains a path outside the workspace: {path!r}"
                )
        return self


# ---------------------------------------------------------------------------
# Canonical state
# ---------------------------------------------------------------------------


class ProjectState(_Model):
    """The canonical, durable state of one project.

    This object IS the LangGraph graph state (docs/state.md 3). Each field has exactly
    one owning node; a node returns only the fields it owns, and the append-only audit
    lists are never replaced wholesale.
    """

    # --- Identity ---
    project_id: str = Field(default_factory=new_project_id)
    session_id: str = Field(default_factory=new_session_id)
    title: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    schema_version: int = SCHEMA_VERSION

    # --- Requirements & planning ---
    user_request: str = ""
    normalized_requirements: NormalizedRequirements | None = None
    requirement_conflicts: list[str] = Field(default_factory=list)
    requirement_gaps: list[str] = Field(default_factory=list)
    plan: Plan | None = None

    # --- Design ---
    # design_spec is the SINGLE holder of the visual system. The individual
    # font/typography/color/spacing/component fields listed alongside it in
    # docs/state.md 2 are deliberately not duplicated here: two copies of the same
    # decision is exactly the divided source of truth CLAUDE.md 9 forbids. Read them
    # from design_spec.
    design_spec: DesignSpec | None = None

    # --- Reference ---
    reference_images: list[ReferenceImageRef] = Field(default_factory=list)
    reference_analysis: ReferenceAnalysis | None = None
    approved_reference_direction: str | None = None

    # --- Architecture & build ---
    frontend_architecture: FrontendArchitecture | None = None
    component_tree: ComponentTree | None = None
    generated_files: list[FileRef] = Field(default_factory=list)
    file_versions: dict[str, str] = Field(
        default_factory=dict, description="Workspace path -> content hash."
    )
    build_status: BuildStatus = Field(default_factory=BuildStatus)
    preview_status: PreviewStatus = Field(default_factory=PreviewStatus)

    # --- Evaluation ---
    # evaluation_results holds every finding; the per-reviewer lists in
    # docs/state.md 2 are derivable from findings[].source and are therefore not
    # stored separately (CLAUDE.md 9). Use findings_by_source().
    evaluation_results: EvaluationReport | None = None
    iteration_count: int = Field(default=0, ge=0)

    # --- Control & audit ---
    current_stage: WorkflowStage = WorkflowStage.REQUIREMENT_ANALYSIS
    run_status: RunStatus = RunStatus.IDLE
    next_action: str | None = None
    pending_checkpoint: HitlCheckpoint | None = None
    hitl_history: list[HitlRecord] = Field(default_factory=list)
    approved_decisions: list[ApprovedDecision] = Field(default_factory=list)
    rejected_options: list[RejectedOption] = Field(default_factory=list)
    mcp_action_history: list[McpActionRecord] = Field(default_factory=list)
    last_error: ErrorBody | None = None

    # --- Transient ---
    # Per-node working data. Excluded from serialization so it can never reach a
    # persisted artifact or an API response, and never be mistaken for a decision
    # (docs/state.md 2). Cleared when a stage completes.
    node_scratch: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def findings_by_source(self, source: AgentRole) -> list[EvaluationFinding]:
        """Findings from one reviewer, derived rather than stored separately."""
        if self.evaluation_results is None:
            return []
        return [f for f in self.evaluation_results.findings if f.source is source]

    def is_awaiting_hitl(self) -> bool:
        """True when a gate is blocking. Kept as one predicate so callers do not
        each re-derive the pairing of run_status and pending_checkpoint."""
        return (
            self.run_status is RunStatus.AWAITING_HITL
            and self.pending_checkpoint is not None
        )
