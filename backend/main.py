"""FastAPI application entry point for the builder backend.

WHY THIS FILE EXISTS
    It creates the HTTP application and wires it together: configuration, logging,
    CORS, the error envelope, and the routers. It is the composition root — the one
    place that knows how the pieces fit.

    Business logic does NOT belong here. Agent reasoning, orchestration and tool
    access live in service and graph modules; route handlers validate, delegate and
    return a typed response (docs/architecture.md 3.2, skills.md 8).

LAYER
    Application entry point. It may import from any layer; no layer imports it.

WHAT IT OWNS
    - startup/shutdown: fail-fast configuration validation, data-directory checks,
      and surfacing security warnings
    - the error envelope: every non-2xx response is an ``ErrorResponse``, and no
      response ever carries a stack trace, a secret, a submitted value or an
      absolute path
    - the ``/api/v1/health`` liveness route (kept here while it is the only route;
      it moves to ``backend/api/`` once real routers exist)

WHAT IT DOES NOT OWN
    Workflow routing. Selecting and transitioning between agents/nodes is the
    LangGraph Agent/Workflow Router's job, not an HTTP concern (CLAUDE.md 3A).

Run: ``uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000`` from the
repository root (docs/runtime.md 7).

Spec: docs/api.md 1, 1.1, 2.1, 5; docs/architecture.md 3.2, 4; docs/security.md 6,
      9, 10; docs/runtime.md 6, 7.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import InMemorySaver
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.agents.base import model_kwargs_for
from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent
from backend.agents.builder import BuilderAgent
from backend.agents.code_reviewer import CodeReviewerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.fixer import FixerAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.agents.planner import PlannerAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.visual_ux_critic import VisualUxCriticAgent
from backend.api import preview as preview_routes
from backend.api import projects as projects_routes
from backend.auth.jwt import SupabaseTokenVerifier
from backend.config.settings import ConfigurationError, Settings, get_settings
from backend.context.builder import ContextBuilder
from backend.db import engine as db_engine
from backend.graph.workflow import WorkflowDependencies, build_workflow
from backend.memory.artifacts import JsonArtifactRepository
from backend.memory.postgres import PostgresStateRepository
from backend.tools.build_runner import BuildRunner
from backend.tools.impeccable import probe_impeccable, run_detector
from backend.tools.preview_capture import capture_preview
from backend.tools.workspace import WorkspaceFiles
from backend.schemas.api import ErrorCode, ErrorResponse, HealthResponse
from backend.schemas.enums import AgentRole
from backend.schemas.state import ErrorBody
from backend.services.projects import ProjectService

# Version of the builder backend itself, reported by /health. Unrelated to the
# version of any project it generates.
APP_VERSION = "0.1.0"

API_PREFIX = "/api/v1"

logger = logging.getLogger("backend")

# Must run BEFORE anything creates an event loop, which is why it is at module
# scope in the composition root rather than inside lifespan: by the time
# lifespan runs, uvicorn has already built the loop and the policy no longer
# applies to it. On Windows that loop is a ProactorEventLoop, which async
# psycopg refuses to use, so the checkpointer would fail on its first query
# (docs/state.md 5.0.2). A no-op everywhere else.
db_engine.configure_event_loop_policy()

# Maps the HTTP status a route raises to a stable machine-readable code. Routes that
# need a more specific code raise it explicitly rather than relying on this fallback.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.APPROVAL_REQUIRED,
    404: ErrorCode.PROJECT_NOT_FOUND,
    409: ErrorCode.INVALID_STAGE_FOR_ACTION,
    410: ErrorCode.CHECKPOINT_ALREADY_RESOLVED,
    413: ErrorCode.UPLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    501: ErrorCode.CAPABILITY_UNAVAILABLE,
    502: ErrorCode.UPSTREAM_ERROR,
    503: ErrorCode.QUALITY_GATE_BLOCKED,
}


def _code_for(exc: BaseException, status_code: int) -> ErrorCode:
    """The machine-readable code for an error, preferring the one it carries.

    Status alone is ambiguous: 409 is both "no gate is open" and "not valid in this
    stage", and ``docs/api.md`` 6 gives those different codes. The service errors
    already know which they are, so their ``code`` wins and the status map is only
    the fallback.

    The originating error is found through ``__context__`` because a route raises
    ``HTTPException(...) from None``. That suppresses the *display* of the cause but
    still attaches the original exception, so the code survives without the route
    having to restate it — and without this function guessing from a message string.

    Only a real ``ErrorCode`` is honoured, so an arbitrary exception carrying some
    other ``code`` attribute cannot inject a value into the public envelope.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = getattr(current, "code", None)
        if isinstance(candidate, ErrorCode):
            return candidate
        current = current.__cause__ or current.__context__
    return _STATUS_TO_CODE.get(status_code, ErrorCode.INTERNAL_ERROR)


def _request_id(request: Request) -> str:
    """The correlation id for this request, assigned by the middleware."""
    return getattr(request.state, "request_id", "unknown")


def _error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    request_id: str,
    details: dict | None = None,
) -> JSONResponse:
    """Render the single error envelope used by every non-2xx response."""
    body = ErrorResponse(
        error=ErrorBody(
            code=code.value, message=message, details=details, request_id=request_id
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _configure_logging(settings: Settings) -> None:
    """Set the root log level once, from configuration.

    Deliberately minimal: this is not a logging framework. Structured
    stage/role/action logging belongs with the components that emit it
    (docs/security.md 10).
    """
    logging.basicConfig(
        level=settings.log_level.value,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )


def build_project_service(
    settings: Settings, checkpointer: Any, repository: Any
) -> ProjectService:
    """Assemble the object graph for the project workflow.

    This is the only place that knows how the layers fit together. Each collaborator
    is constructed here and injected downward, so no module reaches for a global and
    a test can substitute any piece (CLAUDE.md 29).

    The chat model is constructed from the central per-role policy; an agent never
    assembles its own parameters and never names a model (docs/runtime.md 4.1). Each
    role gets its own client because the policy differs per role — the Design
    Director runs at a higher temperature than the Requirement Analyzer, and sharing
    one client would silently flatten that.
    """
    # Lazy import: the provider SDK is only needed when a real service is built, so
    # importing this module does not require it.
    from langchain_anthropic import ChatAnthropic

    # The GRAPH gets the filesystem repository: the only thing a node needs
    # from it is reference-image BYTES, which live on disk either way
    # (docs/database.md 4). The owner-scoped store is the SERVICE's, so a
    # node can never perform an unscoped project read.
    artifacts = JsonArtifactRepository(settings.builder_data_dir)

    def model_for(role: AgentRole) -> ChatAnthropic:
        """A chat client configured by the central policy for one role."""
        return ChatAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            **model_kwargs_for(
                role,
                default_model=settings.anthropic_model,
                heavy_model=settings.anthropic_model_heavy,
            ),
        )

    workflow = build_workflow(
        WorkflowDependencies(
            context_builder=ContextBuilder(),
            requirement_analyzer=RequirementAnalyzerAgent(
                model_for(AgentRole.REQUIREMENT_ANALYZER)
            ),
            planner=PlannerAgent(model_for(AgentRole.PLANNER)),
            design_director=DesignDirectorAgent(
                model_for(AgentRole.DESIGN_DIRECTOR)
            ),
            reference_analyzer=ReferenceAnalyzerAgent(
                model_for(AgentRole.REFERENCE_ANALYZER)
            ),
            frontend_architect=FrontendArchitectAgent(
                model_for(AgentRole.FRONTEND_ARCHITECT)
            ),
            # A factory: the Builder is bound to one project workspace, so the
            # graph constructs it per run. The model is still built here, from
            # the same central policy as every other role.
            build_builder=lambda workspace: BuilderAgent(
                model_for(AgentRole.BUILDER), workspace
            ),
            # Per-workspace, for the same reason as the Builder.
            build_fixer=lambda workspace: FixerAgent(
                model_for(AgentRole.FIXER), workspace
            ),
            build_runner=BuildRunner(
                timeout_seconds=settings.preview_build_timeout_seconds
            ),
            visual_ux_critic=VisualUxCriticAgent(
                model_for(AgentRole.VISUAL_UX_CRITIC)
            ),
            anti_slop_reviewer=AntiSlopReviewerAgent(
                model_for(AgentRole.ANTI_SLOP_REVIEWER)
            ),
            accessibility_reviewer=AccessibilityReviewerAgent(
                model_for(AgentRole.ACCESSIBILITY_REVIEWER)
            ),
            code_reviewer=CodeReviewerAgent(
                model_for(AgentRole.CODE_REVIEWER)
            ),
            # Probed on every evaluation. Availability is a property of the
            # installed CLI, not of this repository (docs/design.md 5.2).
            impeccable_probe=probe_impeccable,
            # Configuration owns the correction budget, not the graph
            # (docs/workflow.md 4).
            max_auto_iterations=settings.max_auto_iterations,
            # The real detector: `impeccable detect --json <dist>`. It raises
            # rather than returning [] on a failed scan, and the node turns that
            # into a BLOCKED gate (docs/design.md 5.3).
            impeccable_detector=run_detector,
            # Renders the build in headless Chromium and captures the DOM and
            # screenshots the reviewers are granted. Raises rather than
            # returning a partial payload, and the node turns that into a
            # BLOCKED gate — a half-captured preview reviewed as if it were
            # whole is worse than none (docs/agents.md 4.6-4.9).
            preview_capture=capture_preview,
            # The same repository the service uses. Reference Analysis needs the
            # image bytes, which state does not carry; sharing one instance keeps
            # a single view of what is on disk.
            repository=artifacts,
        ),
        # Injected, not constructed. A checkpointer is not optional, and the way
        # it fails without one is worth knowing: the first gate still pauses and
        # still returns __interrupt__, so a run LOOKS fine — but answering it
        # raises ``RuntimeError: Cannot use Command(resume=...) without
        # checkpointer``. The gate is reachable and unanswerable.
        #
        # Which saver arrives is decided by ``_open_checkpointer`` below, because
        # choosing it needs an open event loop and a connection whose lifetime
        # must end at shutdown. That was always the point of injecting it here.
        checkpointer=checkpointer,
    )

    return ProjectService(
        repository=repository, workflow=workflow, settings=settings
    )


async def _open_checkpointer(stack: AsyncExitStack, settings: Settings) -> Any:
    """The durable checkpointer, or the interim one when no database is set.

    Two outcomes, and the difference between them is deliberate:

    - **No ``DATABASE_URL``.** Fall back to ``InMemorySaver`` and warn. Nothing
      is misconfigured; the operator simply has not connected a database, and
      refusing to start would make the backend unusable for anyone who just
      wants to run the frontend against it. ``settings.startup_warnings()``
      has already said what that costs.

    - **``DATABASE_URL`` set but unreachable.** Raise, and let startup fail.
      Setting it is an explicit statement that runs should be durable; quietly
      degrading to process memory would mean gates silently stop surviving a
      restart and nobody finds out until one is lost. That is the same
      fail-fast posture ``get_settings()`` already takes on bad configuration.

    The pool is entered on ``stack``, so its lifetime is exactly the
    application's and it is closed on shutdown — connections left open are
    counted against the Supabase project's limit.
    """
    if not settings.is_database_configured:
        logger.warning(
            "no DATABASE_URL: using the in-process checkpointer. Pending HITL "
            "gates will NOT survive a restart (docs/state.md 5.1)."
        )
        return InMemorySaver()

    checkpointer = await stack.enter_async_context(
        db_engine.checkpointer_lifespan(settings)
    )
    logger.info(
        "durable checkpointer ready: PostgreSQL, schema=%s",
        db_engine.CHECKPOINT_SCHEMA,
    )
    return checkpointer


def _open_repository(stack: AsyncExitStack, settings: Settings) -> Any:
    """The owner-scoped PostgreSQL store, or the single-user dev store.

    The two are NOT interchangeable and the difference is stated loudly,
    because a filesystem store silently serving a multi-user deployment is the
    exact failure this whole change exists to prevent:

    - **``DATABASE_URL_APP`` set.** Use PostgreSQL as ``detroit_app``
      (NOBYPASSRLS). Rows are owner-scoped and RLS enforces it.
    - **Not set.** Fall back to the filesystem and warn that there is NO
      isolation. Acceptable only for single-user local development.

    The pool is closed on shutdown via ``stack``.
    """
    artifacts = JsonArtifactRepository(settings.builder_data_dir)
    if not settings.is_app_role_configured:
        logger.warning(
            "DATABASE_URL_APP is not set: project state is on the FILESYSTEM "
            "with NO per-user isolation. Single-user development only."
        )
        return artifacts

    pool = db_engine.create_app_pool(settings.database_url_app.get_secret_value())
    pool.open(wait=True, timeout=db_engine.POOL_TIMEOUT_SECONDS)
    stack.callback(pool.close)
    logger.info("owner-scoped PostgreSQL repository ready (role: detroit_app)")
    return PostgresStateRepository(pool, artifacts)


def _build_verifier(settings: Settings) -> SupabaseTokenVerifier | None:
    """The token verifier, or None when Supabase Auth is not configured.

    Refusing to start is correct when the combination is unsafe rather than
    merely incomplete: an owner-scoped database with no way to verify who the
    owner is would mean every request fails at the dependency, which is a
    confusing way to discover a missing variable.
    """
    if not settings.supabase_url:
        if settings.is_app_role_configured:
            raise ConfigurationError(
                "DATABASE_URL_APP is set but SUPABASE_URL is not. Owner-scoped "
                "storage cannot work without token verification. Set "
                "SUPABASE_URL, or unset DATABASE_URL_APP for single-user mode."
            )
        logger.warning(
            "SUPABASE_URL is not set: authentication is DISABLED and every "
            "project route will answer 503."
        )
        return None
    return SupabaseTokenVerifier(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration and prepare the runtime before serving traffic.

    Configuration problems raise here, so the process refuses to start rather than
    failing later on a request. ``ConfigurationError`` names only variable names, never
    values (docs/runtime.md 6).
    """
    settings = get_settings()
    _configure_logging(settings)

    data_dir = settings.ensure_data_dir()
    logger.info(
        "backend starting: version=%s env=%s data_dir_ready=%s",
        APP_VERSION,
        settings.app_env.value,
        data_dir.is_dir(),
    )

    # Security posture is surfaced loudly rather than assumed (docs/security.md 9).
    for warning in settings.startup_warnings():
        logger.warning("%s", warning)

    # One stack owns every async resource opened for the application's
    # lifetime, so shutdown unwinds them in reverse order even if startup
    # fails partway through.
    async with AsyncExitStack() as stack:
        checkpointer = await _open_checkpointer(stack, settings)
        repository = _open_repository(stack, settings)
        app.state.token_verifier = _build_verifier(settings)

        # Routes resolve the service from application state, so composition
        # stays here rather than leaking into the route modules.
        app.state.project_service = build_project_service(
            settings, checkpointer, repository
        )
        logger.info(
            "project service ready (storage=%s, auth=%s)",
            "postgres" if settings.is_app_role_configured else "filesystem",
            "on" if app.state.token_verifier else "OFF",
        )

        yield

    logger.info("backend stopped")


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can construct an app with
    its own configuration instead of inheriting process state.
    """
    settings = get_settings()

    app = FastAPI(
        title="AI Website Builder — Builder Backend",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # Explicit origin allowlist.
    #
    # allow_credentials stays FALSE and that is correct even with auth: the
    # token travels in an Authorization header, not a cookie, so no credentialed
    # CORS mode is needed. Turning it on would also forbid a wildcard origin and
    # widen what a browser will attach to cross-origin requests, for no gain.
    #
    # Authorization MUST be listed. A browser preflights any request carrying
    # it, and an un-allowed header makes that preflight fail with 400 -- which
    # surfaces in the frontend as "could not reach the backend", pointing at the
    # network rather than at this list (docs/api.md 1.1).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def assign_request_id(
        request: Request, call_next: Callable[[Request], Awaitable]
    ):
        """Attach a correlation id so a safe error response can be tied to the full
        server-side detail in the log (docs/api.md 5, docs/security.md 10)."""
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422 with the offending field locations only.

        FastAPI's own error list includes the submitted ``input`` value. That is
        stripped here: a rejected request body could contain anything a client sent,
        and echoing it back is exactly the leak docs/security.md 6 forbids.
        """
        safe = [
            {"loc": [str(part) for part in err.get("loc", ())], "type": err.get("type")}
            for err in exc.errors()
        ]
        return _error_response(
            422,
            ErrorCode.VALIDATION_ERROR,
            "Request validation failed.",
            _request_id(request),
            details={"errors": safe},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Wrap ordinary HTTP errors in the one documented envelope."""
        code = _code_for(exc, exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(
            exc.status_code, code, message, _request_id(request)
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """500 with no detail at all.

        The traceback is logged server-side and correlated by ``request_id``; the
        client is told nothing beyond that something failed, because an unexpected
        exception can carry paths, queries or provider payloads.
        """
        request_id = _request_id(request)
        logger.exception("unhandled error request_id=%s", request_id)
        return _error_response(
            500,
            ErrorCode.INTERNAL_ERROR,
            "An internal error occurred.",
            request_id,
        )

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        """Liveness plus a boolean-only credential check.

        ``model_configured`` is derived from the validated key but reports only
        whether one is present — never the key, never a prefix of it
        (docs/api.md 2.1, docs/runtime.md 3.4).
        """
        current = get_settings()
        return HealthResponse(
            status="ok",
            version=APP_VERSION,
            model_configured=bool(current.anthropic_api_key.get_secret_value()),
        )

    # Routers are registered here as they are built. Each one is thin and delegates
    # to a service; the remaining routers — HITL, reference images, build/preview,
    # evaluation, external actions, SSE events — are added as their services land.
    app.include_router(projects_routes.router)
    # Static preview output. Deliberately outside /api/v1: it serves files a
    # model caused to exist, not typed JSON (docs/architecture.md 5.4).
    app.include_router(preview_routes.router)

    return app


app = create_app()
