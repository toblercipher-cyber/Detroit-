"""Project lifecycle service: create, read, list, delete, and run the workflow.

WHY THIS FILE EXISTS
    ``docs/architecture.md`` 3.2 requires route handlers to stay thin — validate,
    delegate, return a typed response. This is what they delegate to. Putting this
    logic in the routes would mean orchestration, persistence and HTTP concerns
    tangled in one place, and the workflow would become impossible to drive from
    anywhere but an HTTP request.

    It is the seam where the three lower layers meet: the repository (durable state),
    the graph (stage transitions), and configuration (limits).

LAYER
    Services (``backend/services/``). Depends on config, schemas, memory and graph.
    It imports nothing from ``backend/api`` and knows no HTTP: it raises typed errors
    and the API layer maps them to status codes. That keeps the service usable by a
    future CLI or scheduled run without dragging FastAPI along.

SESSIONS
    A project is the durable unit of work; a session is one client attachment to it.
    Losing a session never loses project state — that separation is the point
    (docs/state.md 5.3).

WHAT THIS FILE DOES NOT DO
    - It does not schedule background work. ``run_workflow`` is awaitable and the
      caller decides how to run it; inventing a task runner here would be
      infrastructure nobody asked for (docs/api.md 2.2).
    - It does not resolve HITL decisions. That path owns ``approved_decisions`` and
      ``hitl_history`` and belongs in its own service (docs/state.md 3).

Spec: docs/api.md 2.2; docs/architecture.md 3.2, 4; docs/state.md 5.2, 5.3;
      skills.md 23.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.config.settings import Settings
from backend.memory.artifacts import (
    ProjectNotFoundError,
    StateRepository,
)
from backend.schemas.enums import RunStatus, WorkflowStage
from backend.schemas.state import ProjectState, new_session_id

logger = logging.getLogger(__name__)

# Stages during which the project is mid-flight. Accepting a new instruction or a
# delete here would race the running graph, so both are refused (docs/api.md 2.2).
_BUSY_STAGES: frozenset[WorkflowStage] = frozenset(
    {
        WorkflowStage.BUILD,
        WorkflowStage.PREVIEW,
        WorkflowStage.EVALUATION,
        WorkflowStage.TARGETED_FIX,
        WorkflowStage.RE_EVALUATION,
        WorkflowStage.EXTERNAL_ACTION,
    }
)

# Title fallback length when the user supplies none. Long enough to recognise a
# project in a list, short enough not to become a paragraph.
_DERIVED_TITLE_MAX = 60


class ProjectServiceError(RuntimeError):
    """Base class for service-level failures the API maps to status codes."""


class ProjectBusyError(ProjectServiceError):
    """The project is mid-stage and cannot accept this action right now."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _derive_title(prompt: str) -> str:
    """A readable title from the opening of the request.

    Deliberately mechanical rather than model-generated: naming a project should not
    cost a model call, and the user can rename it.
    """
    first_line = prompt.strip().splitlines()[0] if prompt.strip() else "Untitled project"
    condensed = " ".join(first_line.split())
    if len(condensed) <= _DERIVED_TITLE_MAX:
        return condensed
    return condensed[: _DERIVED_TITLE_MAX - 1].rstrip() + "…"


class ProjectService:
    """Owns the lifecycle of a project and the runs performed against it."""

    def __init__(
        self,
        repository: StateRepository,
        workflow: Any,
        settings: Settings,
    ) -> None:
        """
        Args:
            repository: durable state. Injected as the interface, never a concrete
                store, so Supabase can replace the filesystem later without touching
                this class (docs/state.md 5.0).
            workflow: the compiled LangGraph workflow.
            settings: validated configuration; supplies the iteration limit.
        """
        self._repository = repository
        self._workflow = workflow
        self._settings = settings

    # --- Lifecycle -------------------------------------------------------

    def create(
        self,
        prompt: str,
        *,
        title: str | None = None,
        surface_hint: str | None = None,
    ) -> ProjectState:
        """Create a project and persist it before any work begins.

        Persisting first is deliberate: if the workflow then fails, the project still
        exists and the user can see why, rather than the request vanishing.
        """
        state = ProjectState(
            user_request=prompt,
            title=title.strip() if title and title.strip() else _derive_title(prompt),
            current_stage=WorkflowStage.REQUIREMENT_ANALYSIS,
            run_status=RunStatus.IDLE,
        )
        if surface_hint:
            # Recorded as a scratch hint rather than a requirement: the user said it,
            # but only the Requirement Analyzer decides what becomes a requirement.
            state.node_scratch["surface_hint"] = surface_hint

        self._repository.save(state)
        logger.info(
            "project created project_id=%s session_id=%s",
            state.project_id,
            state.session_id,
        )
        return state

    def get(self, project_id: str) -> ProjectState:
        """Load stored state, or raise :class:`ProjectNotFoundError`."""
        return self._repository.load(project_id)

    def list_summaries(self) -> list[ProjectState]:
        """Stored projects, newest first.

        Loads each project's state rather than returning ids alone, because a summary
        needs the title, stage and status. A future store can answer this with one
        query behind the same interface.
        """
        states: list[ProjectState] = []
        for project_id in self._repository.list_project_ids():
            try:
                states.append(self._repository.load(project_id))
            except ProjectNotFoundError:
                continue
            except Exception:
                # One unreadable project must not make the whole list unavailable.
                logger.warning("skipping unreadable project in listing")
                continue
        return states

    def delete(self, project_id: str) -> None:
        """Delete a project, refusing while a run is in flight.

        Irreversible, so it happens only on an explicit request and never as a side
        effect of generation (docs/api.md 2.2).
        """
        state = self._repository.load(project_id)
        self._ensure_not_busy(state)
        self._repository.delete(project_id)

    def attach_session(self, project_id: str) -> ProjectState:
        """Issue a new session for an existing project.

        A session carries no approved decisions, so re-attaching never changes what
        the project has settled (docs/state.md 5.3).
        """
        state = self._repository.load(project_id)
        state.session_id = new_session_id()
        state.updated_at = _utcnow()
        self._repository.save(state)
        return state

    # --- Running the workflow --------------------------------------------

    async def run_workflow(self, project_id: str) -> ProjectState:
        """Advance the project through the graph and persist the result.

        Awaitable rather than self-scheduling: the caller decides whether this runs in
        a background task, a worker or a test. State is saved whether the run
        succeeded or failed, so a failure is inspectable rather than lost
        (CLAUDE.md 26).
        """
        state = self._repository.load(project_id)

        state.run_status = RunStatus.RUNNING
        state.updated_at = _utcnow()
        self._repository.save(state)

        config = {"configurable": {"thread_id": project_id}}
        try:
            result = await self._workflow.ainvoke(state, config=config)
        except Exception:
            # The graph records agent failures in state itself; reaching here means
            # the graph machinery failed, which is not something to route around.
            logger.exception("workflow run failed project_id=%s", project_id)
            state.run_status = RunStatus.FAILED
            state.updated_at = _utcnow()
            self._repository.save(state)
            raise

        updated = (
            result
            if isinstance(result, ProjectState)
            else ProjectState.model_validate(result)
        )
        updated.updated_at = _utcnow()
        self._repository.save(updated)
        logger.info(
            "workflow run complete project_id=%s stage=%s status=%s",
            project_id,
            updated.current_stage.value,
            updated.run_status.value,
            )
        return updated

    # --- Derived values used by the API ----------------------------------

    @property
    def max_iterations(self) -> int:
        """The automatic-correction limit, from configuration rather than a literal."""
        return self._settings.max_auto_iterations

    def preview_available(self, state: ProjectState) -> bool:
        """True when a successful build has produced a preview."""
        return state.preview_status.preview_url is not None

    # --- Internals -------------------------------------------------------

    def _ensure_not_busy(self, state: ProjectState) -> None:
        if state.run_status is RunStatus.RUNNING and state.current_stage in _BUSY_STAGES:
            raise ProjectBusyError(f"Project is busy in stage '{state.current_stage.value}'.")
