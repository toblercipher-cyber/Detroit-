"""The Visual & UX Critic: judges the page that was actually rendered.

WHY THIS FILE EXISTS
    A build passing proves the code compiles. A type check proves the types line up.
    Neither says anything about whether a person can use the page — whether anything
    leads the eye, whether the spacing holds together, whether the next action is
    obvious. ``skills.md`` 18 is blunt about this: source alone is not evidence.

    This role is the first reader of the built thing. It reports defects; it does not
    fix them and it does not redesign. Separating "what is wrong" from "what to
    change" is what keeps a review from quietly becoming a rewrite.

    Visual Critic and UX Reviewer are one role here, not two. They inspected the same
    rendered page against overlapping criteria, and two agents competing over one
    finding set produces duplicates, not better coverage (docs/agents.md 2).

LAYER
    Agents (``backend/agents/``). Like the Reference Analyzer it overrides the prompt
    to carry evidence — a screenshot as an image block, the DOM as text. Execution,
    validation and failure classification still come from ``base.py``.

WHEN THERE IS NO EVIDENCE
    This role must never review a page it was not shown. The template declares an
    evidence variable that ``prompt_variables`` cannot supply unless a real
    ``RenderedPreviewPayload`` is present, so a missing or malformed preview fails as
    a contract violation *before any model call* rather than producing an invented
    critique.

WHAT IT CANNOT SEE IS AS IMPORTANT AS WHAT IT CAN
    A screenshot shows appearance but not structure; a DOM shows structure but not
    appearance. Only the evidence actually supplied reaches the prompt, and the task
    instructions say which kind arrived, so the model is not invited to infer the
    half it is missing.

CONTRACT SOURCE
    Transcribed from ``docs/agents.md`` 4.7.

Spec: docs/agents.md 3, 4.7; docs/state.md 6; docs/api.md 4; skills.md 18; CLAUDE.md 15.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from backend.agents.base import (
    _GUARD_BLOCK,
    _STATE_BLOCK,
    _SYSTEM_TEMPLATE,
    _TASK_BLOCK,
    AgentDefinition,
    BaseAgent,
)
from backend.context.builder import (
    AgentContext,
    ContextInput,
    RenderedPreviewPayload,
)
from backend.schemas.enums import AgentRole
from backend.schemas.state import ReviewFindings

_SCREENSHOT_LABEL = {"type": "text", "text": "RENDERED PAGE — screenshot"}
_SCREENSHOT_BLOCK = {
    "type": "image",
    "source_type": "base64",
    "mime_type": "{preview_media_type}",
    "data": "{preview_screenshot}",
}
_DOM_BLOCK = {"type": "text", "text": "RENDERED PAGE — DOM\n{preview_dom}"}

# Declared only when no usable payload arrived. Nothing ever supplies it, so the
# shared missing-variable check in ``base.run`` stops the run before it costs a call.
_NO_EVIDENCE_BLOCK = {"type": "text", "text": "RENDERED PAGE\n{rendered_evidence}"}

# Constructed at import time so an incomplete contract fails on load, not on first run.
VISUAL_UX_CRITIC_DEFINITION = AgentDefinition(
    role=AgentRole.VISUAL_UX_CRITIC,
    purpose="Inspect the rendered page for visual and usability defects.",
    responsibilities=(
        "Assess visual hierarchy: what leads, what follows, what recedes.",
        "Assess layout, spacing consistency and alignment.",
        "Assess responsive behaviour where the evidence shows it.",
        "Assess information architecture and navigation clarity.",
        "Assess interaction flow and cognitive load.",
        "Check consistency with the approved design spec.",
        "Report each defect as a finding with a location and a suggested fix.",
    ),
    prohibited_actions=(
        "Do not write or modify code.",
        "Do not redesign — report the defect, not a replacement design.",
        "Do not judge from source alone; review only what the rendered evidence "
        "actually shows.",
        "Do not report a deliberate design decision as a plain defect. If it "
        "contradicts the approved design spec, mark it as a conflict instead.",
        "Do not invent findings to fill a quota. No defects is a valid result.",
    ),
    output_model=ReviewFindings,
    # Read-only inspection of the rendered preview supplied in context.
    allowed_tools=frozenset({"rendered_preview_inspection"}),
)


class VisualUxCriticAgent(BaseAgent[ReviewFindings]):
    """Reviews the rendered page and returns findings."""

    @property
    def definition(self) -> AgentDefinition:
        return VISUAL_UX_CRITIC_DEFINITION

    def _payload(self, context: AgentContext) -> RenderedPreviewPayload | None:
        """The rendered evidence, if a usable payload was supplied."""
        candidate = context.inputs.get(ContextInput.RENDERED_PREVIEW.value)
        return candidate if isinstance(candidate, RenderedPreviewPayload) else None

    def prompt_template(self, context: AgentContext) -> ChatPromptTemplate:
        """Build a template carrying whatever evidence actually arrived."""
        blocks: list[dict] = [{"type": "text", "text": _TASK_BLOCK}]
        if context.state:
            blocks.append({"type": "text", "text": _STATE_BLOCK})

        payload = self._payload(context)
        if payload is None:
            blocks.append(_NO_EVIDENCE_BLOCK)
        else:
            if payload.screenshot_base64 is not None:
                blocks.append(_SCREENSHOT_LABEL)
                blocks.append(_SCREENSHOT_BLOCK)
            if payload.dom_html is not None:
                blocks.append(_DOM_BLOCK)

        blocks.append({"type": "text", "text": _GUARD_BLOCK})
        return ChatPromptTemplate.from_messages(
            [("system", _SYSTEM_TEMPLATE), ("human", blocks)]
        )

    def prompt_variables(self, context: AgentContext) -> dict[str, str]:
        """Supply evidence variables only for evidence that exists."""
        variables = super().prompt_variables(context)
        payload = self._payload(context)
        if payload is not None:
            if payload.screenshot_base64 is not None:
                variables["preview_media_type"] = payload.screenshot_media_type or ""
                variables["preview_screenshot"] = payload.screenshot_base64
            if payload.dom_html is not None:
                variables["preview_dom"] = payload.dom_html
        return variables

    def task_instructions(self, context: AgentContext) -> str:
        """Role-specific framing, naming which evidence arrived."""
        payload = self._payload(context)
        if payload is None:
            evidence = "No rendered evidence was supplied."
        else:
            kinds = []
            if payload.screenshot_base64 is not None:
                kinds.append("a screenshot, which shows appearance but not markup")
            if payload.dom_html is not None:
                kinds.append("the DOM, which shows structure but not appearance")
            evidence = (
                "You were given " + " and ".join(kinds) + ". Judge only what that "
                "evidence actually shows, and do not infer the part you were not "
                "given."
            )

        return (
            f"{context.task}\n\n"
            "WHAT YOU ARE LOOKING AT\n"
            f"- {evidence}\n"
            "- Review the rendered result, not an idea of what the code probably "
            "does.\n"
            "\n"
            "WHAT COUNTS AS A DEFECT\n"
            "- Something a person using this page would be worse off for: they "
            "cannot tell what matters, cannot find the next action, or have to work "
            "to read it.\n"
            "- Hierarchy that does not lead the eye; spacing or alignment that is "
            "inconsistent without a reason; navigation that hides where things are; "
            "a flow that asks for more attention than the task deserves.\n"
            "- Divergence from the approved design spec in your context.\n"
            "\n"
            "WHAT IS NOT A DEFECT\n"
            "- A choice you would have made differently. Taste is not a finding.\n"
            "- Anything the approved design spec establishes on purpose. If the "
            "rendered page follows the spec and you disagree with the spec, that is "
            "a conflict, not a defect: set `conflicts_with_approved_decision` to "
            "true so it reaches the user instead of being silently changed.\n"
            "- A clean page. Returning no findings is a real and useful answer; an "
            "invented defect costs someone a fix they did not need.\n"
            "\n"
            "WRITING A FINDING\n"
            "- `source` is `visual_ux_critic` and `category` is `ux` on every "
            "finding you return.\n"
            "- `summary` states the problem, not the remedy: what is wrong and why "
            "it matters to someone using the page.\n"
            "- `location` names where — the section, component or region.\n"
            "- `suggested_fix` is a direction, not a redesign. 'Increase the "
            "contrast between the heading and body sizes so the heading leads' is a "
            "direction; a replacement layout is not yours to specify.\n"
            "- `severity`: `blocker` if the page cannot be used for its purpose, "
            "`major` if it clearly impedes, `minor` for polish, `info` for an "
            "observation worth recording."
        )
