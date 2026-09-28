"""HTTP routes for the project lifecycle.

WHY THIS FILE EXISTS
    It is the thin edge of the backend: parse and validate a request, delegate to
    ``ProjectService``, translate a typed service error into a status code, return a
    typed response. Nothing else. Orchestration, persistence and agent reasoning all
    live below this layer (docs/architecture.md 3.2, skills.md 8).

    Every handler here should read as four lines of plumbing. If one starts making
    decisions, that logic belongs in the service.

LAYER
    API (``backend/api/``). Depends on schemas and services. It is imported by
    ``main.py``, which composes it; it imports nothing from ``main``.

ERROR TRANSLATION
    Service and repository errors are raised as ``HTTPException`` with a status code.
    ``main.py`` wraps every one of those in the single documented ``ErrorResponse``
    envelope, so no handler here builds an error body itself and no two handlers can
    shape one differently (docs/api.md 5).

    ``InvalidProjectIdError`` deliberately maps to 404, not 400: a traversal probe
    must be indistinguishable from an unknown project (docs/security.md 3.4).

WHAT IS NOT HERE YET
    ``POST /{project_id}/messages`` is documented in ``docs/api.md`` 2.2 but is not
    implemented, because ``ProjectState`` has nowhere to put a message. Adding a
    route that accepted the user's text and silently dropped it would be worse than
    not having the route. See the note in the module's completion report.

Spec: docs/api.md 2.2, 5; docs/architecture.md 3.2; docs/security.md 3.4.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from backend.memory.artifacts import InvalidProjectIdError, ProjectNotFoundError
from backend.schemas.api import (
    CreateProjectRequest,
    ProjectCreatedResponse,
    ProjectStateResponse,
    ProjectSummary,
    ProjectSummaryList,
    StatusResponse,
)
from backend.schemas.state import ProjectState
from backend.services.projects import ProjectBusyError, ProjectService

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def get_project_service(request: Request) -> ProjectService:
    """Resolve the service from application state.

    Composition stays in ``main.py``: this module never constructs a repository, a
    workflow or settings, so a test can mount the router with a stub service.
    """
    service: ProjectService | None = getattr(
        request.app.state, "project_service", None
    )
    if service is None:  # pragma: no cover - a wiring mistake, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project service is not configured.",
        )
    return service


def _load(service: ProjectService, project_id: str) -> ProjectState:
    """Load a project, translating both 'unknown' and 'malformed id' into 404."""
    try:
        return service.get(project_id)
    except (ProjectNotFoundError, InvalidProjectIdError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown project."
        ) from None


@router.post(
    "",
    response_model=ProjectCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    service: ProjectService = Depends(get_project_service),
) -> ProjectCreatedResponse:
    """Create a project and start Requirement Analysis.

    Returns as soon as the project is persisted; the workflow runs in the background
    and progress is reported through status polling or the event stream
    (docs/api.md 2.2).
    """
    state = service.create(
        payload.prompt, title=payload.title, surface_hint=payload.surface_hint
    )
    background_tasks.add_task(service.run_workflow, state.project_id)
    return ProjectCreatedResponse(
        project_id=state.project_id,
        session_id=state.session_id,
        stage=state.current_stage,
        status=state.run_status,
        created_at=state.created_at,
    )


@router.get("", response_model=ProjectSummaryList)
async def list_projects(
    service: ProjectService = Depends(get_project_service),
) -> ProjectSummaryList:
    """Stored projects, newest first."""
    return ProjectSummaryList(
        projects=[
            ProjectSummary(
                project_id=state.project_id,
                title=state.title,
                stage=state.current_stage,
                status=state.run_status,
                updated_at=state.updated_at,
            )
            for state in service.list_summaries()
        ]
    )


@router.get("/{project_id}", response_model=ProjectStateResponse)
async def get_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
) -> ProjectStateResponse:
    """The public projection of project state.

    Built through ``from_state`` so the redaction boundary is applied in one place
    and cannot be bypassed here (docs/api.md 4).
    """
    return ProjectStateResponse.from_state(_load(service, project_id))


@router.get("/{project_id}/status", response_model=StatusResponse)
async def get_project_status(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
) -> StatusResponse:
    """Cheap polling endpoint, and the fallback when the event stream is unavailable.

    ``stage`` and ``status`` are always reported together so the Chatbot page can
    render the pair (docs/api.md 3.2).
    """
    state = _load(service, project_id)
    checkpoint = state.pending_checkpoint
    return StatusResponse(
        project_id=state.project_id,
        stage=state.current_stage,
        status=state.run_status,
        iteration_count=state.iteration_count,
        max_iterations=service.max_iterations,
        awaiting_checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
        preview_available=service.preview_available(state),
        last_error=state.last_error,
        updated_at=state.updated_at,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
) -> None:
    """Delete a project and everything under it.

    Refused with 409 while a run is in flight, so a delete cannot race the graph
    (docs/api.md 2.2).
    """
    try:
        service.delete(project_id)
    except (ProjectNotFoundError, InvalidProjectIdError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown project."
        ) from None
    except ProjectBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None
