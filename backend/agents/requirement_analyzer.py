"""The Requirement Analyzer: the first stage of the pipeline.

WHY THIS FILE EXISTS
    Everything downstream consumes structured requirements rather than the raw
    prompt. If normalization is skipped, every later stage re-interprets the original
    request from scratch and the user's explicit decisions get quietly reworded into
    something the agent found easier. This role exists to make that impossible: it
    converts one free-text request into typed fields, once, and records what the user
    actually said.

    It also decides how much the user will be asked. ``input_density`` drives adaptive
    HITL — a detailed brief earns fewer questions, a vague one earns targeted
    clarification (docs/workflow.md 2). Getting that classification wrong is what
    makes a system either pester a user who already answered, or guess silently on
    their behalf.

LAYER
    Agents (``backend/agents/``). It declares a contract and supplies prompt framing;
    the shared execution path, structured-output validation and failure classification
    all live in ``base.py``.

CONTRACT SOURCE
    The declaration below is a transcription of ``docs/agents.md`` 4.1. It is not an
    interpretation, and it must be updated from that document rather than edited to
    suit the implementation.

Spec: docs/agents.md 3, 4.1; docs/state.md 6; docs/workflow.md 1, 2; skills.md 12.
"""

from __future__ import annotations

from backend.agents.base import AgentDefinition, BaseAgent
from backend.context.builder import AgentContext
from backend.schemas.enums import AgentRole
from backend.schemas.state import NormalizedRequirements

# Constructed at import time, so an incomplete contract fails when the module loads
# rather than when the agent first runs (base.AgentDefinition validates on init).
REQUIREMENT_ANALYZER_DEFINITION = AgentDefinition(
    role=AgentRole.REQUIREMENT_ANALYZER,
    purpose=(
        "Turn a raw prompt into structured requirements and surface what is missing "
        "or contradictory, before any planning happens."
    ),
    responsibilities=(
        "Normalize the request into typed fields.",
        "Preserve every explicit user decision verbatim.",
        "Detect conflicts between stated requirements.",
        "Detect gaps that genuinely block planning.",
        "Classify the request as sparse or detailed to drive adaptive HITL.",
    ),
    prohibited_actions=(
        "Do not invent requirements the user did not state.",
        "Do not resolve conflicts yourself — report them.",
        "Do not design: no palettes, typography, layouts or visual direction.",
        "Do not plan: no page structure, section ordering or implementation steps.",
        "Do not raise questions the user has already answered, in the request or in "
        "the approved decisions.",
    ),
    output_model=NormalizedRequirements,
    # Pure reasoning over provided context — this role calls nothing.
    allowed_tools=frozenset(),
)


class RequirementAnalyzerAgent(BaseAgent[NormalizedRequirements]):
    """Normalizes a free-text request into :class:`NormalizedRequirements`."""

    @property
    def definition(self) -> AgentDefinition:
        return REQUIREMENT_ANALYZER_DEFINITION

    def task_instructions(self, context: AgentContext) -> str:
        """Role-specific framing appended to the shared contract prompt.

        Written as rules about *fidelity* rather than about quality. The failure mode
        here is not a poor answer — it is a plausible one that quietly replaces what
        the user asked for.
        """
        return (
            f"{context.task}\n\n"
            "HOW TO NORMALIZE\n"
            "- Record what the request actually says. Where the user was explicit, "
            "carry their wording into `explicit_decisions` rather than paraphrasing "
            "it. A reworded decision is a changed decision.\n"
            "- Leave a field empty when the request does not cover it. An empty list "
            "is an honest answer; a plausible guess is not.\n"
            "- `goal` is the outcome the user wants, in their terms.\n"
            "\n"
            "CONFLICTS\n"
            "- A conflict is two stated requirements that cannot both hold. Record "
            "both sides in `conflicts` and stop there.\n"
            "- Do not pick a winner, and do not soften one side to make them fit. "
            "Resolving a conflict is the user's decision, not yours.\n"
            "\n"
            "GAPS\n"
            "- A gap is a decision that planning genuinely cannot proceed without.\n"
            "- Something merely unspecified is not a gap if a sensible default "
            "exists or a later stage will decide it. Every gap becomes a question "
            "someone has to answer, so a padded list costs the user real time.\n"
            "- Never list something the request or the approved decisions already "
            "settle.\n"
            "\n"
            "INPUT DENSITY\n"
            "- `detailed`: the request already fixes most of what is needed — pages "
            "or surfaces, sections, visual direction, interactions, responsive "
            "behaviour, acceptance criteria. Downstream stages should validate and "
            "execute rather than re-brainstorm.\n"
            "- `sparse`: important decisions are absent and must be discovered "
            "through targeted clarification.\n"
            "- Judge this by how much is actually decided, not by how long the "
            "request is. A long, repetitive request can still be sparse."
        )
