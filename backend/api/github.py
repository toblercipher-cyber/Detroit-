"""GitHub integration routes: status, repositories, selection and push.

PUSH IS THE ONLY WRITE OPERATION
    Pull/import routes existed here and were removed: importing a repository
    is outside this feature's scope. Repository creation, pull-request
    management and issue management were never implemented. The entire
    external-write surface of this product is the two push routes below.

WHY THIS FILE EXISTS
    The GitHub capability needs an HTTP surface the workspace UI can drive.
    Keeping it in its own router rather than in ``projects.py`` matches how
    ``preview.py`` is separated: a reviewer can see the entire external-write
    surface of this product by reading one file.

OWNERSHIP, ON EVERY ROUTE THAT TOUCHES A PROJECT
    Every project route here takes ``get_scoped_service``. The service is bound
    to the verified caller, so a project the caller does not own raises
    ``ProjectNotFoundError`` and becomes a 404 — never a 403, so the endpoint
    cannot be used to discover which project ids exist (``docs/api.md`` 1.1).

    The two server-level routes (status, repositories) take ``get_caller``:
    they expose no project data, but they do consume the server's GitHub
    credential, so they are not anonymous.

THE APPROVAL GATE IS NOT OPTIONAL HERE
    ``/push/preview`` mutates NOTHING. It reads the remote and the workspace,
    describes the difference, and hands back a single-use, short-TTL approval
    token. ``/push`` refuses to run without spending that token
    (``docs/mcp.md`` 5).

    The plan is also bound to its project: a token minted for one project
    cannot execute against another, which ``ExternalActionService.consume``
    enforces by comparing the project id before the token.

WHAT NEVER CROSSES THIS BOUNDARY
    The GitHub credential, in any form. ``GitHubStatusResponse`` carries
    booleans and an account name; no route returns, echoes or logs the token
    (``docs/security.md`` 2, 6).

LAYER
    API (``backend/api/``). Depends on services, schemas and deps; imported by
    ``main.py`` and importing nothing from it.

Spec: docs/mcp.md 2.1, 5, 6; docs/api.md 1.1, 5; docs/security.md 2, 6.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.deps import get_caller, get_scoped_service
from backend.auth.jwt import CallerIdentity
from backend.memory.artifacts import InvalidProjectIdError, ProjectNotFoundError
from backend.schemas.api import (
    ErrorCode,
    ExecuteSyncRequest,
    ExternalActionExecuteRequest,
    ExternalActionRequest,
    GitHubLinkResponse,
    GitHubStatusResponse,
    RepositoryList,
    RepositorySummary,
    SelectRepositoryRequest,
    SyncChange,
    SyncPlanResponse,
    SyncResultResponse,
)
from backend.schemas.state import GitHubLink, McpActionRecord, ProjectState
from backend.services.external_actions import (
    ApprovalExpiredError,
    ApprovalRequiredError,
    CapabilityUnavailableError,
    ExternalActionService,
)
from backend.services.github_gateway import (
    GitHubGateway,
    GitHubUnconfiguredError,
    SyncPlan,
)
from backend.services.projects import ProjectService
from backend.tools.github_rest import GitHubConflictError, GitHubError, RepoRef

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["github"])


# --- Shared plumbing -------------------------------------------------------


def get_gateway(request: Request) -> GitHubGateway:
    """The process-wide gateway, built once at startup.

    503 rather than 501 when it is missing entirely: an absent gateway is a
    wiring fault, while a gateway that exists and reports unconfigured is a
    normal, reportable state the UI knows how to show.
    """
    gateway: GitHubGateway | None = getattr(request.app.state, "github_gateway", None)
    if gateway is None:  # pragma: no cover - wiring mistake, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub integration is not configured on this server.",
        )
    return gateway


def get_external_actions(request: Request) -> ExternalActionService:
    """The approval gate. One instance, because it holds the pending plans."""
    service: ExternalActionService | None = getattr(
        request.app.state, "external_actions", None
    )
    if service is None:  # pragma: no cover - wiring mistake
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The approval service is not configured.",
        )
    return service


def _load(service: ProjectService, project_id: str) -> ProjectState:
    """Load an owned project, or 404. Identical to the projects router's rule."""
    try:
        return service.get(project_id)
    except (ProjectNotFoundError, InvalidProjectIdError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown project."
        ) from None


def _require_link(state: ProjectState) -> GitHubLink:
    if state.github is None:
        error = HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This project is not connected to a repository yet.",
        )
        error.code = ErrorCode.INVALID_STAGE_FOR_ACTION
        raise error
    return state.github


def _unavailable(message: str) -> HTTPException:
    """501: we cannot do it, and we do not substitute something else."""
    error = HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=message)
    error.code = ErrorCode.CAPABILITY_UNAVAILABLE
    return error


def _upstream(message: str) -> HTTPException:
    error = HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)
    error.code = ErrorCode.UPSTREAM_ERROR
    return error


def _conflict(message: str) -> HTTPException:
    error = HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    error.code = ErrorCode.INVALID_STAGE_FOR_ACTION
    return error


def _plan_response(
    plan: SyncPlan, *, plan_id: str, token: str, expires_at: datetime
) -> SyncPlanResponse:
    return SyncPlanResponse(
        direction=plan.direction,
        repository=plan.repository,
        branch=plan.branch,
        base_branch=plan.base_branch,
        added=plan.added,
        updated=plan.updated,
        skipped=plan.skipped,
        changes=[
            SyncChange(
                path=c.path, kind=c.kind, reason=c.reason, size_bytes=c.size_bytes
            )
            for c in plan.changes
        ],
        plan_id=plan_id,
        approval_token=token,
        expires_at=expires_at,
    )


def _record(state: ProjectState, action_type: str, ok: bool) -> None:
    """Append to ``mcp_action_history``: what was attempted, and whether it worked.

    ``docs/mcp.md`` 6 requires every external action to be logged with its type
    and outcome. The record carries no credential and no repository content.
    """
    state.mcp_action_history.append(
        McpActionRecord(
            action_id=f"act_{len(state.mcp_action_history) + 1:04d}",
            action_type=action_type,
            status="succeeded" if ok else "failed",
        )
    )


# --- Server-level status ---------------------------------------------------


@router.get("/github/status", response_model=GitHubStatusResponse)
async def github_status(
    _caller: CallerIdentity = Depends(get_caller),
    gateway: GitHubGateway = Depends(get_gateway),
) -> GitHubStatusResponse:
    """Whether GitHub is usable, verified by a real call rather than assumed.

    Authenticated because it spends the server's credential, even though it
    returns no project data and no part of that credential.
    """
    result = await gateway.status()
    return GitHubStatusResponse(
        configured=result.configured,
        mode=result.mode,
        reason=result.reason,
        mcp_server_configured=result.mcp_server_configured,
        mcp_tools_discovered=result.mcp_tools_discovered,
        account=result.account,
    )


@router.get("/github/repositories", response_model=RepositoryList)
async def list_repositories(
    _caller: CallerIdentity = Depends(get_caller),
    gateway: GitHubGateway = Depends(get_gateway),
) -> RepositoryList:
    """Repositories the server's credential can see."""
    try:
        repos = await gateway.list_repositories()
    except GitHubUnconfiguredError as exc:
        raise _unavailable(str(exc)) from None
    except GitHubError as exc:
        raise _upstream(str(exc)) from None
    return RepositoryList(
        repositories=[
            RepositorySummary(
                full_name=r.full_name,
                default_branch=r.default_branch,
                private=r.private,
                can_push=r.permissions_push,
                html_url=r.html_url,
            )
            for r in repos
        ]
    )


# --- Per-project repository selection --------------------------------------


@router.get("/projects/{project_id}/github", response_model=GitHubLinkResponse)
async def get_link(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
) -> GitHubLinkResponse:
    state = _load(service, project_id)
    link = state.github
    if link is None:
        return GitHubLinkResponse(connected=False)
    return GitHubLinkResponse(
        connected=True,
        repository=link.repository,
        default_branch=link.default_branch,
        branch=link.branch,
        last_pushed_at=link.last_pushed_at,
        last_commit_sha=link.last_commit_sha,
    )


@router.put("/projects/{project_id}/github", response_model=GitHubLinkResponse)
async def select_repository(
    project_id: str,
    payload: SelectRepositoryRequest,
    service: ProjectService = Depends(get_scoped_service),
    gateway: GitHubGateway = Depends(get_gateway),
) -> GitHubLinkResponse:
    """Connect this project to a repository.

    The repository is CONFIRMED against GitHub before it is stored: a name
    that does not exist, or that the credential cannot see, is rejected here
    rather than becoming a stored selection that fails at every later push.

    Push permission is required at selection time. Connecting a read-only
    repository would let a user configure something that can never push, and
    find out only after preparing a plan.
    """
    state = _load(service, project_id)
    try:
        ref = RepoRef.parse(payload.repository)
        repo = await gateway.get_repository(ref.full_name)
    except GitHubUnconfiguredError as exc:
        raise _unavailable(str(exc)) from None
    except GitHubError as exc:
        raise _conflict(str(exc)) from None

    if not repo.permissions_push:
        raise _conflict(
            f"The configured credential cannot push to {repo.full_name}."
        )

    state.github = GitHubLink(
        repository=repo.full_name,
        default_branch=repo.default_branch,
        branch=(payload.branch or "detroit/generated").strip(),
    )
    state.updated_at = datetime.now(timezone.utc)
    service.save(state)
    logger.info(
        "github repository connected project_id=%s repo=%s", project_id, repo.full_name
    )
    return GitHubLinkResponse(
        connected=True,
        repository=state.github.repository,
        default_branch=state.github.default_branch,
        branch=state.github.branch,
    )


@router.delete("/projects/{project_id}/github", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_repository(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
) -> None:
    """Forget the selection. Touches nothing on GitHub."""
    state = _load(service, project_id)
    state.github = None
    state.updated_at = datetime.now(timezone.utc)
    service.save(state)


# --- Push ------------------------------------------------------------------


@router.post("/projects/{project_id}/github/push/preview", response_model=SyncPlanResponse)
async def preview_push(
    project_id: str,
    service: ProjectService = Depends(get_scoped_service),
    gateway: GitHubGateway = Depends(get_gateway),
    approvals: ExternalActionService = Depends(get_external_actions),
) -> SyncPlanResponse:
    """Describe what pushing would change, and issue the approval for it.

    Mutates nothing on GitHub and nothing in the workspace.
    """
    state = _load(service, project_id)
    link = _require_link(state)
    workspace = service.workspace_dir(project_id)

    try:
        plan = await gateway.plan_push(
            workspace,
            repository=link.repository,
            branch=link.branch,
            base_branch=link.default_branch,
        )
    except GitHubUnconfiguredError as exc:
        raise _unavailable(str(exc)) from None
    except GitHubError as exc:
        raise _conflict(str(exc)) from None

    try:
        approval = approvals.plan(
            project_id,
            ExternalActionRequest(
                action_type="github_push",
                params={
                    "repository": link.repository,
                    "branch": link.branch,
                    "base": link.default_branch,
                    # Carried so execution uses the sha the user reviewed.
                    "head_sha": plan.head_sha or "",
                },
            ),
        )
    except CapabilityUnavailableError as exc:
        raise _unavailable(str(exc)) from None

    return _plan_response(
        plan,
        plan_id=approval.plan_id,
        token=approval.approval_token,
        expires_at=approval.expires_at,
    )


@router.post("/projects/{project_id}/github/push", response_model=SyncResultResponse)
async def execute_push(
    project_id: str,
    payload: ExecuteSyncRequest,
    service: ProjectService = Depends(get_scoped_service),
    gateway: GitHubGateway = Depends(get_gateway),
    approvals: ExternalActionService = Depends(get_external_actions),
) -> SyncResultResponse:
    """Push, only against a valid approval minted for THIS project."""
    state = _load(service, project_id)
    link = _require_link(state)
    workspace = service.workspace_dir(project_id)

    plan = _spend(approvals, project_id, payload)
    expected = str(plan.params.get("head_sha") or "") or None

    message = (payload.commit_message or "").strip() or (
        f"DETROIT: {state.title or 'generated site'}"
    )
    try:
        result = await gateway.execute_push(
            workspace,
            repository=link.repository,
            branch=link.branch,
            base_branch=link.default_branch,
            commit_message=message,
            expected_head_sha=expected,
        )
    except GitHubConflictError as exc:
        _record(state, "github_push", False)
        service.save(state)
        raise _conflict(str(exc)) from None
    except GitHubUnconfiguredError as exc:
        raise _unavailable(str(exc)) from None
    except GitHubError as exc:
        _record(state, "github_push", False)
        service.save(state)
        raise _upstream(str(exc)) from None

    link.last_pushed_at = datetime.now(timezone.utc)
    link.last_commit_sha = result.commit_sha
    _record(state, "github_push", True)
    state.updated_at = datetime.now(timezone.utc)
    service.save(state)

    return SyncResultResponse(
        direction="push",
        repository=result.repository,
        branch=result.branch,
        files_written=result.files_written,
        commit_sha=result.commit_sha,
        detail=result.detail,
    )


def _spend(
    approvals: ExternalActionService, project_id: str, payload: ExecuteSyncRequest
):
    """Consume the approval, mapping its refusals to the documented codes.

    Runs BEFORE any remote call, so an unapproved request never reaches
    GitHub. 403 for "no valid approval", 410 for "expired or already used" —
    the distinction matters, because only the second is worth retrying with a
    fresh preview (``docs/api.md`` 5).
    """
    try:
        return approvals.consume(
            project_id,
            ExternalActionExecuteRequest(
                plan_id=payload.plan_id, approval_token=payload.approval_token
            ),
        )
    except ApprovalRequiredError as exc:
        error = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
        error.code = ErrorCode.APPROVAL_REQUIRED
        raise error from None
    except ApprovalExpiredError as exc:
        error = HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))
        error.code = ErrorCode.APPROVAL_EXPIRED
        raise error from None
