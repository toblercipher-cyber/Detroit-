"""The Design Director: turns an approved plan into one coherent visual system.

WHY THIS FILE EXISTS
    Without this stage the Builder makes styling decisions component by component,
    and the result is a page where nothing quite agrees with anything else — a
    different radius here, a fourth shade of grey there. One system decided up front
    is what makes the output look designed rather than assembled.

    The output is also what the anti-slop gate later checks against. A finding only
    means something if there is an approved system it can contradict.

LAYER
    Agents (``backend/agents/``). Declares a contract and supplies prompt framing;
    the RunnableSequence, validation and failure classification live in ``base.py``.

THE DECISION THIS ROLE MUST NOT OVERRIDE
    A font, palette or visual direction the user specified is approved truth. It
    arrives as a constraint and stays exactly as given. Replacing one with something
    the model likes better is the single most damaging thing this role can do,
    because it is the failure the user is least likely to notice until the build is
    finished (skills.md 15, 31).

CONTRACT SOURCE
    Transcribed from ``docs/agents.md`` 4.3. Update it from that document rather than
    editing it to suit the implementation.

Spec: docs/agents.md 3, 4.3; docs/design.md 2, 5.4; docs/state.md 6; CLAUDE.md 13, 15;
      skills.md 15, 31.
"""

from __future__ import annotations

from backend.agents.base import AgentDefinition, BaseAgent
from backend.context.builder import AgentContext
from backend.schemas.enums import AgentRole
from backend.schemas.state import DesignSpec

# Constructed at import time so an incomplete contract fails on load, not on first run.
DESIGN_DIRECTOR_DEFINITION = AgentDefinition(
    role=AgentRole.DESIGN_DIRECTOR,
    purpose="Translate the approved plan into one coherent design system.",
    responsibilities=(
        "Define the typography scale and weights.",
        "Define the color system.",
        "Define the spacing rhythm and the layout grid.",
        "Define density and radius.",
        "Define the button, card and container language.",
        "Define image treatment and motion principles.",
        "State the overall visual tone.",
        "Preserve design decisions the user has already approved.",
        "Explain the reasoning, so later stages can tell intent from accident.",
    ),
    prohibited_actions=(
        "Do not replace a user-specified font, palette or visual direction. Those "
        "are approved truth; carry them through exactly as given.",
        "Do not invent a second design system alongside the first.",
        "Do not produce disconnected styling choices — every value must belong to "
        "the same system.",
        "Do not write code, CSS, class names or component markup.",
        "Do not re-open decisions recorded as approved.",
    ),
    output_model=DesignSpec,
    # Pure reasoning over the approved plan — this role calls nothing.
    allowed_tools=frozenset(),
)


class DesignDirectorAgent(BaseAgent[DesignSpec]):
    """Produces the design-system candidate from the approved plan."""

    @property
    def definition(self) -> AgentDefinition:
        return DESIGN_DIRECTOR_DEFINITION

    def task_instructions(self, context: AgentContext) -> str:
        """Role-specific framing appended to the shared contract prompt.

        Two things are pushed hard here. First, coherence: the individual values
        matter far less than whether they belong to one another. Second, the
        anti-slop patterns — stated as named defaults to avoid rather than as a
        vague plea for quality, because "avoid generic design" is not actionable and
        "do not reach for a gradient by default" is.
        """
        return (
            f"{context.task}\n\n"
            "ONE SYSTEM, NOT A LIST OF CHOICES\n"
            "- Every value has to belong with the others. A radius, a shadow and a "
            "type scale chosen independently will read as three unrelated ideas.\n"
            "- Decide the relationships, not just the values: how heading sizes step "
            "down, how spacing multiplies, how a card relates to a button.\n"
            "- Put the reasoning in `rationale`. Later stages use it to tell a "
            "deliberate choice from an accidental one, and a reviewer needs that to "
            "know what not to 'fix'.\n"
            "\n"
            "APPROVED DECISIONS ARE NOT SUGGESTIONS\n"
            "- A font, palette or direction the user fixed is approved truth. Carry "
            "it through exactly. Do not substitute something you consider better, "
            "and do not quietly adjust it to fit the rest of your system — build the "
            "rest of the system around it.\n"
            "- If an approved decision genuinely conflicts with the plan, say so in "
            "`rationale`. Do not resolve it by overriding the user.\n"
            "\n"
            "DEFAULTS WORTH RESISTING\n"
            "These are the patterns that make generated interfaces look generated. "
            "Use one only when the plan gives a specific reason:\n"
            "- gradients used as decoration rather than to convey depth or state\n"
            "- glassmorphism and heavy blur\n"
            "- every surface a rounded card of the same radius\n"
            "- grids of icon tiles standing in for content\n"
            "- headings that differ only slightly in size, so nothing leads\n"
            "- decorative blobs, glows and large drop shadows\n"
            "- the generic SaaS composition: hero, three feature cards, testimonial, "
            "call to action\n"
            "- spacing that varies without a rule behind it\n"
            "\n"
            "The goal is not novelty. An unusual system that fights the content is "
            "worse than a plain one that serves it. Aim for coherent and purposeful."
        )
