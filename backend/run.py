"""Entrypoint that starts the server on an event loop psycopg can use.

WHY THIS FILE EXISTS
    ``uvicorn backend.main:app`` does not work on Windows, and the reason is
    not obvious enough to leave as a README note.

    uvicorn does not consult the asyncio *policy*. It builds its loop from a
    factory:

        def asyncio_loop_factory(use_subprocess=False):
            if sys.platform == "win32" and not use_subprocess:
                return asyncio.ProactorEventLoop
            return asyncio.SelectorEventLoop

    So on Windows it instantiates ``ProactorEventLoop`` directly, and async
    psycopg refuses to run on it:

        Psycopg cannot use the 'ProactorEventLoop' to run in async mode.

    The durable checkpointer's pool then cannot open a single connection and
    startup dies with ``PoolTimeout`` after 20 seconds — a confusing symptom
    for a cause that has nothing to do with the database.

    Setting ``asyncio.set_event_loop_policy`` anywhere, including at import of
    ``backend.main``, does not help: a factory is not a policy, and by then
    uvicorn has already built its loop.

WHAT THIS DOES INSTEAD
    Constructs the server itself and runs it with an explicit
    ``loop_factory``, which is the one thing that actually decides the loop
    type. Everything else — the app, lifespan, settings — is unchanged.

WHY NOT JUST USE ``--reload``
    It happens to work, because reload sets ``use_subprocess=True`` and the
    factory then returns ``SelectorEventLoop``. Depending on that would make
    production correctness a side effect of a development flag.

PLAYWRIGHT STILL WORKS
    The rendered-preview capture needs a subprocess, which ``SelectorEventLoop``
    cannot spawn. That is handled where it belongs: the capture runs in a
    worker thread and swaps the policy for the duration, and Playwright's sync
    API builds its own loop from the policy
    (``backend/tools/preview_capture.py``).

USAGE
    python -m backend.run

Spec: docs/state.md 5.0.2; docs/runtime.md 7.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from backend.config.settings import get_settings


def main() -> None:
    """Run the API on a loop the database driver supports."""
    settings = get_settings()
    config = uvicorn.Config(
        "backend.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.value.lower(),
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # The whole point of this module. SelectorEventLoop is what async
        # psycopg requires; `loop_factory` is the only lever that sets it,
        # and it exists from Python 3.12.
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
