"""The Anti-Slop Reviewer: interprets the detector's output against project intent.

WHY THIS FILE EXISTS
    Impeccable can tell you a gradient is there. It cannot tell you whether that
    gradient was a decision. This role reads the detector's findings next to the
    approved design system and the rendered page, and separates three things a raw
    detector run leaves mixed together:

      - a genuine lapse into generic, generated-looking design
      - a deliberate choice the user already approved
      - a rule firing on something that does not actually matter here

    Without that separation the "fix" step would dutifully strip out the parts
    someone chose on purpose.

WHAT THIS ROLE IS NOT
    It is not the gate, and it is not a replacement for the detector. Impeccable is
    mandatory and blocking; if it did not run, the evaluation layer reports the gate
    blocked and this agent is never reached (``docs/design.md`` 5.1-5.3). An agent-only
    pass is explicitly not a substitute.

    It also does not report whether the detector ran. That is a fact the runtime
    observes and records on ``EvaluationReport``; a model able to assert it could
    forge the very gate it is being checked by. This agent returns findings only
    (``docs/agents.md`` 3, 4.8).

ALLOWED TOOLS — a deliberate narrowing
    ``docs/agents.md`` 4.8 still lists "Impeccable" among this role's allowed tools.
    It is declared here with rendered-preview inspection only, because the evaluation
    layer runs the detector and passes its findings in as context — the agent never
    invokes it. Declaring a tool the role does not call would overstate its
    privileges, and least privilege is the point of the declaration
    (``docs/security.md`` 7). That doc line is stale and is flagged for correction.

LAYER
    Agents (``backend/agents/``). Overrides the prompt to carry rendered evidence and
    the detector's findings; execution, validation and failure classification come
    from ``base.py``.

Spec: docs/agents.md 3, 4.8; docs/design.md 5.1-5.5; docs/state.md 6; CLAUDE.md 15;
      skills.md 17.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from backend.agents.base import (
    _CONSTRAINTS_BLOCK,
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
_NO_EVIDENCE_BLOCK = {"type": "text", "text": "RENDERED PAGE\n{rendered_evidence}"}

# Always declared. If the detector's findings are absent, nothing supplies this and
# the shared missing-variable check stops the run — which is correct: no findings
# input means no detector run, and a detector that did not run must not be
# interpreted as though it had (docs/design.md 5.3).
_DETECTOR_BLOCK = {
    "type": "text",
    "text": "DETECTOR FINDINGS (from Impeccable)\n{detector_findings}",
}

# Transcribed from docs/design.md 5.4. These are what a finding is interpreted
# against; the detector's own rule set is whatever the installed version implements.
_CHECKLIST = (
    "- unnecessary gradients\n"
    "- excessive glassmorphism\n"
    "- repetitive rounded cards\n"
    "- arbitrary icon tiles / icon grids\n"
    "- weak typography hierarchy\n"
    "- excessive decorative blobs\n"
    "- excessive glow / shadow effects\n"
    "- generic SaaS template composition\n"
    "- inconsistent spacing\n"
    "- poor responsive behavior"
)

# Constructed at import time so an incomplete contract fails on load, not on first run.
ANTI_SLOP_REVIEWER_DEFINITION = AgentDefinition(
    role=AgentRole.ANTI_SLOP_REVIEWER,
    purpose=(
        "Interpret the detector's anti-slop findings and produce concrete corrective "
        "instructions."
    ),
    responsibilities=(
        "Read the detector's findings against the rendered page.",
        "Interpret each one against the anti-slop checklist and the approved design "
        "system.",
        "Separate genuine slop from decisions the user approved on purpose.",
        "Give specific, actionable corrections rather than 'make it better'.",
        "Report only what was observed.",
    ),
    prohibited_actions=(
        "Do not stand in for the detector. If it did not run, there is nothing here "
        "to interpret and the gate is blocked.",
        "Do not report a blocked or skipped check as completed or passed.",
        "Do not treat a detector finding as proof that something is wrong.",
        "Do not treat a clean run as proof that the design is good.",
        "Do not propose 'fixing' an intentional approved decision — mark it as a "
        "conflict so it reaches the user, not the Fixer.",
        "Do not pursue novelty for its own sake; different is not better.",
    ),
    output_model=ReviewFindings,
    # The detector is run by the evaluation layer, not called from here — see the
    # module docstring.
    allowed_tools=frozenset({"rendered_preview_inspection"}),
)


class AntiSlopReviewerAgent(BaseAgent[ReviewFindings]):
    """Interprets detector findings and returns corrective findings."""

    @property
    def definition(self) -> AgentDefinition:
        return ANTI_SLOP_REVIEWER_DEFINITION

    def _payload(self, context: AgentContext) -> RenderedPreviewPayload | None:
        candidate = context.inputs.get(ContextInput.RENDERED_PREVIEW.value)
        return candidate if isinstance(candidate, RenderedPreviewPayload) else None

    def _detector_findings(self, context: AgentContext):
        """The detector's output. ``None`` means it did not run; ``[]`` means it ran
        and found nothing, which is a real and different answer."""
        return context.inputs.get(ContextInput.FINDINGS.value)

    def prompt_template(self, context: AgentContext) -> ChatPromptTemplate:
        blocks: list[dict] = [{"type": "text", "text": _TASK_BLOCK}]
        # Approved decisions are what separate a deliberate choice from slop. An
        # override that drops them would leave this role unable to tell the
        # difference, which is the one judgement it exists to make.
        if context.constraints:
            blocks.append({"type": "text", "text": _CONSTRAINTS_BLOCK})
        if context.state:
            blocks.append({"type": "text", "text": _STATE_BLOCK})

        # Always present, so a missing detector run cannot be silently interpreted.
        blocks.append(_DETECTOR_BLOCK)

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
        variables = super().prompt_variables(context)

        findings = self._detector_findings(context)
        if findings is not None:
            variables["detector_findings"] = (
                "\n".join(
                    f.model_dump_json() if isinstance(f, BaseModel) else str(f)
                    for f in findings
                )
                or "The detector ran and reported nothing."
            )

        payload = self._payload(context)
        if payload is not None:
            if payload.screenshot_base64 is not None:
                variables["preview_media_type"] = payload.screenshot_media_type or ""
                variables["preview_screenshot"] = payload.screenshot_base64
            if payload.dom_html is not None:
                variables["preview_dom"] = payload.dom_html
        return variables

    def task_instructions(self, context: AgentContext) -> str:
        findings = self._detector_findings(context)
        if findings is not None and len(findings) == 0:
            detector_note = (
                "The detector ran and reported nothing. That is not the same as the "
                "design being good — it means no rule matched. Look at the rendered "
                "page yourself and report anything the checklist covers that the "
                "detector missed. Reporting nothing is also a valid answer."
            )
        else:
            detector_note = (
                "Work through the detector's findings one at a time. Each is evidence "
                "that a rule matched — not proof that something is wrong."
            )

        return (
            f"{context.task}\n\n"
            "YOUR JOB IS INTERPRETATION\n"
            f"- {detector_note}\n"
            "- You did not run the detector and you are not the gate. Do not comment "
            "on whether it ran, which version it was, or whether the check passed.\n"
            "\n"
            "WHAT THE CHECKLIST COVERS\n"
            f"{_CHECKLIST}\n"
            "\n"
            "THREE OUTCOMES FOR EACH DETECTOR FINDING\n"
            "1. Genuine slop — the page really did fall into a generic pattern. "
            "Report it with a concrete correction.\n"
            "2. An approved decision — the approved design system or the constraints "
            "above establish it deliberately. Set "
            "`conflicts_with_approved_decision` to true so it reaches the user. Do "
            "not propose changing it; the Fixer must never undo something the user "
            "chose.\n"
            "3. Not material here — the rule matched but nothing about this page is "
            "worse for it. Leave it out rather than padding the list.\n"
            "\n"
            "WRITING A CORRECTION\n"
            "- `source` is `anti_slop_reviewer` and `category` is `anti_slop` on "
            "every finding.\n"
            "- Say what to change and where. 'Make the hero less generic' is not "
            "actionable; 'the three feature cards are identical in weight, so "
            "nothing leads — give the primary one more prominence' is.\n"
            "- `location` names the section or component.\n"
            "- `severity`: `blocker` only if the page reads as template output "
            "throughout, `major` for a pattern that dominates a section, `minor` for "
            "a local lapse, `info` for an observation.\n"
            "\n"
            "THE POINT IS NOT NOVELTY\n"
            "- Coherent and purposeful beats unusual. A design that is strange in "
            "order to avoid looking generated is a worse outcome, not a better one.\n"
            "- Do not suggest a change whose only justification is that it differs "
            "from what is common."
        )
