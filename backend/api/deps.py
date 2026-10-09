"""Shared FastAPI dependencies: caller identity and the scoped service.

WHY THIS FILE EXISTS
    Every protected route needs the same two things — who is calling, and a
    service restricted to them. Written per route that is eight chances to get
    it subtly different; written once it is a decorator-level fact that a
    reviewer can check by reading the signature.

LAYER
    API (``backend/api/``). Depends on auth, services and schemas. It is
    imported by route modules and imports nothing from them.

---------------------------------------------------------------------------
THE RULE THIS FILE ENFORCES

    Identity comes from a verified bearer token and from nowhere else.

Not from a path parameter, not from a query string, not from a body field. A
client that can name its own user id can claim anyone's project
(``docs/api.md`` 1.1, ``docs/database.md`` 1.3.5). There is deliberately no
function here that reads a user id from a request.

HOW A ROUTE USES IT

    @router.get("/{project_id}")
    async def get_project(
        project_id: str,
        service: ProjectService = Depends(get_scoped_service),
    ): ...

``get_scoped_service`` verifies the token and returns a service already bound
to that caller. A route that takes it cannot operate on another user's data;
a route that forgets it does not compile into a working endpoint, because the
unbound service raises ``TypeError`` on its first repository call.

Spec: docs/api.md 1.1, 5; docs/database.md 1.3.5; docs/security.md 6.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.jwt import AuthError, CallerIdentity, SupabaseTokenVerifier
from backend.schemas.api import ErrorCode
from backend.services.projects import ProjectService

logger = logging.getLogger(__name__)

# auto_error=False so a MISSING header reaches our handler instead of FastAPI's
# default 403. The contract says 401 for "no usable token", and the envelope
# has to be ours (docs/api.md 5).
_bearer = HTTPBearer(auto_error=False, scheme_name="SupabaseAccessToken")


def _unauthenticated(message: str) -> HTTPException:
    """401 in the project's one error envelope.

    The code travels on the exception so ``main.py``'s ``_code_for`` uses it in
    preference to the status map — the same mechanism the 409s already rely on.

    WWW-Authenticate is set because 401 without it is an incomplete response,
    and some clients treat its absence as a server bug rather than a challenge.
    """
    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )
    error.code = ErrorCode.UNAUTHENTICATED
    return error


def get_verifier(request: Request) -> SupabaseTokenVerifier:
    """Resolve the process-wide verifier from application state.

    It is built once at startup because it caches the JWKS; a per-request
    instance would fetch signing keys on every call.

    A 503 here means the application started without auth configured. That is
    a deployment fault, not a client fault, so it is not a 401 — telling a
    caller "unauthenticated" when the server simply cannot check anything
    would be misleading.
    """
    verifier: SupabaseTokenVerifier | None = getattr(
        request.app.state, "token_verifier", None
    )
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured on this server.",
        )
    return verifier


def get_caller(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    verifier: SupabaseTokenVerifier = Depends(get_verifier),
) -> CallerIdentity:
    """The verified caller, or 401.

    Every failure — absent header, wrong scheme, bad signature, expired,
    wrong issuer or audience — produces 401 with the verifier's own safe
    message. None of them says which part failed beyond what the caller can
    already tell, because that detail only helps someone probing.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthenticated("An access token is required.")
    if credentials.scheme.lower() != "bearer":
        raise _unauthenticated("The Authorization header must use the Bearer scheme.")
    try:
        return verifier.verify(credentials.credentials)
    except AuthError as exc:
        # Logged without the token. A rejected credential is still a
        # credential (docs/security.md 6).
        logger.info("rejected access token: %s", exc)
        raise _unauthenticated(str(exc)) from None


def get_project_service(request: Request) -> ProjectService:
    """The unbound service. For routes that touch no project data."""
    service: ProjectService | None = getattr(
        request.app.state, "project_service", None
    )
    if service is None:  # pragma: no cover - a wiring mistake, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project service is not configured.",
        )
    return service


def get_scoped_service(
    caller: CallerIdentity = Depends(get_caller),
    service: ProjectService = Depends(get_project_service),
) -> ProjectService:
    """A service that can only reach the verified caller's projects.

    This is the dependency project routes should take. The owner id comes from
    the token; nothing a client sends can influence which rows are reachable.
    """
    return service.for_owner(caller.user_id)
