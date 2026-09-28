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
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.agents.base import model_kwargs_for
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.api import projects as projects_routes
from backend.config.settings import Settings, get_settings
from backend.context.builder import ContextBuilder
from backend.graph.workflow import WorkflowDependencies, build_workflow
from backend.memory.artifacts import JsonArtifactRepository
from backend.schemas.api import ErrorCode, ErrorResponse, HealthResponse
from backend.schemas.enums import AgentRole
from backend.schemas.state import ErrorBody
from backend.services.projects import ProjectService

# Version of the builder backend itself, reported by /health. Unrelated to the
# version of any project it generates.
APP_VERSION = "0.1.0"

API_PREFIX = "/api/v1"

logger = logging.getLogger("backend")

# Maps the HTTP status a route raises to a stable machine-readable code. Routes that
# need a more specific code raise it explicitly rather than relying on this fallback.
_STATUS_TO_CODE: dict[int, ErrorCode] = {
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


def build_project_service(settings: Settings) -> ProjectService:
    """Assemble the object graph for the project workflow.

    This is the only place that knows how the layers fit together. Each collaborator
    is constructed here and injected downward, so no module reaches for a global and
    a test can substitute any piece (CLAUDE.md 29).

    The chat model is constructed from the central per-role policy; an agent never
    assembles its own parameters and never names a model (docs/runtime.md 4.1).
    """
    # Lazy import: the provider SDK is only needed when a real service is built, so
    # importing this module does not require it.
    from langchain_anthropic import ChatAnthropic

    repository = JsonArtifactRepository(settings.builder_data_dir)

    analyzer_model = ChatAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        **model_kwargs_for(
            AgentRole.REQUIREMENT_ANALYZER,
            default_model=settings.anthropic_model,
            heavy_model=settings.anthropic_model_heavy,
        ),
    )

    workflow = build_workflow(
        WorkflowDependencies(
            context_builder=ContextBuilder(),
            requirement_analyzer=RequirementAnalyzerAgent(analyzer_model),
        ),
        # Not chosen here: the interim checkpointer is an open decision
        # (docs/state.md 5.1). build_workflow warns that runs are not durable.
        checkpointer=None,
    )

    return ProjectService(repository=repository, workflow=workflow, settings=settings)


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

    # Routes resolve the service from application state, so composition stays here
    # rather than leaking into the route modules.
    app.state.project_service = build_project_service(settings)
    logger.info("project service ready")

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

    # Explicit origin allowlist. No credentials: there is no authentication in v1, so
    # nothing should be sending cookies cross-origin (docs/api.md 1.1).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
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
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
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

    return app


app = create_app()
