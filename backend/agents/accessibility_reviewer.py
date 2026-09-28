"""The Accessibility Reviewer: checks that the rendered page is actually usable.

WHY THIS FILE EXISTS
    Accessibility defects are the ones least likely to be noticed by the person who
    built the page and most likely to exclude someone from using it. They are also
    largely objective — a heading level was skipped or it was not, an image has
    alternative text or it does not — which is why this is a separate role from the
    Visual & UX Critic rather than a section of its opinion.

    Keeping them apart also keeps the criteria honest. "This looks cramped" and "this
    control cannot be reached with a keyboard" are different claims with different
    standards of evidence, and mixing them lets the second hide behind the first.

LAYER
    Agents (``backend/agents/``). It supplies :meth:`evidence_blocks` rather than
    rebuilding the prompt, so the task, constraints, state slice and untrusted-input
    guard are contributed by ``base.py`` and cannot be dropped here.

WHAT THE EVIDENCE CAN AND CANNOT SHOW
    This role's findings depend entirely on which evidence arrived, and the two kinds
    answer different questions:

      - the DOM answers almost everything structural — heading order, landmarks,
        alt text, ARIA, what is focusable
      - a screenshot answers only what is visible — apparent contrast, apparent
        target size

    Several documented concerns cannot be settled by either. Whether focus is
    *visible*, whether motion respects a reduced-motion preference, and whether a
    contrast ratio actually passes a threshold need computed styles or interaction,
    and neither is available. The prompt says so, because a reviewer that asserts a
    contrast ratio it could not compute produces a finding nobody can act on.

Spec: docs/agents.md 3, 4.9; docs/state.md 6; docs/api.md 4; skills.md 18.
"""

from __future__ import annotations

from backend.agents.base import AgentDefinition, BaseAgent
from backend.context.builder import (
    AgentContext,
    ContextInput,
    RenderedPreviewPayload,
)
from backend.schemas.enums import AgentRole
from backend.schemas.state import ReviewFindings

# The DOM comes first: for this role it is the primary evidence, not a supplement.
_DOM_BLOCK = {"type": "text", "text": "RENDERED PAGE — DOM\n{preview_dom}"}
_SCREENSHOT_LABEL = {"type": "text", "text": "RENDERED PAGE — screenshot"}
_SCREENSHOT_BLOCK = {
    "type": "image",
    "source_type": "base64",
    "mime_type": "{preview_media_type}",
    "data": "{preview_screenshot}",
}
_CHANGED_FILES_BLOCK = {
    "type": "text",
    "text": "FILES CHANGED IN THIS STEP (paths only)\n{changed_file_paths}",
}

# Declared when no usable preview arrived. Nothing supplies it, so the shared
# missing-variable check stops the run before it costs a call.
_NO_EVIDENCE_BLOCK = {"type": "text", "text": "RENDERED PAGE\n{rendered_evidence}"}

# Constructed at import time so an incomplete contract fails on load, not on first run.
ACCESSIBILITY_REVIEWER_DEFINITION = AgentDefinition(
    role=AgentRole.ACCESSIBILITY_REVIEWER,
    purpose="Verify basic accessibility and semantic quality of the rendered page.",
    responsibilities=(
        "Check semantic HTML: elements used for what they mean, not how they look.",
        "Check heading order and document structure.",
        "Check landmarks and page regions.",
        "Check what is focusable and in what order, as far as the DOM shows it.",
        "Check alternative text on images and non-text content.",
        "Check ARIA usage for correctness, including where it is unnecessary.",
        "Check apparent contrast and apparent touch target size from the rendering.",
        "Note where a concern cannot be settled from the evidence available.",
    ),
    prohibited_actions=(
        "Do not write or modify code.",
        "Do not restyle or propose a visual redesign.",
        "Do not give opinions on visual taste — that is the Visual & UX Critic's "
        "role, and mixing the two lets a preference pose as an access barrier.",
        "Do not assert a measurement you cannot take. You have no computed styles "
        "and cannot interact with the page.",
        "Do not invent findings to fill a quota. No defects is a valid result.",
    ),
    output_model=ReviewFindings,
    allowed_tools=frozenset({"rendered_preview_inspection"}),
)


class AccessibilityReviewerAgent(BaseAgent[ReviewFindings]):
    """Reviews the rendered page for accessibility defects."""

    @property
    def definition(self) -> AgentDefinition:
        return ACCESSIBILITY_REVIEWER_DEFINITION

    def _payload(self, context: AgentContext) -> RenderedPreviewPayload | None:
        candidate = context.inputs.get(ContextInput.RENDERED_PREVIEW.value)
        return candidate if isinstance(candidate, RenderedPreviewPayload) else None

    def _changed_files(self, context: AgentContext):
        return context.inputs.get(ContextInput.CHANGED_FILES.value)

    def evidence_blocks(self, context: AgentContext) -> list[dict]:
        """Supply the rendered evidence; ``base.py`` contributes everything else."""
        payload = self._payload(context)
        if payload is None:
            return [_NO_EVIDENCE_BLOCK]

        blocks: list[dict] = []
        if payload.dom_html is not None:
            blocks.append(_DOM_BLOCK)
        if payload.screenshot_base64 is not None:
            blocks.append(_SCREENSHOT_LABEL)
            blocks.append(_SCREENSHOT_BLOCK)
        if self._changed_files(context):
            blocks.append(_CHANGED_FILES_BLOCK)
        return blocks

    def prompt_variables(self, context: AgentContext) -> dict[str, str]:
        variables = super().prompt_variables(context)

        payload = self._payload(context)
        if payload is not None:
            if payload.dom_html is not None:
                variables["preview_dom"] = payload.dom_html
            if payload.screenshot_base64 is not None:
                variables["preview_media_type"] = payload.screenshot_media_type or ""
                variables["preview_screenshot"] = payload.screenshot_base64

        changed = self._changed_files(context)
        if changed:
            variables["changed_file_paths"] = "\n".join(
                f"- {getattr(f, 'path', f)}" for f in changed
            )
        return variables

    def task_instructions(self, context: AgentContext) -> str:
        payload = self._payload(context)
        has_dom = payload is not None and payload.dom_html is not None
        has_shot = payload is not None and payload.screenshot_base64 is not None

        if not has_dom and not has_shot:
            evidence = "No rendered evidence was supplied."
        elif has_dom and has_shot:
            evidence = (
                "You have the DOM and a screenshot. Judge structure and semantics "
                "from the DOM, and appearance-dependent concerns from the screenshot."
            )
        elif has_dom:
            evidence = (
                "You have the DOM only. Structure, semantics, alt text and ARIA are "
                "all visible to you. Contrast and target size are not — say so "
                "rather than guessing."
            )
        else:
            evidence = (
                "You have a screenshot only. You can comment on apparent contrast "
                "and apparent target size. You cannot see markup, so do not make "
                "claims about headings, landmarks, alt text or ARIA."
            )

        return (
            f"{context.task}\n\n"
            "WHAT YOU HAVE\n"
            f"- {evidence}\n"
            "\n"
            "WHAT TO CHECK\n"
            "- Semantics: is each element the one that means what the content is "
            "doing? A clickable div is a finding; a button is not.\n"
            "- Heading order: one level at a time, no skipping, and headings used "
            "for structure rather than for size.\n"
            "- Landmarks: can someone jump to the main content, the navigation, the "
            "footer?\n"
            "- Focus: what is reachable, in what order, and is anything interactive "
            "unreachable?\n"
            "- Text alternatives: images and icons that carry meaning need them; "
            "decorative ones should be hidden from assistive technology.\n"
            "- ARIA: correct where used, and not used where a native element would "
            "have done the job. Unnecessary ARIA is itself a finding.\n"
            "- Apparent contrast and apparent target size, from what you can see.\n"
            "\n"
            "THE LIMITS OF STATIC EVIDENCE\n"
            "- You have no computed styles and cannot interact with the page. You "
            "cannot measure a contrast ratio, confirm a focus indicator is visible, "
            "or verify that motion respects a reduced-motion preference.\n"
            "- Where a concern needs that, say what you observed and that it could "
            "not be confirmed. A finding that names its own uncertainty is useful; a "
            "confident number you did not compute is not, because someone will act "
            "on it.\n"
            "\n"
            "STAY IN YOUR LANE\n"
            "- Report access barriers, not aesthetics. 'The spacing feels tight' is "
            "the Visual & UX Critic's call. 'These two controls are close enough "
            "that a touch target would overlap' is yours.\n"
            "- A clean result is a real answer. Do not manufacture a finding.\n"
            "\n"
            "WRITING A FINDING\n"
            "- `source` is `accessibility_reviewer` and `category` is `a11y` on "
            "every finding.\n"
            "- `summary` names who is affected and how: not 'missing alt', but "
            "'the hero image carries the offer and has no text alternative, so it is "
            "silent to a screen reader'.\n"
            "- `location` names the element or region.\n"
            "- `severity`: `blocker` if it makes the page unusable for someone, "
            "`major` if it seriously impedes, `minor` for friction, `info` for "
            "something worth recording but not acting on."
        )
