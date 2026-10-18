"""AG-UI event stream: the browser attaches to a run already in flight.

WHY THIS ROUTE EXISTS
    ``docs/api.md`` 2.8 specified an SSE stream and nothing implemented it, so
    the workspace polled ``GET /status`` every 1.5s and could show only a
    stage name. This is the stream, speaking the AG-UI protocol.

WHY IT ATTACHES INSTEAD OF RUNNING
    The usual AG-UI shape is "POST a run, stream the response". That cannot
    work here: ``POST /projects`` already starts the graph in a background
    task, so a route that executed the agent itself would run the SAME graph
    twice — duplicate model calls, two writers on one checkpoint.

    So this is a GET that subscribes to the in-process broker
    (``backend/services/agui_stream.py``). One run, any number of watching
    tabs, and no execution happens here at all.

WHAT A CLIENT GETS, IN ORDER
    1. ``STATE_SNAPSHOT`` — the persisted state, immediately. This is what
       makes reload and reconnect work: a browser is never blank waiting for
       the next event, and a run that already finished still renders.
    2. ``detroit.hitl`` (CUSTOM) when the run is parked at a gate, so a
       reconnecting tab sees the question it must answer.
    3. Any recent events the broker still holds, for a tab that attached a
       moment after the run began.
    4. Live events until the client disconnects.

    A settled run gets ``RUN_FINISHED`` immediately and the stream closes.
    There is nothing to wait for, and holding the connection open would make a
    finished build look like a running one.

AUTHENTICATION AND OWNERSHIP ARE UNCHANGED
    ``get_scoped_service`` is the same dependency every project route uses, so
    the caller is a verified Supabase identity and the service is bound to
    them. Another user's project is a 404, exactly as everywhere else — the
    stream inherits ownership rather than re-implementing it.

    EventSource cannot send an Authorization header, so this route is consumed
    with ``fetch`` + a stream reader on the frontend rather than with the
    EventSource API. That is a deliberate trade: keeping the bearer token is
    worth more than the four lines EventSource would save.

Spec: docs/api.md 1.1, 2.8; docs/security.md 6; CLAUDE.md 25.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from ag_ui.core import BaseEvent
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend.api.deps import get_scoped_service
from backend.memory.artifacts import InvalidProjectIdError, ProjectNotFoundError
from backend.schemas.enums import RunStatus
from backend.services import agui_translator as agui
from backend.services.agui_stream import broker
from backend.services.projects import ProjectService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["agui"])

# How long to wait for an event before emitting a comment line. Without it a
# proxy or a laptop sleeping can silently drop an idle connection and the tab
# would sit there looking connected.
KEEPALIVE_SECONDS = 15.0

# A settled run is one nothing more will happen to without the user acting.
_SETTLED = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


@router.get("/projects/{project_id}/agui")
async def stream_agui(
    project_id: str,
    request: Request,
    service: ProjectService = Depends(get_scoped_service),
) -> StreamingResponse:
    """Stream this project's AG-UI events. Starts nothing; observes only."""
    try:
        state = service.get(project_id)
    except (ProjectNotFoundError, InvalidProjectIdError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown project."
        ) from None

    encoder = EventEncoder()

    async def body() -> AsyncIterator[str]:
        # Subscribe BEFORE sending the snapshot, so an event published between
        # the two is queued rather than lost.
        async with broker.subscribe(project_id) as queue:
            yield encoder.encode(agui.state_snapshot(state))

            if state.run_status is RunStatus.AWAITING_HITL:
                yield encoder.encode(agui.hitl_waiting(state))

            for event in broker.replay(project_id):
                yield encoder.encode(event)
                if event.type.value == "RUN_FINISHED":
                    # The run already ended before this tab attached. Catching
                    # it up and then holding the socket open would make a
                    # finished build look like a running one.
                    return

            if state.run_status in _SETTLED:
                # Nothing is running. Close rather than hold the connection.
                yield encoder.encode(agui.run_finished(project_id, "settled"))
                return

            while True:
                if await request.is_disconnected():
                    logger.info("agui client disconnected project_id=%s", project_id)
                    return
                try:
                    event: BaseEvent = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_SECONDS
                    )
                except asyncio.TimeoutError:
                    # An SSE comment: keeps the connection warm, and a client
                    # parser ignores it.
                    yield ": keepalive\n\n"
                    continue
                except asyncio.CancelledError:
                    # The server is shutting down or the client vanished.
                    # Exiting cleanly lets the subscriber be removed.
                    raise

                yield encoder.encode(event)

                # RUN_FINISHED ends this connection. A later run publishes to a
                # fresh subscription, which the client opens again.
                if event.type.value == "RUN_FINISHED":
                    return

    return StreamingResponse(
        body(),
        media_type=encoder.get_content_type(),
        headers={
            # Without this an intermediary may buffer the stream and deliver it
            # all at once, which defeats the point.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
