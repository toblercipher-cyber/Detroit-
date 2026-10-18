"""Serves the built static output of a generated project.

WHY THIS FILE EXISTS
    The Build stage produces ``workspace/dist/``. Something has to hand those
    bytes to a browser so the user — and the Visual & UX Critic — can look at the
    result. This is that, and nothing more.

WHY IT IS NOT UNDER /api/v1
    ``docs/architecture.md`` 5.4 puts preview at ``/preview/{project_id}/...``.
    The API returns typed JSON envelopes; this returns arbitrary files a model
    caused to exist. Keeping them on separate paths means no error-envelope or
    content-type rule has to make exceptions, and the split is visible in the URL.

WHY A STATIC BUILD AND NOT A DEV SERVER
    v1 serves ``dist/`` (``docs/architecture.md`` 5.4). A per-project dev server
    would be N long-lived processes running model-influenced plugin pipelines and
    holding ports. A one-shot build plus static files is a much smaller surface
    and is enough to review and iterate.

WHAT THIS IS DEFENDING AGAINST
    Two different problems, and they need different controls.

    First, WHO may read a project's output. Until this route required a token,
    the answer was "anyone who knows a project id", verified as a real
    cross-user leak. Now a signed five-minute capability token stands in the
    path where the project id used to be, and ownership is re-checked against
    current state on every request (see ``serve_preview``).

    Note the degenerate case: in single-user development there is no
    application database, ``for_owner`` has nobody to scope to, and the
    ownership re-check reduces to "this project exists". That is correct when
    there is exactly one user, and it is why ``main.py`` refuses to start in
    multi-user mode without a configured signing key.

    Second, WHAT the bytes may do once served. Every one of them originated
    from a model, so three further controls apply:

    - **Containment.** The requested path is resolved and confirmed to sit inside
      this project's ``dist/`` before anything is opened. Traversal returns 404,
      never the file and never a different error that would confirm the path
      exists (``docs/security.md`` 3.4).
    - **No sniffing.** Content types come from a closed extension table and are
      sent with ``X-Content-Type-Options: nosniff``, so a file cannot be coaxed
      into being interpreted as something other than what it is.
    - **A restrictive CSP.** The page may load its own bundle and nothing else:
      no third-party script, no outbound connection, no plugin. Generated code
      that tried to phone home would be blocked by the browser.

LAYER
    API (``backend/api/``). Depends on the service for path resolution; it owns
    no state and runs no workflow.

Spec: docs/architecture.md 5.4; docs/api.md 2.5; docs/security.md 3.4, 4.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from backend.api.deps import get_preview_secret
from backend.auth import preview_token
from backend.auth.preview_token import PreviewTokenError
from backend.memory.artifacts import InvalidProjectIdError, ProjectNotFoundError
from backend.services.projects import ProjectService

logger = logging.getLogger(__name__)

# Deliberately outside /api/v1: this is not the typed API (docs/architecture.md 5.4).
router = APIRouter(prefix="/preview", tags=["preview"])

PREVIEW_DIR = "dist"
INDEX_FILE = "index.html"

# Content types for what a Vite build actually emits. A closed table rather than
# mimetypes.guess_type(): that consults the machine's registry, so the type served
# would depend on the host, and an unknown extension would be guessed at rather
# than refused.
#
# Anything not listed is not served. That is the point — a generated project has
# no reason to emit a .exe, and if it does, the answer is 404.
_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=utf-8",
}

# The generated site may load its own bundle and nothing else.
#
# 'unsafe-inline' is granted for styles only, because Vite inlines small critical
# CSS and the alternative is a per-build nonce this static server cannot mint.
# Scripts get no such allowance: script-src 'self' means an injected inline
# <script> — the realistic outcome of a prompt-injection attempt landing in
# generated markup — does not execute.
#
# connect-src 'none' stops a generated page exfiltrating anything it was told to.
_CSP_DIRECTIVES = (
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'none'",
    "form-action 'none'",
    "base-uri 'none'",
)


def _csp(allowed_origins: Sequence[str]) -> str:
    """The preview CSP, with ``frame-ancestors`` built from the CORS allowlist.

    WHY THIS IS NOT A CONSTANT ANY MORE
        It used to end ``frame-ancestors 'self'``, and the comment claimed that
        let the Chatbot page embed the preview. It did not. ``'self'`` is the
        origin serving THIS response -- the backend on :8000 -- while the app
        runs on :5173, so the browser blocked the only embed anyone wanted.

        The frontend origins are already enumerated for CORS, and they are the
        same origins entitled to frame a preview, so that list is reused rather
        than a second one invented and left to drift.

    NEVER A WILDCARD
        ``frame-ancestors *`` would let any site on the internet frame a
        preview and attempt clickjacking against it. The value is always the
        explicit allowlist. If that list is somehow empty, this falls back to
        ``'none'`` -- refusing all framing -- because an empty allowlist must
        not silently mean "anyone".

        The exposure this does add is bounded: a framed preview is the user's
        own static output, with ``connect-src 'none'`` and ``form-action
        'none'``, so there is no credentialed action inside it to hijack.
    """
    origins = [origin for origin in allowed_origins if origin and origin != "*"]
    ancestors = " ".join(origins) if origins else "'none'"
    return "; ".join((*_CSP_DIRECTIVES, f"frame-ancestors {ancestors}"))

# Everything except the CSP, which depends on configuration and is added per
# response by `_headers_for` below.
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # The preview is regenerated on every build and must never be served from a
    # cache after a rebuild, or a user reviews the previous version.
    "Cache-Control": "no-store",
}


def _headers_for(request: Request) -> dict[str, str]:
    """The static headers plus a CSP built from this server's CORS allowlist.

    Settings are read from application state rather than ``get_settings()`` so
    a test that builds an app with a different allowlist gets that allowlist,
    not a cached process-wide one.
    """
    settings = getattr(request.app.state, "settings", None)
    origins = getattr(settings, "allowed_origins", ()) if settings else ()
    return {**_SECURITY_HEADERS, "Content-Security-Policy": _csp(origins)}


def get_project_service(request: Request) -> ProjectService:
    """Resolve the service from application state, as the API router does."""
    service: ProjectService | None = getattr(
        request.app.state, "project_service", None
    )
    if service is None:  # pragma: no cover - a wiring mistake, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Project service is not configured.",
        )
    return service


def _not_found() -> HTTPException:
    """One response for every reason a file cannot be served.

    Unknown project, unbuilt project, traversal attempt and missing file all
    return the same 404. Distinguishing them would let a caller map the
    filesystem by watching which error comes back (docs/security.md 3.4).
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def resolve_preview_file(dist_dir: Path, relative_path: str) -> Path:
    """Resolve a requested path inside ``dist_dir``, or raise 404.

    Two independent checks, in this order:

    1. The resolved path must sit inside the resolved ``dist_dir``. This is the
       real containment guarantee and it catches ``..``, absolute paths, and
       symlinks that point outward, because resolution happens before comparison.
    2. The extension must be one this server knows. An unlisted type is refused
       rather than guessed at.

    An empty path or a directory serves ``index.html``, which is how a SPA's
    entry point is reached.
    """
    base = dist_dir.resolve()

    # An absolute or drive-qualified path would otherwise replace the base
    # entirely when joined, so it is rejected on sight.
    candidate_rel = relative_path.strip("/")
    if not candidate_rel:
        candidate_rel = INDEX_FILE

    candidate = (base / candidate_rel).resolve()

    if candidate != base and base not in candidate.parents:
        logger.warning("rejected preview path outside the project dist directory")
        raise _not_found()

    if candidate.is_dir():
        candidate = (candidate / INDEX_FILE).resolve()
        if base not in candidate.parents and candidate.parent != base:
            raise _not_found()

    if not candidate.is_file():
        raise _not_found()

    if candidate.suffix.lower() not in _CONTENT_TYPES:
        logger.warning("refused to serve unlisted preview type %r", candidate.suffix)
        raise _not_found()

    return candidate


@router.get("/{token}", include_in_schema=False)
@router.get("/{token}/{file_path:path}")
async def serve_preview(
    token: str,
    request: Request,
    file_path: str = "",
    service: ProjectService = Depends(get_project_service),
    preview_secret: str = Depends(get_preview_secret),
) -> FileResponse:
    """Serve one file from a project's built output, against a signed token.

    Read-only by construction: the router declares GET alone, so there is no
    write path to secure.

    WHY THE FIRST SEGMENT IS A TOKEN AND NOT A PROJECT ID
        It used to be a project id, with no authentication of any kind, and
        that was a cross-user data leak: a probe fetched user A's built
        ``index.html`` as user B and with no credentials at all, receiving 200
        and A's content both times.

        It cannot simply require a bearer token instead, because nothing that
        loads a preview can send one — a browser tab and an iframe both issue
        plain GETs. So the capability travels in the path, signed and
        five-minute-lived, minted only by the owner-scoped
        ``GET /api/v1/projects/{id}/preview``
        (``backend/auth/preview_token.py`` explains the design).

        Old ``/preview/{project_id}/...`` URLs now fail verification and 404,
        which is the intended outcome rather than a migration oversight.

    THREE CHECKS, NONE OF WHICH SUBSTITUTES FOR ANOTHER
        1. The token verifies: our signature, unexpired, right scope.
        2. The project still exists AND is still owned by the token's subject.
           Without this a token would outlive a deletion or an ownership
           change for the remainder of its TTL.
        3. The resolved path stays inside this project's ``dist/``
           (``resolve_preview_file``, unchanged and already adversarial).

        Every failure returns the same bare 404.
    """
    try:
        grant = preview_token.verify(token, secret=preview_secret)
    except PreviewTokenError as exc:
        # The token itself is never logged: it IS the credential, and this log
        # line is the one place it would otherwise land (docs/security.md 2, 10).
        logger.info("rejected preview token: %s", exc)
        raise _not_found() from None

    project_id = grant.project_id

    # Re-check ownership against CURRENT state rather than trusting the claim.
    # `for_owner` binds the repository to the token's subject, so a project the
    # subject no longer owns -- or that has since been deleted -- raises
    # ProjectNotFoundError here instead of being served.
    #
    # ValueError covers a `sub` that is not a UUID at all, which a valid
    # signature does not rule out.
    try:
        scoped = service.for_owner(UUID(grant.owner_id))
        scoped.get(project_id)
        workspace = scoped.workspace_dir(project_id)
    except (ProjectNotFoundError, InvalidProjectIdError, ValueError):
        logger.info("preview token no longer authorizes its project jti=%s", grant.jti)
        raise _not_found() from None

    dist_dir = workspace / PREVIEW_DIR
    if not dist_dir.is_dir():
        # Nothing built yet. 404 rather than 409: this endpoint serves files, and
        # the typed API endpoint is where "no successful build" is explained
        # (docs/api.md 2.5).
        raise _not_found()

    target = resolve_preview_file(dist_dir, file_path)
    return FileResponse(
        target,
        media_type=_CONTENT_TYPES[target.suffix.lower()],
        headers=_headers_for(request),
    )
