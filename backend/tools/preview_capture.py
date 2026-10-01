"""Renders a built project in a headless browser and captures what it looks like.

WHY THIS FILE EXISTS
    Three of the four reviewers are granted the **rendered** preview
    (``docs/agents.md`` 4.6-4.9), and ``RenderedPreviewPayload`` refuses a URL
    alone: a link is not something an agent can look at. Without capture the
    mandatory quality gate blocks, because the only other "evidence" available
    would be the built ``index.html`` — an empty ``<div id="root">`` before React
    runs — and reviewers passing a gate on that is precisely the agent-only
    substitute ``CLAUDE.md`` 15 forbids.

    So this is the piece that turns a build into something reviewable.

WHY A THROWAWAY LOOPBACK SERVER, AND NOT file://
    ``file://`` was tried first and **does not work**. A Vite build loads its
    bundle as an ES module, and a module script fetched from a ``file://`` page
    has origin ``null``, so Chromium blocks it by CORS — along with the
    stylesheet::

        Access to script at 'file:///.../assets/index-*.js' from origin 'null'
        has been blocked by CORS policy

    React never executes, ``#root`` stays empty, and no CSS applies. The capture
    would have handed reviewers a blank page and an empty DOM while looking
    like it worked. The fix is not a browser flag: disabling file-origin
    restrictions would weaken the sandbox around code a model wrote.

    So ``dist/`` is served over HTTP from a short-lived server on
    ``127.0.0.1`` with an ephemeral port, started and stopped inside the
    capture. That is still not the FastAPI app: the quality gate must not
    require the web server to be running, and the real serving path's CSP and
    traversal containment have their own suite. Loopback-only with a port the
    OS picks means nothing is exposed beyond this machine.

TWO VIEWPORTS, AND WHY THE PAYLOAD ONLY CARRIES ONE
    Both desktop (1280x800) and mobile (390x844) are captured, because
    "poor responsive behavior" is on the anti-slop list (``CLAUDE.md`` 15) and a
    reviewer shown only a wide viewport cannot see it.

    ``RenderedPreviewPayload`` holds a single screenshot, so one payload cannot
    carry both. :func:`capture_viewports` returns both, keyed by viewport, and
    :func:`capture_preview` returns the desktop one to match the
    ``Callable[[Path, str], RenderedPreviewPayload]`` seam the graph already
    expects. Surfacing the mobile capture to reviewers needs a change to that
    seam or to the payload model, which is a separate decision.

THE DOM IS POST-RENDER, WHICH IS THE WHOLE POINT
    ``page.content()`` is taken after the network settles, so it is the markup
    React produced — not the shell that shipped. That is the difference between
    a reviewer seeing a heading hierarchy and seeing an empty div.

WHAT IS LOADED IS CONSTRAINED
    Only the workspace's own ``dist/index.html``, resolved and confirmed to sit
    inside that directory. The page is generated code, so it is treated as
    untrusted: no downloads, no dialogs, and a bounded timeout.

LAYER
    Tools (``backend/tools/``). Playwright and the filesystem; no agent, no
    state, no graph.

Spec: docs/architecture.md 5.4; docs/agents.md 4.6-4.9; CLAUDE.md 15;
      docs/security.md 3.3.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.context.builder import RenderedPreviewPayload

logger = logging.getLogger(__name__)


class PreviewCaptureError(RuntimeError):
    """The built output could not be rendered or captured.

    Raised rather than returning a partial payload: the Evaluation node turns
    this into a BLOCKED gate, and a half-captured preview reviewed as if it
    were whole is worse than no preview at all.
    """


@dataclass(frozen=True)
class Viewport:
    """A device class to capture at."""

    name: str
    width: int
    height: int


# Desktop and a common phone width. Two is deliberate: one more viewport is one
# more full page render per evaluation, and these two are what the anti-slop
# checklist's "poor responsive behavior" actually needs to be visible.
DESKTOP = Viewport("desktop", 1280, 800)
MOBILE = Viewport("mobile", 390, 844)
VIEWPORTS: tuple[Viewport, ...] = (DESKTOP, MOBILE)

SCREENSHOT_MEDIA_TYPE = "image/png"

# Generous for a local file, bounded so a page that never settles cannot hang
# the evaluation stage.
LOAD_TIMEOUT_MS = 30_000

# After the network goes quiet, a short grace period for entry animations and
# font swaps to finish. Without it a screenshot can catch a half-faded hero and
# a reviewer reports a visual defect that does not exist.
SETTLE_MS = 400


class _QuietHandler(SimpleHTTPRequestHandler):
    """Static handler that does not write a request log to stderr.

    The default handler prints every request. During a capture that is dozens
    of lines of noise interleaved with the backend's own logs, saying nothing
    the capture result does not already say.
    """

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        return


@contextlib.contextmanager
def _serve(directory: Path) -> Iterator[str]:
    """Serve ``directory`` on loopback for the life of the block.

    Port 0 lets the OS choose a free one, so concurrent captures cannot collide
    and nothing has to be configured. Bound to 127.0.0.1 only: the generated
    site is model-written code and has no business being reachable off this
    machine, even briefly.
    """
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # Daemon so a wedged request thread can never keep the process alive.
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _index_path(dist_dir: Path) -> Path:
    """The entry point to render, confirmed to be inside ``dist_dir``.

    Resolve-then-compare, the same containment used elsewhere: a symlink inside
    ``dist/`` pointing outward would otherwise be rendered, and the page being
    rendered is code a model wrote.
    """
    base = dist_dir.resolve()
    index = (base / "index.html").resolve()
    if base != index.parent and base not in index.parents:
        raise PreviewCaptureError("The preview entry point is outside the build.")
    if not index.is_file():
        raise PreviewCaptureError("The build produced no index.html to render.")
    return index


def capture_viewports(
    dist_dir: Path,
    url: str = "",
    *,
    viewports: tuple[Viewport, ...] = VIEWPORTS,
) -> dict[str, RenderedPreviewPayload]:
    """Capture, from either a synchronous or an asynchronous caller.

    Playwright's sync API refuses to start when an asyncio loop is running in
    the calling thread — "Please use the Async API instead". The only caller
    that matters, ``evaluation_node``, is a coroutine, so the naive version
    raised there while passing every test that called it directly.

    The capture therefore runs in a worker thread when a loop is running. That
    thread has no loop of its own, so the sync API is legal, and the calling
    thread waits exactly as it already did — the node invokes this
    synchronously, so no behaviour changes.

    Using the async API throughout would avoid blocking the loop, but that
    means ``await``-ing here and in the node, which changes the
    ``Callable[[Path, str], RenderedPreviewPayload]`` seam the graph is built
    around. That is a deliberate follow-up, not a thing to slip in.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _capture_viewports_sync(dist_dir, url, viewports=viewports)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            _capture_viewports_sync, dist_dir, url, viewports=viewports
        ).result()


def _capture_viewports_sync(
    dist_dir: Path,
    url: str = "",
    *,
    viewports: tuple[Viewport, ...] = VIEWPORTS,
) -> dict[str, RenderedPreviewPayload]:
    """Render the built site once per viewport and capture DOM and screenshots.

    Args:
        dist_dir: the project's built output directory.
        url: the public preview URL, recorded on the payload so a reviewer can
            refer to it. It is **not** fetched; the local build is.
        viewports: device classes to capture. Defaults to desktop and mobile.

    Returns:
        One payload per viewport, keyed by viewport name.

    Raises:
        PreviewCaptureError: Playwright is unavailable, the build has no entry
            point, or rendering failed.
    """
    index = _index_path(dist_dir)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - depends on the environment
        raise PreviewCaptureError(
            "Playwright is not installed, so the preview cannot be rendered."
        ) from None

    captured: dict[str, RenderedPreviewPayload] = {}
    try:
        with _serve(index.parent) as origin, sync_playwright() as playwright:
            page_url = origin + index.name
            # The payload records where a human can see this, falling back to
            # where it was actually rendered. Never empty: the payload model
            # requires a url, and an empty one fails validation.
            recorded = url or page_url
            browser = playwright.chromium.launch()
            try:
                for viewport in viewports:
                    captured[viewport.name] = _capture_one(
                        browser, page_url, recorded, viewport, PlaywrightError
                    )
            finally:
                # Closed even when a capture fails: a leaked browser process
                # outlives the request and holds memory until the server dies.
                browser.close()
    except PreviewCaptureError:
        raise
    except Exception as exc:
        logger.exception("preview capture failed dist=%s", dist_dir.name)
        raise PreviewCaptureError(
            f"The preview could not be rendered ({type(exc).__name__})."
        ) from None

    logger.info(
        "captured preview viewports=%s dist=%s",
        ",".join(captured),
        dist_dir.name,
    )
    return captured


def _capture_one(
    browser,
    page_url: str,
    recorded_url: str,
    viewport: Viewport,
    playwright_error: type[Exception],
) -> RenderedPreviewPayload:
    """Render at one viewport and build its payload.

    A fresh context per viewport rather than resizing one page: resizing does
    not re-run load-time media queries or device detection, so a "mobile"
    screenshot taken by shrinking a desktop page can show desktop markup.
    """
    context = browser.new_context(
        viewport={"width": viewport.width, "height": viewport.height},
        # The generated page is untrusted code. It gets no device permissions
        # and no ability to put a file on disk.
        accept_downloads=False,
        is_mobile=viewport.name == "mobile",
    )
    try:
        page = context.new_page()
        # Generated code may call alert()/confirm(); unhandled, those block the
        # page forever and the capture dies on timeout instead of saying why.
        page.on("dialog", lambda dialog: dialog.dismiss())

        try:
            page.goto(page_url, wait_until="networkidle",
                      timeout=LOAD_TIMEOUT_MS)
        except playwright_error as exc:
            raise PreviewCaptureError(
                f"The {viewport.name} preview did not finish loading."
            ) from None

        page.wait_for_timeout(SETTLE_MS)

        dom = page.content()
        shot = page.screenshot(type="png", full_page=True)
    finally:
        context.close()

    return RenderedPreviewPayload(
        url=recorded_url,
        dom_html=dom,
        screenshot_base64=base64.b64encode(shot).decode("ascii"),
        screenshot_media_type=SCREENSHOT_MEDIA_TYPE,
    )


def capture_preview(dist_dir: Path, url: str = "") -> RenderedPreviewPayload:
    """Capture the preview and return the desktop payload.

    Matches the ``Callable[[Path, str], RenderedPreviewPayload]`` signature the
    graph's ``preview_capture`` dependency expects, so it can be wired without
    changing the seam.

    Both viewports are still rendered, because the mobile pass is what makes
    responsive problems visible and dropping it would quietly narrow the review.
    Only the desktop payload fits through this signature; see this module's
    docstring.
    """
    captured = capture_viewports(dist_dir, url)
    payload = captured.get(DESKTOP.name)
    if payload is None:  # pragma: no cover - defensive
        raise PreviewCaptureError("The desktop preview was not captured.")
    return payload
