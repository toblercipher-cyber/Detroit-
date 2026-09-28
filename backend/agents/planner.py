"""The Planner: turns approved requirements into the plan every later stage consumes.

WHY THIS FILE EXISTS
    The plan is the contract between understanding the request and building it. Once
    approved it is what the Design Director, the Frontend Architect and the Builder
    work from — none of them re-reads the original prompt. If the plan is vague, each
    of those stages invents its own interpretation and they quietly diverge.

    So the output has to be specific enough to build from and complete enough that
    nothing downstream needs to guess.

LAYER
    Agents (``backend/agents/``). It declares a contract and supplies prompt framing;
    execution, structured-output validation and failure classification live in
    ``base.py``.

THE LINE THIS ROLE MUST NOT CROSS
    Planning is structure, not appearance and not code. Fonts, palettes, spacing and
    component treatment belong to the Design Director; files and implementation
    belong to the Builder. A planner that starts specifying hex codes has taken a
    decision the design stage — and its HITL gate — was supposed to make
    (skills.md 14, docs/agents.md 4.2).

CONTRACT SOURCE
    Transcribed from ``docs/agents.md`` 4.2. Update it from that document rather than
    editing it to suit the implementation.

Spec: docs/agents.md 3, 4.2; docs/state.md 6; docs/workflow.md 1; CLAUDE.md 12;
      skills.md 14.
"""

from __future__ import annotations

from backend.agents.base import AgentDefinition, BaseAgent
from backend.context.builder import AgentContext
from backend.schemas.enums import AgentRole
from backend.schemas.state import Plan

# Constructed at import time so an incomplete contract fails on load, not on first run.
PLANNER_DEFINITION = AgentDefinition(
    role=AgentRole.PLANNER,
    purpose=(
        "Produce the implementation plan that later stages consume without "
        "reinterpreting the original prompt."
    ),
    responsibilities=(
        "State the objective and the audience.",
        "Define the page and section structure.",
        "Define the content hierarchy.",
        "Define the CTA strategy.",
        "State the interaction and responsive requirements.",
        "State the design intent, without making design-system decisions.",
        "Record the technical constraints.",
        "Write acceptance criteria that a reviewer could actually check.",
    ),
    prohibited_actions=(
        "Do not write or modify code, and do not name files to create.",
        "Do not make design-system decisions — no fonts, palettes, hex values, "
        "spacing scales or component styling. Those belong to the Design Director.",
        "Do not re-open or restate approved decisions as choices still to be made.",
        "Do not expand scope beyond the surfaces the requirements asked for.",
        "Do not resolve a recorded requirement conflict on your own.",
    ),
    output_model=Plan,
    # Pure reasoning over the approved requirements — this role calls nothing.
    allowed_tools=frozenset(),
)


class PlannerAgent(BaseAgent[Plan]):
    """Produces the approved-plan candidate from normalized requirements."""

    @property
    def definition(self) -> AgentDefinition:
        return PLANNER_DEFINITION

    def task_instructions(self, context: AgentContext) -> str:
        """Role-specific framing appended to the shared contract prompt.

        The emphases here are the two ways a plan fails in practice: it is too vague
        to build from, or it quietly decides something that belonged to a later
        stage and its approval gate.
        """
        return (
            f"{context.task}\n\n"
            "WHAT MAKES THIS PLAN USABLE\n"
            "- Later stages build from this and never re-read the user's original "
            "request. Anything you leave implicit will be guessed at, differently, "
            "by each of them.\n"
            "- Be concrete. 'A hero section' is not a plan; what the hero has to "
            "communicate, and what the visitor should do next, is.\n"
            "- Cover only the surfaces the requirements asked for. Extra pages are "
            "scope you invented, and someone has to review or delete them.\n"
            "\n"
            "STRUCTURE VS. APPEARANCE\n"
            "- `design_intent` is the mood and the reasoning — who this is for, how "
            "it should feel, what the visual system has to achieve.\n"
            "- It is NOT the visual system itself. No fonts, no colours, no hex "
            "values, no spacing scales, no component styling. Those are the Design "
            "Director's decisions and they pass through their own review.\n"
            "\n"
            "APPROVED DECISIONS\n"
            "- Build the plan around them. Do not list a settled decision as an open "
            "question or an alternative to weigh.\n"
            "\n"
            "CONFLICTS AND GAPS\n"
            "- Conflicts recorded in the requirements are the user's to resolve. Plan "
            "around them where you can and leave them flagged; do not silently pick "
            "a side.\n"
            "- If a gap genuinely blocks part of the plan, put it in "
            "`open_questions` and plan the rest. Do not invent the missing decision "
            "and do not stall the whole plan on it.\n"
            "\n"
            "ACCEPTANCE CRITERIA\n"
            "- Write criteria someone could check against the built page and get a "
            "clear yes or no. 'Looks professional' cannot be checked; 'every section "
            "is reachable from the top navigation' can."
        )
