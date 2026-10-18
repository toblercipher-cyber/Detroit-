"""AG-UI event broker: one live run, many attached browsers.

WHY THIS FILE EXISTS
    ``POST /projects`` starts the graph in a FastAPI ``BackgroundTask`` and
    returns immediately. The browser then has to watch a run that is ALREADY
    executing somewhere else in the process.

    That rules out the usual AG-UI shape, where the HTTP request *is* the run
    and the response streams it. Doing that here would start a second graph
    execution for the same project — duplicate model calls, duplicate commits,
    two writers racing on one checkpoint. So the stream ATTACHES instead: the
    background run publishes events here, and each connected browser
    subscribes.

WHAT IT IS NOT
    Not a message queue, not durable, not cross-process. It is an in-memory
    fan-out for the lifetime of one server process, which is exactly the scope
    of one FastAPI app running one background task. Durability is already
    solved underneath it by the PostgreSQL checkpointer and ``ProjectState`` —
    a browser that misses events reloads the persisted state and carries on
    (``backend/api/agui.py`` does that on every connect).

BACKPRESSURE, AND WHY A SLOW CLIENT CANNOT STALL A BUILD
    Every subscriber gets its own bounded queue. If a browser stops reading —
    a backgrounded tab, a wedged connection — its queue fills and the OLDEST
    events are dropped for that subscriber only. The publisher never blocks and
    never waits, so a slow client can degrade its own view and nothing else.
    The run always wins.

CLEANUP
    A subscriber is removed when its context manager exits, which FastAPI
    triggers on client disconnect. When the last subscriber for a project goes
    away the project's entry is deleted, so an idle server holds nothing.

Spec: docs/api.md 2.8; docs/architecture.md 3.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ag_ui.core import BaseEvent

logger = logging.getLogger(__name__)

# Per-subscriber buffer. Deep enough to absorb a burst of node updates, small
# enough that a dead tab cannot pin memory.
QUEUE_SIZE = 256

# Events kept per project so a browser that attaches a moment after the run
# started still sees what it missed. Not durability — a short catch-up window.
REPLAY_SIZE = 64


class AgUiBroker:
    """Fan-out of AG-UI events, keyed by project id."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[BaseEvent]]] = {}
        self._replay: dict[str, deque[BaseEvent]] = {}
        self._lock = asyncio.Lock()

    def publish(self, project_id: str, event: BaseEvent) -> None:
        """Hand one event to every attached browser.

        Deliberately synchronous and non-blocking: it is called from inside
        graph execution, and a publisher that could await is a publisher that
        can be slowed by a reader.
        """
        self._replay.setdefault(project_id, deque(maxlen=REPLAY_SIZE)).append(event)

        for queue in self._subscribers.get(project_id, ()):
            if queue.full():
                # Drop this subscriber's oldest rather than the newest: the
                # recent past is what a late reader needs to catch up.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race with a reader
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - race with a reader
                logger.debug("agui subscriber queue full project_id=%s", project_id)

    def replay(self, project_id: str) -> list[BaseEvent]:
        """Recent events, for a browser that attached mid-run."""
        return list(self._replay.get(project_id, ()))

    def clear(self, project_id: str) -> None:
        """Drop the replay buffer once a run has settled and been read."""
        self._replay.pop(project_id, None)

    @asynccontextmanager
    async def subscribe(
        self, project_id: str
    ) -> AsyncIterator[asyncio.Queue[BaseEvent]]:
        """Attach a browser, and detach it however the request ends."""
        queue: asyncio.Queue[BaseEvent] = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self._lock:
            self._subscribers.setdefault(project_id, set()).add(queue)
        logger.info(
            "agui subscriber attached project_id=%s total=%d",
            project_id,
            len(self._subscribers.get(project_id, ())),
        )
        try:
            yield queue
        finally:
            async with self._lock:
                peers = self._subscribers.get(project_id)
                if peers is not None:
                    peers.discard(queue)
                    if not peers:
                        self._subscribers.pop(project_id, None)
            logger.info("agui subscriber detached project_id=%s", project_id)

    def subscriber_count(self, project_id: str) -> int:
        return len(self._subscribers.get(project_id, ()))


# One broker per process, created at import and wired onto app.state in
# main.py. A module-level instance rather than a dependency because the
# background task and the HTTP route must reach the SAME object, and the task
# has no request to resolve one from.
broker = AgUiBroker()
