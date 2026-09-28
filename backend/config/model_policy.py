"""Per-role Claude invocation policy: temperature, token budget and model tier.

WHY THIS FILE EXISTS
    ``docs/runtime.md`` 4.1 requires these defaults to be "centralized in config — not
    scattered across agent modules". If each agent set its own temperature, the
    project would have twelve independent, undocumented tuning decisions that nobody
    could review together. Here they sit in one table that can be read at a glance.

    It is separate from ``settings.py`` because it is not environment configuration:
    nothing here comes from ``.env``. It is a static policy table, and mixing it into
    the settings object would give that object two unrelated responsibilities.

LAYER
    Configuration (``backend/config/``). Deliberately free of Pydantic and of
    ``settings.py``: it imports only the standard library and the ``AgentRole``
    vocabulary, so it can be imported and tested on its own.

    Model *ids* are passed in rather than read from settings, so this module never
    reaches into the environment and callers keep a single source of configuration.

SINGLE PROVIDER
    Anthropic Claude only in v1. There is no provider field here and no
    multi-provider abstraction — adding one would be speculative complexity
    (CLAUDE.md 7, skills.md 10).

Spec: docs/runtime.md 4, 4.1; docs/agents.md 2; CLAUDE.md 7.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from backend.schemas.enums import AgentRole


class ModelTier(str, Enum):
    """Which configured model a role uses.

    A role opts into the heavier model through this table, never by hard-coding a
    model id in an agent module (docs/runtime.md 4.1).
    """

    DEFAULT = "default"
    HEAVY = "heavy"


@dataclass(frozen=True)
class RolePolicy:
    """How one role invokes the model.

    Frozen so a caller cannot mutate shared policy for every later invocation.
    """

    temperature: float
    max_tokens: int
    tier: ModelTier = ModelTier.DEFAULT
    rationale: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.temperature <= 1.0:
            raise ValueError(
                f"temperature must be between 0.0 and 1.0, got {self.temperature}"
            )
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")


# The table from docs/runtime.md 4.1, verbatim. Temperature tracks how much judgment
# a role legitimately exercises: objective checks run deterministic, design runs warm.
# Wrapped read-only so an import cannot quietly retune another caller's policy.
ROLE_POLICIES: Mapping[AgentRole, RolePolicy] = MappingProxyType(
    {
        AgentRole.REQUIREMENT_ANALYZER: RolePolicy(
            0.0, 4_000, rationale="structured extraction — determinism matters"
        ),
        AgentRole.PLANNER: RolePolicy(
            0.4, 8_000, rationale="structured but needs judgment"
        ),
        AgentRole.DESIGN_DIRECTOR: RolePolicy(
            0.6, 8_000, rationale="the most genuinely creative role"
        ),
        AgentRole.REFERENCE_ANALYZER: RolePolicy(
            0.2, 8_000, rationale="observation, not invention"
        ),
        AgentRole.FRONTEND_ARCHITECT: RolePolicy(
            0.2, 8_000, rationale="structural decisions"
        ),
        AgentRole.BUILDER: RolePolicy(
            0.2, 16_000, rationale="code generation — favour consistency"
        ),
        AgentRole.VISUAL_UX_CRITIC: RolePolicy(
            0.3, 6_000, rationale="judgment with consistency"
        ),
        AgentRole.ANTI_SLOP_REVIEWER: RolePolicy(
            0.2, 6_000, rationale="checklist-driven"
        ),
        AgentRole.ACCESSIBILITY_REVIEWER: RolePolicy(
            0.0, 6_000, rationale="objective criteria"
        ),
        AgentRole.CODE_REVIEWER: RolePolicy(
            0.0, 8_000, rationale="objective criteria"
        ),
        AgentRole.FIXER: RolePolicy(
            0.1, 12_000, rationale="minimal targeted edits"
        ),
        AgentRole.GITHUB_OPERATOR: RolePolicy(
            0.0, 4_000, rationale="tool invocation only"
        ),
    }
)


class UnknownRolePolicyError(KeyError):
    """No policy is defined for the requested role.

    Raised rather than falling back to a default: an unpolicied role would run with
    silently invented parameters, which is exactly the undocumented tuning this
    module exists to prevent.
    """


def policy_for(role: AgentRole) -> RolePolicy:
    """The invocation policy for ``role``."""
    try:
        return ROLE_POLICIES[role]
    except KeyError:
        raise UnknownRolePolicyError(
            f"No model policy defined for role '{role.value}'"
        ) from None


def resolve_model_id(role: AgentRole, *, default_model: str, heavy_model: str) -> str:
    """Pick the model id for ``role`` from the two configured ids.

    Both ids are supplied by the caller (from ``Settings``), so this module never
    reads the environment and a role can never hard-code a model.
    """
    policy = policy_for(role)
    return heavy_model if policy.tier is ModelTier.HEAVY else default_model
