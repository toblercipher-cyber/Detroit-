"""The two-phase approval gate for external writes.

``docs/mcp.md`` 5: a plan is produced without mutating anything, the user sees
exactly what would change, and execution happens only against a single-use,
short-TTL token. This file owns that gate. It does not execute anything.

Why the store is not ``ProjectState``: a live approval token written into
``project_state.json`` would sit in a human-review artifact long after its TTL,
and nothing in ``docs/state.md`` 2 gives it a home. ``mcp_action_history``
records the *outcome* of an action, not permission to perform one.

Spec: docs/mcp.md 5, 6; docs/api.md 2.7, 5; docs/security.md 6.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.schemas.api import (
    ErrorCode,
    ExternalActionExecuteRequest,
    ExternalActionPlan,
    ExternalActionRequest,
    ExternalActionType,
)

logger = logging.getLogger(__name__)

# Short, per docs/mcp.md 5. Long enough to read a plan and decide, short enough
# that an unused approval stops being usable quickly.
DEFAULT_TTL_SECONDS = 300

# Params echoed back in the plan. An allowlist rather than the whole dict: params
# are client-supplied and a plan is shown to a user, so nothing unexpected is
# rendered into it (docs/security.md 6).
_DESCRIBABLE_PARAMS: tuple[str, ...] = (
    "repository", "owner", "repo", "branch", "base", "head", "title", "path", "message",
)


class ExternalActionError(RuntimeError):
    """Base for approval-gate failures."""


class CapabilityUnavailableError(ExternalActionError):
    """The requested action is not available on the connected server.

    501. Capabilities are discovered, never assumed, so "no server connected"
    and "server lacks this tool" are the same answer: we cannot do it, and we
    do not substitute something else (docs/mcp.md 4).
    """

    code = ErrorCode.CAPABILITY_UNAVAILABLE


class ApprovalRequiredError(ExternalActionError):
    """No valid approval accompanies this execution. 403."""

    code = ErrorCode.APPROVAL_REQUIRED


class ApprovalExpiredError(ExternalActionError):
    """The approval was valid once but is expired or already spent. 410."""

    code = ErrorCode.APPROVAL_EXPIRED


@dataclass
class PendingPlan:
    """An issued plan awaiting execution.

    The token is stored as a SHA-256 digest, never in the clear. The store is
    in-memory today, but a plan that leaks through a log, a debugger or a future
    persistence layer should not hand anyone the approval.
    """

    plan_id: str
    project_id: str
    action_type: ExternalActionType
    summary: str
    changes: list[str]
    params: dict[str, Any]
    expires_at: datetime
    token_digest: str = field(repr=False)
    spent_at: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def is_spent(self) -> bool:
        return self.spent_at is not None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def describe_action(action_type: ExternalActionType, params: dict[str, Any]) -> tuple[str, list[str]]:
    """A user-facing description of what would change.

    Built only from the request: this restates what was asked for, it does not
    consult or assume any server capability. Values are rendered from an
    allowlist of keys so an arbitrary param cannot appear in the plan.
    """
    described = [
        f"{key}: {params[key]}"
        for key in _DESCRIBABLE_PARAMS
        if key in params and isinstance(params[key], (str, int, bool))
    ]
    extra = len(params) - len(described)
    if extra > 0:
        described.append(f"{extra} further parameter(s) not shown")

    summary = f"Perform '{action_type}' on the target repository."
    changes = described or ["No parameters were supplied."]
    # None of the four supported actions rewrites history; a destructive action
    # would need its own type and its own explicit authorization (docs/mcp.md 2.1).
    changes.append("Repository history is not rewritten by this action.")
    return summary, changes


class ExternalActionService:
    """Issues plans and validates approvals. Executes nothing.

    Execution belongs to the GitHub Operator behind this gate; keeping the two
    apart is what makes "no mutation without a valid token" checkable in one
    place.
    """

    def __init__(
        self,
        capability_check: Callable[[ExternalActionType], bool] | None = None,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        """
        Args:
            capability_check: asks whether an action is supported by the
                *discovered* capability set. ``None`` means nothing has been
                discovered, so every action is unavailable -- which is the
                correct answer while no MCP server is connected
                (docs/mcp.md 1).
            ttl_seconds: approval lifetime.
            clock: injected so expiry is testable without sleeping.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._capability_check = capability_check
        self._ttl = ttl_seconds
        self._clock = clock
        self._plans: dict[str, PendingPlan] = {}

    def plan(
        self, project_id: str, request: ExternalActionRequest
    ) -> ExternalActionPlan:
        """Produce a non-mutating plan and a single-use approval token.

        Raises:
            CapabilityUnavailableError: the action is not available (501).
        """
        if self._capability_check is None or not self._capability_check(
            request.action_type
        ):
            logger.info(
                "external action unavailable project_id=%s action=%s",
                project_id,
                request.action_type,
            )
            raise CapabilityUnavailableError(
                f"'{request.action_type}' is not available on the connected server."
            )

        now = self._clock()
        self._purge_expired(now)

        token = secrets.token_urlsafe(32)
        plan_id = f"plan_{secrets.token_hex(16)}"
        summary, changes = describe_action(request.action_type, request.params)
        expires_at = now + timedelta(seconds=self._ttl)

        self._plans[plan_id] = PendingPlan(
            plan_id=plan_id,
            project_id=project_id,
            action_type=request.action_type,
            summary=summary,
            changes=changes,
            params=dict(request.params),
            expires_at=expires_at,
            token_digest=_digest(token),
        )
        # The token is returned to the caller and never logged.
        logger.info(
            "external action planned project_id=%s action=%s plan_id=%s",
            project_id,
            request.action_type,
            plan_id,
        )
        return ExternalActionPlan(
            plan_id=plan_id,
            action_type=request.action_type,
            summary=summary,
            changes=changes,
            approval_token=token,
            expires_at=expires_at,
        )

    def consume(
        self, project_id: str, request: ExternalActionExecuteRequest
    ) -> PendingPlan:
        """Validate an approval and spend it, returning the plan to execute.

        Order matters. The token is checked before expiry, so a caller without
        the token learns only that it is invalid (403) and never whether a plan
        exists or has already been used.

        Spending happens here rather than after execution: an approval is for
        one attempt, so a failed execution needs a fresh one
        (docs/mcp.md 5, "approval is per action").

        Raises:
            ApprovalRequiredError: unknown plan, wrong project, or bad token (403).
            ApprovalExpiredError: expired or already spent (410).
        """
        plan = self._plans.get(request.plan_id)

        # One response for unknown, foreign and mistoken plans: distinguishing
        # them would let a caller probe for valid plan ids.
        if (
            plan is None
            or plan.project_id != project_id
            or not secrets.compare_digest(
                plan.token_digest, _digest(request.approval_token)
            )
        ):
            logger.warning(
                "external action rejected: invalid approval project_id=%s", project_id
            )
            raise ApprovalRequiredError("A valid approval token is required.")

        now = self._clock()
        if plan.is_spent:
            logger.warning(
                "external action rejected: approval already spent plan_id=%s",
                plan.plan_id,
            )
            raise ApprovalExpiredError("This approval has already been used.")
        if plan.is_expired(now):
            logger.warning(
                "external action rejected: approval expired plan_id=%s", plan.plan_id
            )
            raise ApprovalExpiredError("This approval has expired.")

        plan.spent_at = now
        logger.info(
            "external action approved project_id=%s action=%s plan_id=%s",
            project_id,
            plan.action_type,
            plan.plan_id,
        )
        return plan

    def _purge_expired(self, now: datetime) -> None:
        """Drop plans past their TTL so the store does not grow without bound.

        Spent plans are kept until they expire, so a replay gets "already used"
        rather than "unknown", which is the more accurate answer.
        """
        stale = [
            pid for pid, plan in self._plans.items() if plan.is_expired(now)
        ]
        for pid in stale:
            del self._plans[pid]
