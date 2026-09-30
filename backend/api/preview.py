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
    Every byte here originated from a model. Three separate controls apply:

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
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

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
# frame-ancestors 'self' lets the Chatbot page embed it while nobody else can.
_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
    )
)

_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # The preview is regenerated on every build and must never be served from a
    # cache after a rebuild, or a user reviews the previous version.
    "Cache-Control": "no-store",
}


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


@router.get("/{project_id}", include_in_schema=False)
@router.get("/{project_id}/{file_path:path}")
async def serve_preview(
    project_id: str,
    file_path: str = "",
    service: ProjectService = Depends(get_project_service),
) -> FileResponse:
    """Serve one file from this project's built output.

    Read-only by construction: the router declares GET alone, so there is no
    write path to secure.
    """
    try:
        workspace = service.workspace_dir(project_id)
    except (ProjectNotFoundError, InvalidProjectIdError):
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
        headers=_SECURITY_HEADERS,
    )
