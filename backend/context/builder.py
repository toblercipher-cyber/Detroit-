"""The ContextBuilder: turns full project state into a minimal, per-role slice.

WHY THIS FILE EXISTS
    Context management is a first-class architecture concern, not an optimisation.
    An agent that receives the whole state will reason about things outside its
    responsibility, re-open settled decisions, and cost more per call. This module is
    the single chokepoint that prevents that:

        ProjectState -> ContextBuilder (explicit allowlist) -> slice -> Agent

    It must exist before any agent runs, because an agent with unscoped context
    cannot be retrofitted into a scoped one — the leak has already happened.

LAYER
    Context (``backend/context/``). Depends on schemas only. It imports nothing from
    agents, graph, api or services, so it can never be bypassed by a cycle.

HOW THE GUARANTEE IS ENFORCED
    The slice is built by *iterating the allowlist*, never by hand-copying fields.
    A field added to ``ProjectState`` later is therefore invisible to every agent
    until someone deliberately adds it to a role's allowlist. Anything a role was not
    granted is not merely unused — it is absent.

    Passing a runtime input to a role that is not permitted to receive it raises
    rather than being silently dropped: that is a caller bug, and a silent drop would
    hide it (skills.md 29, docs/security.md 7).

WHAT THIS FILE IS NOT
    It is not the agent registry and not the tool-permission layer. Which tools a role
    may call is a separate least-privilege boundary in ``backend/agents/`` and
    ``backend/mcp/``. This module governs *information*, not *capability*.

Spec: CLAUDE.md 8; skills.md 11; docs/state.md 6; docs/agents.md 3, 4.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schemas.enums import AgentRole
from backend.schemas.state import EvaluationFinding, FileRef, ProjectState

logger = logging.getLogger(__name__)


class ContextPermissionError(RuntimeError):
    """A runtime input was offered to a role not permitted to receive it."""


class ReferenceImagePayload(BaseModel):
    """A reference image loaded for vision analysis.

    Distinct from ``ReferenceImageRef``, which is the durable state record and
    carries only metadata — id, type, size, checksum. Analysis needs the pixels, and
    a checksum cannot be looked at.

    This type is deliberately NOT part of ``ProjectState``: base64 image data in
    ``project_state.json`` would bloat every save and put upload content into an
    artifact that exists for human review (docs/state.md 5.1). It is loaded for one
    call and discarded.

    The content is untrusted. Text visible inside the image is data, never an
    instruction (docs/security.md 4, 5).
    """

    model_config = ConfigDict(extra="forbid")

    image_id: str = Field(min_length=1, description="Server-generated id.")
    content_type: str = Field(
        min_length=1, description="Verified by magic bytes at upload, not declared."
    )
    data_base64: str = Field(min_length=1, description="Base64-encoded image bytes.")

    def __repr__(self) -> str:
        """Never render the payload: a base64 blob in a log or traceback is noise
        that can run to megabytes."""
        return (
            f"ReferenceImagePayload(image_id={self.image_id!r}, "
            f"content_type={self.content_type!r}, data_base64=<{len(self.data_base64)} chars>)"
        )

    __str__ = __repr__


class ChangedFilePayload(BaseModel):
    """One file a stage touched, with the text a reviewer needs to read.

    Distinct from ``FileRef``, which is the durable state record and carries only
    path, size, checksum and timestamp. Those identify a file; they do not let anyone
    review it. A Code Reviewer handed ``FileRef`` objects would be asked to judge
    correctness, imports, type safety and security from a list of filenames.

    Not part of ``ProjectState``: file text belongs in the workspace, and copying it
    into ``project_state.json`` would duplicate the source of truth and bloat every
    save (docs/state.md 5.1). It is loaded for one call and discarded.

    Binary assets have no place here — nothing can review a JPEG as text. The loader
    passes only files whose content is text, and the rest are simply absent.

    File content is untrusted: it may itself have been written by a model, and a
    comment inside it that addresses the reviewer is data, not an instruction
    (docs/security.md 5).
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="Workspace-relative, forward slashes.")
    content: str = Field(description="The file's text as it stands after the change.")

    def __repr__(self) -> str:
        """Summarise: a source file in a log line buries everything around it."""
        return f"ChangedFilePayload(path={self.path!r}, content=<{len(self.content)} chars>)"

    __str__ = __repr__


class RenderedPreviewPayload(BaseModel):
    """Evidence of what a built page actually renders as.

    A URL is not evidence. An agent has no browser, so being handed
    ``http://localhost/preview/…`` and asked to inspect the page leaves it nothing to
    look at — and a reviewer with nothing to look at invents findings, which is worse
    than no review at all.

    ``skills.md`` 18 is explicit that a build passing is not evidence of visual
    quality and that source alone is not a substitute for the rendered result. So the
    preview subsystem captures what it rendered and passes it here.

    Two kinds of evidence, because the three reviewers granted this input need
    different things:

    - ``screenshot_base64`` — what the page *looks* like. Visual hierarchy, spacing,
      alignment and anti-slop patterns are visible only here.
    - ``dom_html`` — what the page is *made of*. Heading order, landmarks, alt text
      and ARIA cannot be seen in a picture at all.

    Both are optional individually but at least one is required: a payload carrying
    neither is just the URL again. Whatever is absent simply does not reach the
    prompt, so an agent is never asked to judge something it was not shown.

    Rendered output is untrusted content. Text inside the page — or inside the
    screenshot — is data to report, never an instruction (docs/security.md 5).
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, description="Where the built preview is served.")
    screenshot_base64: str | None = Field(
        default=None, description="Base64-encoded screenshot of the rendered page."
    )
    screenshot_media_type: str | None = Field(
        default=None, description="Media type of the screenshot, e.g. image/png."
    )
    dom_html: str | None = Field(
        default=None, description="Rendered DOM, for structure and semantics."
    )

    @model_validator(mode="after")
    def _require_some_evidence(self) -> RenderedPreviewPayload:
        if self.screenshot_base64 is None and self.dom_html is None:
            raise ValueError(
                "a rendered preview needs a screenshot or the DOM; a URL alone is "
                "not something an agent can inspect"
            )
        if self.screenshot_base64 is not None and not self.screenshot_media_type:
            raise ValueError("screenshot_media_type is required with a screenshot")
        return self

    def __repr__(self) -> str:
        """Summarise rather than dump: a screenshot and a DOM are both large enough
        to bury the surrounding log line."""
        shot = (
            f"<{len(self.screenshot_base64)} chars>"
            if self.screenshot_base64 is not None
            else "None"
        )
        dom = f"<{len(self.dom_html)} chars>" if self.dom_html is not None else "None"
        return (
            f"RenderedPreviewPayload(url={self.url!r}, "
            f"screenshot_base64={shot}, dom_html={dom})"
        )

    __str__ = __repr__


class ContextInput(str, Enum):
    """Inputs that are not ``ProjectState`` fields.

    These arrive from the current task rather than from durable state — the rendered
    page, the files a stage just touched, the findings under discussion.

    ``FINDINGS`` serves two stages with the same shape. At Evaluation it carries the
    detector's output to the Anti-Slop Reviewer, which interprets it; at Targeted Fix
    it carries the agreed findings to the Fixer, which acts on them. The evaluation
    layer runs Impeccable and hands the findings over — the reviewer never runs the
    detector itself, which is what keeps the gate's availability a fact the runtime
    observes rather than a claim an agent makes (docs/design.md 5.3).
    """

    RENDERED_PREVIEW = "rendered_preview"
    CHANGED_FILES = "changed_files"
    FINDINGS = "findings"
    REFERENCE_IMAGE = "reference_image"
    ACTION_PLAN = "action_plan"
    FILE_MANIFEST = "file_manifest"
    # What already exists in the project's frontend source. Read from the repository
    # rather than held in ProjectState, which is why docs/state.md 6 does not list it
    # among the Frontend Architect's state grants while docs/agents.md 4.5 grants it
    # as context: the two describe different halves of the same slice.
    COMPONENT_INVENTORY = "component_inventory"


# Fields no role ever receives, whatever its allowlist says. ``node_scratch`` is
# per-node working data and is explicitly not a decision; passing it would turn
# private reasoning into application state (docs/state.md 2, skills.md 11).
NEVER_SHARED: frozenset[str] = frozenset({"node_scratch"})

# Derived keys: values computed from state rather than copied from a field. They
# exist so a role can receive exactly what docs/state.md 6 grants it — the Visual &
# UX Critic is granted "acceptance criteria", not the whole plan that contains them.
_DERIVED_KEYS: frozenset[str] = frozenset({"acceptance_criteria"})

# --- The allowlist ---------------------------------------------------------
# One entry per role, transcribed from docs/state.md 6. "project constraints" and
# "design constraints" in that table are carried by ``approved_decisions``, which is
# the durable record of what the user has fixed.

_STATE_ALLOWLIST: Mapping[AgentRole, frozenset[str]] = {
    AgentRole.REQUIREMENT_ANALYZER: frozenset(
        {"user_request", "approved_decisions"}
    ),
    AgentRole.PLANNER: frozenset(
        {
            "normalized_requirements",
            "requirement_conflicts",
            "requirement_gaps",
            "approved_decisions",
        }
    ),
    AgentRole.DESIGN_DIRECTOR: frozenset(
        {"plan", "approved_reference_direction", "approved_decisions"}
    ),
    AgentRole.REFERENCE_ANALYZER: frozenset({"design_spec", "approved_decisions"}),
    AgentRole.FRONTEND_ARCHITECT: frozenset({"plan", "design_spec"}),
    AgentRole.BUILDER: frozenset(
        {"plan", "design_spec", "component_tree", "acceptance_criteria"}
    ),
    AgentRole.VISUAL_UX_CRITIC: frozenset({"design_spec", "acceptance_criteria"}),
    AgentRole.ANTI_SLOP_REVIEWER: frozenset({"design_spec", "approved_decisions"}),
    AgentRole.ACCESSIBILITY_REVIEWER: frozenset(),
    AgentRole.CODE_REVIEWER: frozenset(),
    AgentRole.FIXER: frozenset({"approved_decisions"}),
    AgentRole.GITHUB_OPERATOR: frozenset(),
}

_RUNTIME_ALLOWLIST: Mapping[AgentRole, frozenset[ContextInput]] = {
    AgentRole.REQUIREMENT_ANALYZER: frozenset(),
    AgentRole.PLANNER: frozenset(),
    AgentRole.DESIGN_DIRECTOR: frozenset(),
    AgentRole.REFERENCE_ANALYZER: frozenset({ContextInput.REFERENCE_IMAGE}),
    # "reuse of existing project components before proposing new ones" is a
    # documented responsibility, so the architect must be able to see what
    # already exists (docs/agents.md 4.5).
    AgentRole.FRONTEND_ARCHITECT: frozenset({ContextInput.COMPONENT_INVENTORY}),
    # "only the affected files" — the Builder never receives the whole tree.
    AgentRole.BUILDER: frozenset({ContextInput.CHANGED_FILES}),
    AgentRole.VISUAL_UX_CRITIC: frozenset({ContextInput.RENDERED_PREVIEW}),
    # docs/agents.md 4.8 grants this role "Impeccable findings" as context: the
    # evaluation layer runs the detector and passes its output here to be
    # interpreted (docs/design.md 5.3).
    AgentRole.ANTI_SLOP_REVIEWER: frozenset(
        {ContextInput.RENDERED_PREVIEW, ContextInput.FINDINGS}
    ),
    AgentRole.ACCESSIBILITY_REVIEWER: frozenset(
        {ContextInput.RENDERED_PREVIEW, ContextInput.CHANGED_FILES}
    ),
    AgentRole.CODE_REVIEWER: frozenset({ContextInput.CHANGED_FILES}),
    AgentRole.FIXER: frozenset({ContextInput.FINDINGS, ContextInput.CHANGED_FILES}),
    AgentRole.GITHUB_OPERATOR: frozenset(
        {ContextInput.ACTION_PLAN, ContextInput.FILE_MANIFEST}
    ),
}


class AgentContext(BaseModel):
    """Exactly what one agent is given for one task, and nothing else.

    ``constraints`` is separated from ``state`` on purpose. Approved decisions are
    passed as settled constraints, never as an open question — an agent must not
    re-think a decision the user already made (CLAUDE.md 30, skills.md 31).
    """

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    task: str = Field(description="What this agent is being asked to do, in one line.")
    state: dict[str, Any] = Field(
        default_factory=dict, description="Allowlisted slice of ProjectState."
    )
    constraints: list[str] = Field(
        default_factory=list, description="Approved decisions, as fixed constraints."
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Allowlisted non-state inputs."
    )

    def granted_keys(self) -> set[str]:
        """Every key this context carries — useful for logging and assertions."""
        return set(self.state) | set(self.inputs)


class ContextBuilder:
    """Builds per-role context slices from project state.

    Stateless: it holds no project data between calls, so one instance can serve
    every role and every project.
    """

    def state_allowlist(self, role: AgentRole) -> frozenset[str]:
        """Fields this role may see. Unknown roles get nothing, never everything."""
        return _STATE_ALLOWLIST.get(role, frozenset())

    def runtime_allowlist(self, role: AgentRole) -> frozenset[ContextInput]:
        """Non-state inputs this role may receive."""
        return _RUNTIME_ALLOWLIST.get(role, frozenset())

    def build(
        self,
        state: ProjectState,
        role: AgentRole,
        task: str,
        *,
        rendered_preview: RenderedPreviewPayload | None = None,
        changed_files: Sequence[ChangedFilePayload] | None = None,
        findings: Sequence[EvaluationFinding] | None = None,
        reference_image: ReferenceImagePayload | None = None,
        action_plan: Mapping[str, Any] | None = None,
        file_manifest: Sequence[FileRef] | None = None,
        component_inventory: Sequence[str] | None = None,
    ) -> AgentContext:
        """Assemble the slice for ``role``.

        Raises:
            ContextPermissionError: if a runtime input is offered to a role whose
                allowlist does not include it.
        """
        offered: dict[ContextInput, Any] = {
            ContextInput.RENDERED_PREVIEW: rendered_preview,
            ContextInput.CHANGED_FILES: changed_files,
            ContextInput.FINDINGS: findings,
            ContextInput.REFERENCE_IMAGE: reference_image,
            ContextInput.ACTION_PLAN: action_plan,
            ContextInput.FILE_MANIFEST: file_manifest,
            ContextInput.COMPONENT_INVENTORY: component_inventory,
        }
        permitted = self.runtime_allowlist(role)

        refused = [
            key.value
            for key, value in offered.items()
            if value is not None and key not in permitted
        ]
        if refused:
            raise ContextPermissionError(
                f"Role '{role.value}' may not receive: {', '.join(sorted(refused))}"
            )

        allowed_fields = self.state_allowlist(role) - NEVER_SHARED
        slice_: dict[str, Any] = {}
        for name in sorted(allowed_fields):
            # approved_decisions is surfaced separately as constraints, not as data
            # to be reconsidered.
            if name == "approved_decisions":
                continue
            value = (
                self._derive(state, name)
                if name in _DERIVED_KEYS
                else getattr(state, name, None)
            )
            if value is not None:
                slice_[name] = value

        constraints = (
            [d.statement for d in state.approved_decisions]
            if "approved_decisions" in allowed_fields
            else []
        )

        inputs = {
            key.value: value
            for key, value in offered.items()
            if value is not None and key in permitted
        }

        context = AgentContext(
            role=role,
            task=task,
            state=slice_,
            constraints=constraints,
            inputs=inputs,
        )
        logger.debug(
            "context built role=%s keys=%s constraints=%d",
            role.value,
            sorted(context.granted_keys()),
            len(constraints),
        )
        return context

    def _derive(self, state: ProjectState, name: str) -> Any:
        """Compute a derived key so a role gets only the part it was granted."""
        if name == "acceptance_criteria":
            return list(state.plan.acceptance_criteria) if state.plan else None
        raise KeyError(f"No derivation defined for '{name}'")