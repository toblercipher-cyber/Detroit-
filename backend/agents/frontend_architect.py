"""The Frontend Architect: decides the structure the Builder will implement.

WHY THIS FILE EXISTS
    Between "here is the design system" and "here is the code" sits a decision
    nobody should make while typing: what components exist, which already exist, how
    they nest, and which file holds what. Left to the Builder, that decision gets
    made incrementally — and incremental structure is how a project ends up with
    three cards that are nearly the same component.

    This is also the stage that decides how much gets written at all. Every component
    it proposes is code someone has to review and maintain, so the useful instinct is
    reuse first, new only when nothing fits.

LAYER
    Agents (``backend/agents/``). Declares a contract and supplies prompt framing;
    the RunnableSequence, validation and failure classification live in ``base.py``.

THE BOUNDARY WITH EXISTING WORK
    An approved page is not raw material. Restructuring one is allowed only as a
    *scoped technical change* — a change that leaves its rendered output visually
    identical, such as hoisting a shared nav so a second route can mount it.
    Anything that alters how an approved page looks is a redesign and needs the
    user, not an architect's judgement (docs/frontend.md 3).

CONTRACT SOURCE
    Transcribed from ``docs/agents.md`` 4.5.

Spec: docs/agents.md 3, 4.5; docs/frontend.md 3; docs/state.md 6; CLAUDE.md 17, 29;
      skills.md 25.
"""

from __future__ import annotations

from backend.agents.base import AgentDefinition, BaseAgent
from backend.context.builder import AgentContext, ContextInput
from backend.schemas.enums import AgentRole
from backend.schemas.state import FrontendArchitecture

# Constructed at import time so an incomplete contract fails on load, not on first run.
FRONTEND_ARCHITECT_DEFINITION = AgentDefinition(
    role=AgentRole.FRONTEND_ARCHITECT,
    purpose="Define the page and component structure to be built.",
    responsibilities=(
        "Define the page composition.",
        "Define the component tree and how components nest.",
        "Identify which components are reusable.",
        "Reuse existing project components before proposing new ones.",
        "Define the responsive structure.",
        "Define state boundaries — which component owns which piece of state.",
        "Produce a file plan the Builder can follow.",
    ),
    prohibited_actions=(
        "Do not write implementation code, markup, CSS or class names.",
        "Do not introduce unnecessary abstraction — no wrapper that exists only to "
        "wrap, and no shared component invented for a single use.",
        "Do not propose new dependencies or libraries.",
        "Do not restructure an approved existing page beyond a scoped technical "
        "change that leaves its rendered output visually identical.",
        "Do not re-decide the design system; consume it as given.",
    ),
    output_model=FrontendArchitecture,
    # Repository read of the frontend source, which reaches this role as the
    # component inventory in its context rather than as a call it makes itself.
    allowed_tools=frozenset({"repository_read"}),
)


class FrontendArchitectAgent(BaseAgent[FrontendArchitecture]):
    """Produces the component architecture from the approved plan and design spec."""

    @property
    def definition(self) -> AgentDefinition:
        return FRONTEND_ARCHITECT_DEFINITION

    def task_instructions(self, context: AgentContext) -> str:
        """Role-specific framing appended to the shared contract prompt.

        The inventory is called out explicitly when present, because "reuse what
        exists" is the responsibility most easily skipped: proposing a fresh
        component is always easier than reading what is already there.
        """
        inventory = context.inputs.get(ContextInput.COMPONENT_INVENTORY.value)
        if inventory:
            reuse_note = (
                "The project's existing components are listed under TASK INPUTS. "
                "Work through them before proposing anything new.\n"
                "- Put every component you will reuse in `reused_components`, named "
                "exactly as the inventory names it.\n"
                "- A new component needs a reason: say in the file plan what it does "
                "that no existing one does.\n"
                "- Two components that differ only in content are one component with "
                "a prop."
            )
        else:
            # Stated plainly rather than left silent: an empty inventory is a real
            # situation (a fresh project), and the model should not infer that
            # something exists which it was simply not shown.
            reuse_note = (
                "No existing component inventory was supplied, so treat this as a "
                "project with nothing built yet. Do not assume a component exists "
                "that you have not been shown."
            )

        return (
            f"{context.task}\n\n"
            "REUSE BEFORE NEW\n"
            f"{reuse_note}\n"
            "\n"
            "STRUCTURE, NOT IMPLEMENTATION\n"
            "- Decide what exists and how it nests. The Builder decides how it is "
            "written.\n"
            "- No markup, no CSS, no class names, no props signatures.\n"
            "- The file plan says which file holds which component, and nothing "
            "about its contents.\n"
            "\n"
            "KEEP IT AS SMALL AS IT CAN BE\n"
            "- Every component you propose is code someone maintains. A component "
            "used once, in one place, is usually a section of its parent.\n"
            "- Do not add a layer whose only job is to pass values through.\n"
            "- Depth is a cost: a tree four levels deep is harder to follow than a "
            "flat one that names things well.\n"
            "\n"
            "STATE BOUNDARIES\n"
            "- Say which component owns each piece of state, and keep it as close to "
            "where it is used as possible.\n"
            "- Note where state has to be shared, because that is what decides "
            "whether something is one component or two.\n"
            "\n"
            "RESPONSIVE STRUCTURE\n"
            "- Describe how the composition reflows, not the breakpoint values. "
            "Whether a two-column split becomes stacked is structure; the pixel "
            "width it happens at belongs to the design system.\n"
            "\n"
            "EXISTING APPROVED PAGES\n"
            "- An approved page may be restructured only if its rendered output "
            "stays visually identical — hoisting a shared element so another route "
            "can mount it is fine.\n"
            "- Anything that changes how an approved page looks is a redesign and is "
            "not yours to decide. Note it as something the user must approve instead."
        )
