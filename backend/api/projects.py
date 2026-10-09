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

AUTHENTICATION AND OWNERSHIP
    Every route below takes ``get_scoped_service``, which verifies the bearer
    token and returns a service restricted to that caller. Consequences:

    - Identity is never read from a path, query or body. The ``project_id`` in
      a path says WHICH project; the token says WHOSE (docs/api.md 1.1).
    - Another user's project answers 404, identically to one that does not
      exist. Under RLS the row is simply not visible, so this needs no special
      case -- and a 403 would confirm the id exists.
    - A missing or invalid token is 401 ``unauthenticated``, in the same error
      envelope as everything else.

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

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response

from backend.api.deps import get_project_service, get_scoped_service
from backend.memory.artifacts import InvalidProjectIdError, ProjectNotFoundError
from backend.schemas.api import (
    CreateProjectRequest,
    ErrorCode,
    HitlDecisionAccepted,
    HitlDecisionRequest,
    HitlHistoryResponse,
    ProjectCreatedResponse,
    ProjectStateResponse,
    PreviewResponse,
    ProjectSummary,
    ProjectSummaryList,
    ReferenceImageResponse,
    StatusResponse,
)
from backend.schemas.state import HitlCheckpoint, ProjectState
from backend.services.projects import (
    CheckpointAlreadyResolvedError,
    NoPendingCheckpointError,
    ProjectBusyError,
    ProjectService,
    ProjectServiceError,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# Resolving the service now goes through backend/api/deps.py, which returns one
# already bound to the verified caller. Re-exported so existing imports of
# `get_project_service` from this module keep working.
__all__ = ["router", "get_project_service"]


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
    service: ProjectService = Depends(get_scoped_service),
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
    service: ProjectService = Depends(get_scoped_service),
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
    service: ProjectService = Depends(get_scoped_service),
) -> ProjectStateResponse:
    """The public projection of project state.

    Built through ``from_state`` so the redaction boundary is applied in one place
    and cannot be bypassed here (docs/api.md 4).
    """
    return ProjectStateResponse.from_state(_load(service, project_id))


@router.get("/{project_id}/status", response_model=StatusResponse)
async def get_project_status(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
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


@router.get("/{project_id}/preview", response_model=PreviewResponse)
async def get_preview(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
) -> PreviewResponse:
    """Where this project's built output can be viewed, and how current it is.

    This is the authoritative answer the Chatbot page asks for before rendering
    the sandboxed iframe (docs/architecture.md 5.5). The bytes themselves are
    served by the separate ``/preview`` router; this endpoint only reports the
    URL and its freshness.

    409 rather than 404 when nothing has been built: the project exists, and
    "not yet" is a different situation from "no such project" — the client
    should keep polling, not give up (docs/api.md 2.5).
    """
    state = _load(service, project_id)
    preview = state.preview_status

    if not service.preview_available(state) or preview.built_at is None:
        # Status alone cannot distinguish this from the other 409s, so the code
        # travels on the exception. main.py's _code_for reads it in preference
        # to the status map, which is what keeps docs/api.md 6 accurate here.
        error = HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No successful build is available to preview yet.",
        )
        error.code = ErrorCode.PREVIEW_NOT_AVAILABLE
        raise error

    return PreviewResponse(
        preview_url=preview.preview_url,
        built_at=preview.built_at,
        status=preview.status,
    )


@router.post(
    "/{project_id}/reference-images",
    response_model=ReferenceImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_reference_image(
    project_id: str,
    file: UploadFile = File(...),
    service: ProjectService = Depends(get_scoped_service),
) -> ReferenceImageResponse:
    """Attach a reference image for the Reference Analysis stage.

    The uploaded filename is read for nothing. It is not stored, not used as a
    path component, and not echoed back; the response carries a server-generated
    id instead (docs/security.md 4).

    ``file.content_type`` is the client's claim and is treated as one: the service
    confirms it against the file's magic bytes and rejects a disagreement.
    """
    _load(service, project_id)
    content = await file.read()
    try:
        ref = service.attach_reference_image(
            project_id, content, file.content_type or ""
        )
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from None
    except UnsupportedMediaTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from None

    return ReferenceImageResponse(
        image_id=ref.image_id,
        content_type=ref.content_type,
        size_bytes=ref.size_bytes,
        sha256=ref.sha256,
        uploaded_at=ref.uploaded_at,
    )


@router.get(
    "/{project_id}/hitl",
    response_model=HitlCheckpoint,
    responses={204: {"description": "No checkpoint is currently pending."}},
)
async def get_pending_checkpoint(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
):
    """The gate the user is being asked to review, or 204 when there is none.

    204 rather than 404: the project exists and "nothing pending" is a normal
    answer for a client that polls this endpoint (docs/api.md 2.3).
    """
    state = _load(service, project_id)
    if state.pending_checkpoint is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return state.pending_checkpoint


@router.post(
    "/{project_id}/hitl",
    response_model=HitlDecisionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_hitl_decision(
    project_id: str,
    payload: HitlDecisionRequest,
    service: ProjectService = Depends(get_scoped_service),
) -> HitlDecisionAccepted:
    """Answer the pending gate and resume the workflow.

    202 rather than 200: the decision is accepted and the graph moves on, but what
    it produces next is not part of this response (docs/api.md 2.3).

    The action/payload consistency of the body is enforced by the request model, so
    a malformed decision is a 422 before it reaches here. The two errors below are
    the ones only the service can detect, and they are distinct on purpose: 409
    means no gate is open, 410 means this gate is no longer the open one.
    """
    _load(service, project_id)
    try:
        return await service.submit_hitl_decision(project_id, payload)
    except NoPendingCheckpointError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None
    except CheckpointAlreadyResolvedError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail=str(exc)
        ) from None
    except ProjectServiceError:
        # The service already logged the cause and sanitized the message; re-raising
        # it verbatim would be the one place a provider or path string could escape.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The workflow could not be resumed.",
        ) from None


@router.get("/{project_id}/hitl/history", response_model=HitlHistoryResponse)
async def get_hitl_history(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
) -> HitlHistoryResponse:
    """Every decision taken on this project, oldest first.

    Append-only, so this is the audit trail: each record carries the id of the gate
    the user actually reviewed (docs/state.md 3).
    """
    state = _load(service, project_id)
    return HitlHistoryResponse(history=state.hitl_history)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
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
