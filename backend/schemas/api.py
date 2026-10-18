"""Request and response models for the HTTP contract.

WHY THIS FILE EXISTS
    FastAPI route handlers stay thin: they validate input, delegate to a service and
    return a typed response. This file holds the wire contract those handlers speak,
    so the shape of the API is reviewable in one place rather than inferred from
    scattered route signatures.

    It deliberately REUSES the domain types from ``state.py`` instead of redefining
    them. A second definition of ``EvaluationReport`` here would let the API and the
    state drift apart — and would quietly drop the mandatory-gate invariant that
    model enforces.

LAYER
    Schemas (``backend/schemas/``). Depends on the standard library, Pydantic,
    ``enums.py`` and ``state.py``. It imports nothing from api routes, graph, agents,
    services or config, so it cannot create a cycle.

THE REDACTION BOUNDARY
    ``ProjectStateResponse`` is a PUBLIC PROJECTION of ``ProjectState``, not the state
    itself. It must never carry the raw prompt, provider keys, internal agent
    reasoning, absolute filesystem paths or checkpointer internals. Build it only via
    ``ProjectStateResponse.from_state()`` so that redaction happens in exactly one
    place and cannot be forgotten at a call site (docs/api.md 4, docs/security.md 6).

Spec: docs/api.md 4 and 5; docs/security.md 6; docs/state.md 2.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.enums import HitlAction, RunStatus, WorkflowStage
from backend.schemas.state import (
    ApprovedDecision,
    ComponentTree,
    DesignSpec,
    ErrorBody,
    EvaluationReport,
    FileRef,
    FrontendArchitecture,
    HitlRecord,
    McpActionRecord,
    NormalizedRequirements,
    Plan,
    PreviewFreshness,
    ProjectState,
    ReferenceAnalysis,
)

# Re-exported so route modules import one vocabulary from one place. These are the
# same objects as in state.py, never copies.
from backend.schemas.state import (  # noqa: F401  (re-export)
    BuildStatus as BuildStatusResponse,
    EvaluationFinding,
    HitlCheckpoint,
    HitlOption,
)

ExternalActionType = Literal[
    "github_branch", "github_commit", "github_push", "github_pr"
]


class _Model(BaseModel):
    """Shared base: reject unknown fields so a client typo fails loudly."""

    model_config = ConfigDict(extra="forbid")


class ErrorCode(str, Enum):
    """Stable, machine-readable error codes (docs/api.md 5).

    Defined as an enum so a route cannot invent or misspell a code. Values are part
    of the wire format.
    """

    VALIDATION_ERROR = "validation_error"
    # No usable access token: absent, malformed, expired, wrong issuer or
    # audience. Never used for "this is not yours" -- that is a 404, so the
    # endpoint cannot be used to probe which project ids exist (docs/api.md 1.1).
    UNAUTHENTICATED = "unauthenticated"
    # A verified caller attempting something their account may not do. Reserved;
    # ownership is deliberately NOT expressed with it.
    FORBIDDEN = "forbidden"
    PROJECT_NOT_FOUND = "project_not_found"
    INVALID_STAGE_FOR_ACTION = "invalid_stage_for_action"
    NO_PENDING_CHECKPOINT = "no_pending_checkpoint"
    CHECKPOINT_ALREADY_RESOLVED = "checkpoint_already_resolved"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    UPLOAD_TOO_LARGE = "upload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    PREVIEW_NOT_AVAILABLE = "preview_not_available"
    # Impeccable unavailable: the mandatory gate cannot run and the workflow stops.
    QUALITY_GATE_BLOCKED = "quality_gate_blocked"
    BUILD_FAILED = "build_failed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED = "approval_expired"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    UPSTREAM_ERROR = "upstream_error"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


class ErrorResponse(_Model):
    """The single envelope for every non-2xx response.

    Carries only safe, user-facing detail. Keys, environment values, stack traces,
    internal prompts, absolute paths, MCP credentials and raw provider responses are
    never included; full detail goes to the server log via ``request_id``
    (docs/security.md 6).
    """

    error: ErrorBody


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(_Model):
    """Liveness plus a boolean-only credential check.

    ``model_configured`` is a bool and nothing more — never the key, never a prefix
    of it (docs/runtime.md 3.4).
    """

    status: Literal["ok", "degraded"]
    version: str
    model_configured: bool


# ---------------------------------------------------------------------------
# Projects & sessions
# ---------------------------------------------------------------------------


class CreateProjectRequest(_Model):
    """Start a project from a natural-language prompt."""

    prompt: str = Field(min_length=1, max_length=8000)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    surface_hint: str | None = Field(
        default=None, description='e.g. "landing_page", "pricing_page".'
    )


class ProjectCreatedResponse(_Model):
    project_id: str
    session_id: str
    stage: WorkflowStage
    status: RunStatus
    created_at: datetime


class ProjectSummary(_Model):
    """One project in a listing.

    Carries what a dashboard needs so it does not have to fetch every project
    individually: `list_summaries` already loads the full state, so these cost
    nothing extra here and save N requests there.
    """

    project_id: str
    title: str
    stage: WorkflowStage
    status: RunStatus
    updated_at: datetime
    created_at: datetime
    iteration_count: int = Field(default=0, ge=0)


class ProjectSummaryList(_Model):
    projects: list[ProjectSummary] = Field(default_factory=list)


class StatusResponse(_Model):
    """The cheap polling endpoint, and the SSE fallback.

    ``stage`` and ``status`` are orthogonal and always reported together so the
    Chatbot page can render the pair (docs/api.md 3.2).
    """

    project_id: str
    stage: WorkflowStage
    status: RunStatus
    iteration_count: int = Field(ge=0)
    max_iterations: int = Field(ge=1)
    awaiting_checkpoint_id: str | None = None
    preview_available: bool = False
    last_error: ErrorBody | None = None
    updated_at: datetime


class ProjectStateResponse(_Model):
    """Public projection of ``ProjectState``.

    Deliberately omits ``user_request`` (the raw prompt), ``node_scratch`` (internal
    agent working data), ``file_versions`` and ``pending_checkpoint`` internals. Use
    ``from_state()`` rather than constructing this directly.
    """

    project_id: str
    session_id: str
    title: str
    stage: WorkflowStage
    status: RunStatus
    iteration_count: int = Field(ge=0)
    normalized_requirements: NormalizedRequirements | None = None
    plan: Plan | None = None
    design_spec: DesignSpec | None = None
    reference_analysis: ReferenceAnalysis | None = None
    frontend_architecture: FrontendArchitecture | None = None
    component_tree: ComponentTree | None = None
    generated_files: list[FileRef] = Field(default_factory=list)
    evaluation_results: EvaluationReport | None = None
    approved_decisions: list[ApprovedDecision] = Field(default_factory=list)
    hitl_history: list[HitlRecord] = Field(default_factory=list)
    mcp_action_history: list[McpActionRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_state(cls, state: ProjectState) -> ProjectStateResponse:
        """Build the projection, enumerating every exposed field explicitly.

        Explicit field-by-field copying is the point: a new field added to
        ``ProjectState`` is NOT exposed until someone deliberately adds it here.
        """
        return cls(
            project_id=state.project_id,
            session_id=state.session_id,
            title=state.title,
            stage=state.current_stage,
            status=state.run_status,
            iteration_count=state.iteration_count,
            normalized_requirements=state.normalized_requirements,
            plan=state.plan,
            design_spec=state.design_spec,
            reference_analysis=state.reference_analysis,
            frontend_architecture=state.frontend_architecture,
            component_tree=state.component_tree,
            generated_files=state.generated_files,
            evaluation_results=state.evaluation_results,
            approved_decisions=state.approved_decisions,
            hitl_history=state.hitl_history,
            mcp_action_history=state.mcp_action_history,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )


class UserMessageRequest(_Model):
    message: str = Field(min_length=1, max_length=8000)


class MessageAcceptedResponse(_Model):
    accepted: bool
    stage: WorkflowStage
    status: RunStatus


# ---------------------------------------------------------------------------
# HITL
# ---------------------------------------------------------------------------


class HitlDecisionRequest(_Model):
    """A user decision at a gate.

    The payload shape is constrained per action so an ambiguous submission — such as
    ``approve`` carrying an instruction — is rejected rather than silently
    half-applied (docs/api.md 4, docs/workflow.md 3).
    """

    checkpoint_id: str
    action: HitlAction
    option_id: str | None = None
    instruction: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _check_payload_matches_action(self) -> HitlDecisionRequest:
        if self.action is HitlAction.SELECT_OPTION:
            if not self.option_id:
                raise ValueError("option_id is required when action is 'select_option'")
            if self.instruction is not None:
                raise ValueError("instruction is not allowed with 'select_option'")
        elif self.action is HitlAction.CUSTOM:
            if not self.instruction:
                raise ValueError("instruction is required when action is 'custom'")
            if self.option_id is not None:
                raise ValueError("option_id is not allowed with 'custom'")
        else:  # APPROVE, REJECT — neither field applies
            if self.option_id is not None or self.instruction is not None:
                raise ValueError(
                    f"'{self.action.value}' takes neither option_id nor instruction"
                )
        return self


class HitlDecisionAccepted(_Model):
    accepted: bool
    stage: WorkflowStage
    status: RunStatus
    resumed: bool = Field(description="True when the workflow resumed from the gate.")


class HitlHistoryResponse(_Model):
    history: list[HitlRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference images
# ---------------------------------------------------------------------------


class ReferenceImageResponse(_Model):
    """Metadata for a stored reference image.

    ``image_id`` is server-generated; the client-supplied filename is never reused as
    a path component or echoed back (docs/security.md 4).
    """

    image_id: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    uploaded_at: datetime


class ReferenceImageList(_Model):
    images: list[ReferenceImageResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Build & preview
# ---------------------------------------------------------------------------


class PreviewResponse(_Model):
    """Where the built preview is served, and whether it reflects current files.

    Stricter than the stored ``PreviewStatus``: this is returned only once a
    successful build exists, so the URL and timestamp are required. Without one the
    API returns 409 ``preview_not_available`` instead (docs/api.md 2.5).

    ``preview_url`` CARRIES A CAPABILITY AND IS SHORT-LIVED
        It is ``/preview/{token}/`` -- a signed token, not a project id. The
        static route sends no ``Authorization`` header because a browser tab or
        an iframe cannot, so the right to read this project's built output
        travels in the URL instead (``backend/auth/preview_token.py``).

        Two consequences for anyone consuming this model. The URL expires, at
        ``expires_at``, and a client that wants to keep showing a preview
        re-requests this endpoint rather than caching the string. And the URL
        is a credential: it should not be logged, pasted into an issue, or
        handed to anyone who is not entitled to the project.
    """

    preview_url: str
    built_at: datetime
    status: PreviewFreshness
    expires_at: datetime


class GeneratedFileManifest(_Model):
    files: list[FileRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class TargetedFixRequest(_Model):
    """Request a targeted correction.

    An empty ``finding_ids`` means "all blocking and major findings". Findings marked
    ``conflicts_with_approved_decision`` are never routed to the Fixer regardless of
    what is listed here (docs/design.md 5.5).
    """

    finding_ids: list[str] = Field(default_factory=list)
    instruction: str | None = Field(default=None, min_length=1, max_length=4000)


class FixAcceptedResponse(_Model):
    accepted: bool
    iteration_count: int = Field(ge=0)
    max_iterations: int = Field(ge=1)


# ---------------------------------------------------------------------------
# External actions (two-phase: plan, then execute with a token)
# ---------------------------------------------------------------------------


class ExternalActionRequest(_Model):
    """Ask for a plan. This never mutates anything (docs/api.md 2.7)."""

    action_type: ExternalActionType
    params: dict[str, Any] = Field(
        default_factory=dict, description="Validated per action_type by the service."
    )


class ExternalActionPlan(_Model):
    """Exactly what would change, plus a single-use approval token.

    Producing a plan performs no mutation. Execution without a valid token is
    rejected with 403; one approval never generalises to a later action
    (docs/mcp.md 5).
    """

    plan_id: str
    action_type: ExternalActionType
    summary: str = Field(description="What will change, in user terms.")
    changes: list[str] = Field(default_factory=list)
    approval_token: str
    expires_at: datetime


class ExternalActionExecuteRequest(_Model):
    plan_id: str
    approval_token: str


class ExternalActionResult(_Model):
    action_id: str
    action_type: ExternalActionType
    status: Literal["succeeded", "failed"]
    detail: str
    performed_at: datetime


# ---------------------------------------------------------------------------
# GitHub integration (docs/mcp.md 2.1)
# ---------------------------------------------------------------------------


class GitHubStatusResponse(_Model):
    """Whether GitHub is usable from this server.

    CONTAINS NO CREDENTIAL, and nothing derived from one — not a prefix, not a
    length, not a masked form. ``configured`` is the answer to "can this work",
    which is all a browser needs (``docs/security.md`` 2).

    ``configured`` is verified by a real call, not by the presence of an
    environment variable: a revoked or wrong-scoped token is "set" and still
    cannot work, and reporting that as connected is the false claim this
    integration must not make.
    """

    configured: bool
    mode: Literal["rest", "mcp", "none"]
    reason: str = ""
    mcp_server_configured: bool = False
    mcp_tools_discovered: int | None = None
    account: str | None = None


class RepositorySummary(_Model):
    full_name: str
    default_branch: str
    private: bool
    can_push: bool
    html_url: str


class RepositoryList(_Model):
    repositories: list[RepositorySummary] = Field(default_factory=list)


class GitHubLinkResponse(_Model):
    """The repository this project is connected to, or nothing."""

    connected: bool
    repository: str | None = None
    default_branch: str | None = None
    branch: str | None = None
    last_pushed_at: datetime | None = None
    last_commit_sha: str | None = None


class SelectRepositoryRequest(_Model):
    repository: str = Field(min_length=3, description="owner/name")
    branch: str | None = Field(
        default=None, description="Branch DETROIT pushes to. Defaults to detroit/generated."
    )


class SyncChange(_Model):
    """One file's fate in a sync preview."""

    path: str
    # Push emits exactly these. "unchanged" and "conflict" belonged to the
    # removed pull path.
    kind: Literal["add", "update", "skip"]
    reason: str = ""
    size_bytes: int = 0


class SyncPlanResponse(_Model):
    """What a push or pull WOULD do, plus the approval needed to do it.

    Producing this mutates nothing. The token is single-use and short-lived,
    and execution without it is refused — the same two-phase gate every
    external write uses (``docs/mcp.md`` 5).
    """

    direction: Literal["push"]
    repository: str
    branch: str
    base_branch: str
    added: int
    updated: int
    skipped: int
    changes: list[SyncChange] = Field(default_factory=list)
    plan_id: str
    approval_token: str
    expires_at: datetime


class ExecuteSyncRequest(_Model):
    plan_id: str
    approval_token: str
    commit_message: str | None = None


class SyncResultResponse(_Model):
    direction: Literal["push"]
    repository: str
    branch: str
    files_written: int
    commit_sha: str | None = None
    detail: str = ""


class ExternalActionHistory(_Model):
    actions: list[McpActionRecord] = Field(default_factory=list)
