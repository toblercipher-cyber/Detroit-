"""Project lifecycle service: create, read, list, delete, and run the workflow.

WHY THIS FILE EXISTS
    ``docs/architecture.md`` 3.2 requires route handlers to stay thin — validate,
    delegate, return a typed response. This is what they delegate to. Putting this
    logic in the routes would mean orchestration, persistence and HTTP concerns
    tangled in one place, and the workflow would become impossible to drive from
    anywhere but an HTTP request.

    It is the seam where the three lower layers meet: the repository (durable state),
    the graph (stage transitions), and configuration (limits).

LAYER
    Services (``backend/services/``). Depends on config, schemas, memory and graph.
    It imports nothing from ``backend/api`` and knows no HTTP: it raises typed errors
    and the API layer maps them to status codes. That keeps the service usable by a
    future CLI or scheduled run without dragging FastAPI along.

SESSIONS
    A project is the durable unit of work; a session is one client attachment to it.
    Losing a session never loses project state — that separation is the point
    (docs/state.md 5.3).

WHAT THIS FILE DOES NOT DO
    - It does not schedule background work. ``run_workflow`` is awaitable and the
      caller decides how to run it; inventing a task runner here would be
      infrastructure nobody asked for (docs/api.md 2.2).
    - It does not resolve HITL decisions. That path owns ``approved_decisions`` and
      ``hitl_history`` and belongs in its own service (docs/state.md 3).

Spec: docs/api.md 2.2; docs/architecture.md 3.2, 4; docs/state.md 5.2, 5.3;
      skills.md 23.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langgraph.types import Command

from backend.config.settings import Settings
from uuid import UUID, uuid4

from backend.memory.postgres import (
    OwnerScopedRepository,
    PostgresStateRepository,
)
from backend.memory.artifacts import (
    ProjectNotFoundError,
    StateRepository,
)
from backend.schemas.api import (
    ErrorCode,
    HitlDecisionAccepted,
    HitlDecisionRequest,
)
from backend.schemas.enums import RunStatus, WorkflowStage
from backend.schemas.state import (
    HitlCheckpoint,
    ProjectState,
    ReferenceImageRef,
    new_image_id,
    new_session_id,
)
from backend.services import agui_translator as agui

logger = logging.getLogger(__name__)

# LangGraph reports a paused run by adding this key to the returned mapping. It is
# transport, not project state: ``ProjectState`` is ``extra="forbid"``, so it must be
# separated out before validation rather than validated alongside the real fields.
_INTERRUPT_KEY = "__interrupt__"

# Stages during which the project is mid-flight. Accepting a new instruction or a
# delete here would race the running graph, so both are refused (docs/api.md 2.2).
_BUSY_STAGES: frozenset[WorkflowStage] = frozenset(
    {
        WorkflowStage.BUILD,
        WorkflowStage.PREVIEW,
        WorkflowStage.EVALUATION,
        WorkflowStage.TARGETED_FIX,
        WorkflowStage.RE_EVALUATION,
        WorkflowStage.EXTERNAL_ACTION,
    }
)

# Title fallback length when the user supplies none. Long enough to recognise a
# project in a list, short enough not to become a paragraph.
_DERIVED_TITLE_MAX = 60


class ProjectServiceError(RuntimeError):
    """Base class for service-level failures the API maps to status codes."""


class ProjectBusyError(ProjectServiceError):
    """The project is mid-stage and cannot accept this action right now."""

    code = ErrorCode.INVALID_STAGE_FOR_ACTION


class UploadTooLargeError(ProjectServiceError):
    """The uploaded file is over ``MAX_UPLOAD_BYTES``."""

    code = ErrorCode.UPLOAD_TOO_LARGE


class UnsupportedMediaTypeError(ProjectServiceError):
    """The upload is not an allowlisted image, by declaration or by content."""

    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE


class NoPendingCheckpointError(ProjectServiceError):
    """A decision arrived for a project that is not waiting at a gate."""

    code = ErrorCode.NO_PENDING_CHECKPOINT


class CheckpointAlreadyResolvedError(ProjectServiceError):
    """The decision names a gate that is no longer the pending one.

    Almost always a stale client: the gate was answered in another tab, or the page
    was left open across a later stage. Distinct from "no gate at all" because the
    user-facing remedy differs — reload and look at the current gate, rather than
    wait for one.
    """

    code = ErrorCode.CHECKPOINT_ALREADY_RESOLVED


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _derive_title(prompt: str) -> str:
    """A readable title from the opening of the request.

    Deliberately mechanical rather than model-generated: naming a project should not
    cost a model call, and the user can rename it.
    """
    first_line = prompt.strip().splitlines()[0] if prompt.strip() else "Untitled project"
    condensed = " ".join(first_line.split())
    if len(condensed) <= _DERIVED_TITLE_MAX:
        return condensed
    return condensed[: _DERIVED_TITLE_MAX - 1].rstrip() + "…"


def _sniff_image_type(content: bytes) -> str | None:
    """The media type implied by the leading bytes, or ``None`` if unrecognised.

    Content decides the type; the declared ``Content-Type`` header does not
    (docs/security.md 4). A header is client-supplied text and an attacker sets it
    freely, so a PNG label on a payload proves nothing.

    Deliberately a fixed table of the three allowlisted signatures rather than a
    general image library: recognising fewer formats is the point, and a sniffer
    that understands more of them is a larger parser on untrusted input.
    """
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    # RIFF container; the WEBP marker sits after the 4-byte length field.
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_state(values: Any) -> ProjectState | None:
    """Best-effort ProjectState from a mid-run values chunk, or None.

    Used only to describe progress. A chunk that cannot be validated yields
    None and the event is emitted without a snapshot — a malformed observation
    must never interrupt the run it is observing.
    """
    if values is None:
        return None
    try:
        state, _ = _state_from_result(values)
        return state
    except Exception:
        return None


def _state_from_result(result: Any) -> tuple[ProjectState, HitlCheckpoint | None]:
    """Split a graph result into project state and a pending gate, if any.

    LangGraph returns the state mapping with ``__interrupt__`` added when a run
    pauses. ``ProjectState`` forbids extra fields — deliberately, so nothing
    smuggles undeclared data into state — which means handing the raw mapping to
    ``model_validate`` raises on **every** paused run. Since the very first gate
    pauses, that is every run.

    Separating the key here keeps the forbidding strict and the pause legal.

    A malformed or empty payload yields ``None`` rather than a guess: the run is
    still reported, just without a gate, so a broken interrupt degrades to "no
    pending checkpoint" instead of inventing one the user never saw.
    """
    if isinstance(result, ProjectState):
        return result, None

    payload = result.get(_INTERRUPT_KEY) if isinstance(result, dict) else None
    fields = {k: v for k, v in result.items() if k != _INTERRUPT_KEY}
    state = ProjectState.model_validate(fields)

    if not payload:
        return state, None

    # A tuple of Interrupt objects, each carrying the value the node passed to
    # interrupt(). Our gates raise exactly one; if a future parallel branch raises
    # more, the first is the one being answered and the rest re-raise on resume.
    first = payload[0] if isinstance(payload, (list, tuple)) else payload
    raw = getattr(first, "value", first)
    try:
        return state, HitlCheckpoint.model_validate(raw)
    except Exception:
        logger.warning(
            "paused run carried an unreadable checkpoint payload project_id=%s",
            state.project_id,
        )
        return state, None


class ProjectService:
    """Owns the lifecycle of a project and the runs performed against it."""

    def __init__(
        self,
        repository: StateRepository,
        workflow: Any,
        settings: Settings,
        event_sink: Callable[[str, Any], None] | None = None,
    ) -> None:
        """
        Args:
            repository: durable state. Injected as the interface, never a concrete
                store, so Supabase can replace the filesystem later without touching
                this class (docs/state.md 5.0).
            workflow: the compiled LangGraph workflow.
            settings: validated configuration; supplies the iteration limit.
            event_sink: where AG-UI events go, as ``(project_id, event)``.
                Injected rather than imported so this class keeps no global
                state and a test can run the graph with no observer at all.
                ``None`` means the run emits nothing, which is exactly what
                every existing suite wants.
        """
        self._repository = repository
        self._workflow = workflow
        self._settings = settings
        self._event_sink = event_sink

    # --- AG-UI event emission --------------------------------------------

    def _emit(self, project_id: str, event: Any) -> None:
        """Publish one event, and never let the observer break the run.

        A failing sink is a monitoring problem; a build that dies because a
        browser disconnected is a product problem. The run always wins.
        """
        if self._event_sink is None:
            return
        try:
            self._event_sink(project_id, event)
        except Exception:  # pragma: no cover - defensive
            logger.warning("agui event sink failed project_id=%s", project_id)

    async def _run_graph(
        self, project_id: str, payload: Any, config: dict[str, Any]
    ) -> Any:
        """Execute the graph, streaming AG-UI events, returning what ainvoke would.

        WHY astream AND NOT ainvoke
            ``ainvoke`` returns only the final mapping, so there is nothing to
            report while a build runs. ``astream`` with BOTH modes gives the
            per-node updates the timeline needs AND the full state values.

        WHY THE RESULT IS STILL EXACTLY ainvoke's
            Verified against the real graph rather than assumed: the last
            ``values`` chunk already carries ``__interrupt__`` when a gate
            fires, so it is the same mapping ``ainvoke`` returned and
            ``_state_from_result`` parses it unchanged. Pause, resume and
            failure handling are therefore untouched by this change.
        """
        run_id = uuid4().hex
        last_values: Any = None
        self._emit(project_id, agui.run_started(project_id, run_id))

        async for mode, chunk in self._workflow.astream(
            payload, config=config, stream_mode=["updates", "values"]
        ):
            if mode == "values":
                last_values = chunk
                continue
            for node, _update in (chunk or {}).items():
                if node == _INTERRUPT_KEY:
                    continue
                # The node has already finished by the time the update
                # arrives; STEP_STARTED is emitted alongside so a client sees
                # an opened and closed step rather than a bare close.
                self._emit(project_id, agui.step_started(node))
                self._emit(project_id, agui.activity(node))
                state = _safe_state(last_values)
                for event in agui.from_node_update(node, state):
                    self._emit(project_id, event)

        return last_values

    def _emit_settled(self, project_id: str, state: ProjectState, run_id: str) -> None:
        """Close out a run: the gate it is waiting at, or how it ended."""
        self._emit(project_id, agui.state_snapshot(state))
        if state.run_status is RunStatus.AWAITING_HITL:
            self._emit(project_id, agui.hitl_waiting(state))
        elif state.run_status is RunStatus.FAILED:
            message = (
                state.last_error.message
                if state.last_error
                else "The run failed."
            )
            self._emit(project_id, agui.run_error(message))
        self._emit(project_id, agui.run_finished(project_id, run_id))

    # --- Caller scoping ---------------------------------------------------

    def for_owner(self, owner_id: UUID) -> "ProjectService":
        """A service whose every operation is restricted to one verified user.

        WHY SCOPING LIVES HERE AND NOT ON EACH METHOD
            Adding ``owner_id`` to eight methods would mean eighteen call sites
            inside this class and every caller in the API and the tests. One of
            them eventually gets missed, and a missed owner argument is a
            cross-user read. Binding once makes the unscoped version
            unreachable instead of merely discouraged.

        WHAT HAPPENS IF A ROUTE FORGETS TO CALL THIS
            It crashes, loudly, and that is deliberate. The unbound service
            holds a :class:`PostgresStateRepository`, whose methods require a
            keyword ``owner_id``; calling ``load(project_id)`` on it raises
            ``TypeError`` before a query is built. There is no code path where
            a forgotten scope silently returns another user's rows.

        Args:
            owner_id: ``auth.users.id``, taken from a VERIFIED access token.
                Never from a path, query string or request body
                (``docs/api.md`` 1.1).
        """
        repository = self._repository
        if isinstance(repository, PostgresStateRepository):
            scoped: StateRepository = OwnerScopedRepository(repository, owner_id)
        elif isinstance(repository, OwnerScopedRepository):
            # Re-binding an already-bound service would silently widen or
            # narrow access depending on the argument. Refuse instead.
            raise ProjectServiceError("This service is already bound to a caller.")
        else:
            # Single-user development store. It has no owner column and cannot
            # enforce isolation, so it is only reachable when no application
            # database is configured -- main.py refuses that combination in
            # production. Returning self keeps dev and tests working without
            # pretending the filesystem is enforcing anything.
            return self
        return ProjectService(
            repository=scoped, workflow=self._workflow, settings=self._settings
        )

    @property
    def is_owner_scoped(self) -> bool:
        """True when this instance is restricted to one user's data."""
        return isinstance(self._repository, OwnerScopedRepository)

    # --- Lifecycle -------------------------------------------------------

    def create(
        self,
        prompt: str,
        *,
        title: str | None = None,
        surface_hint: str | None = None,
    ) -> ProjectState:
        """Create a project and persist it before any work begins.

        Persisting first is deliberate: if the workflow then fails, the project still
        exists and the user can see why, rather than the request vanishing.
        """
        state = ProjectState(
            user_request=prompt,
            title=title.strip() if title and title.strip() else _derive_title(prompt),
            current_stage=WorkflowStage.REQUIREMENT_ANALYSIS,
            run_status=RunStatus.IDLE,
        )
        if surface_hint:
            # Recorded as a scratch hint rather than a requirement: the user said it,
            # but only the Requirement Analyzer decides what becomes a requirement.
            state.node_scratch["surface_hint"] = surface_hint

        self._repository.save(state)
        logger.info(
            "project created project_id=%s session_id=%s",
            state.project_id,
            state.session_id,
        )
        return state

    def get(self, project_id: str) -> ProjectState:
        """Load stored state, or raise :class:`ProjectNotFoundError`."""
        return self._repository.load(project_id)

    def save(self, state: ProjectState) -> None:
        """Persist a state this caller already loaded.

        Owner-scoped like every other operation: on a bound service the
        repository supplies the owner, and the UPDATE policy's WITH CHECK
        refuses a row that is not the caller's. There is deliberately no
        owner argument here — a caller that could name an owner could write
        into another user's project (``docs/database.md`` 1.3.5).

        Added for the GitHub routes, which mutate a project (the connected
        repository, the sync timestamps, the action history) outside the
        workflow. Those fields are user decisions and audit records, not graph
        output, so they do not belong to a workflow node.
        """
        self._repository.save(state)

    def list_summaries(self) -> list[ProjectState]:
        """Stored projects, newest first.

        Loads each project's state rather than returning ids alone, because a summary
        needs the title, stage and status. A future store can answer this with one
        query behind the same interface.
        """
        states: list[ProjectState] = []
        for project_id in self._repository.list_project_ids():
            try:
                states.append(self._repository.load(project_id))
            except ProjectNotFoundError:
                continue
            except Exception:
                # One unreadable project must not make the whole list unavailable.
                logger.warning("skipping unreadable project in listing")
                continue
        return states

    def delete(self, project_id: str) -> None:
        """Delete a project, refusing while a run is in flight.

        Irreversible, so it happens only on an explicit request and never as a side
        effect of generation (docs/api.md 2.2).
        """
        state = self._repository.load(project_id)
        self._ensure_not_busy(state)
        self._repository.delete(project_id)

    def attach_session(self, project_id: str) -> ProjectState:
        """Issue a new session for an existing project.

        A session carries no approved decisions, so re-attaching never changes what
        the project has settled (docs/state.md 5.3).
        """
        state = self._repository.load(project_id)
        state.session_id = new_session_id()
        state.updated_at = _utcnow()
        self._repository.save(state)
        return state

    # --- Reference images -------------------------------------------------

    def attach_reference_image(
        self, project_id: str, content: bytes, declared_type: str
    ) -> ReferenceImageRef:
        """Validate an upload, store the bytes, and record it in state.

        Order matters and is not arbitrary. Size is checked first because it is the
        cheapest rejection; type is confirmed from the content before anything is
        written; nothing touches the filesystem until both have passed. The state
        record is appended only after the bytes are durable, so a listed image
        always exists on disk.

        Args:
            project_id: the project to attach to.
            content: the raw uploaded bytes. Never decoded, parsed or executed.
            declared_type: the client's ``Content-Type``. Checked against the
                allowlist AND against the real content; a disagreement is rejected
                rather than resolved in the client's favour.

        Returns:
            The durable metadata record, which is what the API echoes back. The
            client filename is not part of it and is never stored.

        Raises:
            ProjectNotFoundError: no such project.
            UploadTooLargeError: over ``MAX_UPLOAD_BYTES``.
            UnsupportedMediaTypeError: not an allowlisted image, or the declared
                type disagrees with the content.
        """
        state = self._repository.load(project_id)

        if len(content) > self._settings.max_upload_bytes:
            raise UploadTooLargeError(
                f"The file is larger than the {self._settings.max_upload_bytes}-byte limit."
            )

        allowed = self._settings.allowed_upload_types
        actual = _sniff_image_type(content)
        # Both must hold: an allowlisted claim, and content that is actually that
        # type. Either check alone is bypassable — the header by lying, the sniff by
        # uploading a real PNG to an endpoint that only expected JPEG.
        if declared_type not in allowed or actual is None or actual != declared_type:
            logger.warning(
                "rejected upload project_id=%s declared=%r sniffed=%r",
                project_id,
                declared_type,
                actual,
            )
            raise UnsupportedMediaTypeError(
                "The file must be a PNG, JPEG or WebP image."
            )

        image_id = new_image_id()
        # ``actual``, never ``declared_type``: the stored extension is derived from
        # the verified content, so the client names neither the file nor its suffix
        # (docs/security.md 4).
        self._repository.save_reference_image(project_id, image_id, content, actual)

        ref = ReferenceImageRef(
            image_id=image_id,
            content_type=actual,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        state.reference_images.append(ref)
        state.updated_at = _utcnow()
        self._repository.save(state)

        logger.info(
            "reference image attached project_id=%s image_id=%s type=%s bytes=%d",
            project_id,
            image_id,
            actual,
            len(content),
        )
        return ref

    # --- Running the workflow --------------------------------------------

    async def run_workflow(self, project_id: str) -> ProjectState:
        """Advance the project through the graph and persist the result.

        Awaitable rather than self-scheduling: the caller decides whether this runs in
        a background task, a worker or a test. State is saved whether the run
        succeeded or failed, so a failure is inspectable rather than lost
        (CLAUDE.md 26).
        """
        state = self._repository.load(project_id)

        state.run_status = RunStatus.RUNNING
        state.updated_at = _utcnow()
        self._repository.save(state)

        config = {"configurable": {"thread_id": project_id}}
        run_id = uuid4().hex
        try:
            result = await self._run_graph(project_id, state, config)
        except Exception:
            # The graph records agent failures in state itself; reaching here means
            # the graph machinery failed, which is not something to route around.
            logger.exception("workflow run failed project_id=%s", project_id)
            state.run_status = RunStatus.FAILED
            state.updated_at = _utcnow()
            self._repository.save(state)
            self._emit(project_id, agui.run_error("The build could not be started."))
            self._emit(project_id, agui.run_finished(project_id, run_id))
            raise

        updated, checkpoint = _state_from_result(result)

        if checkpoint is not None:
            # A paused run is a normal outcome, not a failure: the graph is waiting
            # for the user. The gate node never got to return, because interrupt()
            # suspended it mid-node, so the checkpoint reaches us only through the
            # interrupt payload and this is the one place that can record it
            # (docs/state.md 5.2).
            updated.pending_checkpoint = checkpoint
            updated.run_status = RunStatus.AWAITING_HITL

        updated.updated_at = _utcnow()
        self._repository.save(updated)
        self._emit_settled(project_id, updated, run_id)
        logger.info(
            "workflow run settled project_id=%s stage=%s status=%s checkpoint=%s",
            project_id,
            updated.current_stage.value,
            updated.run_status.value,
            checkpoint.checkpoint_id if checkpoint else "-",
        )
        return updated

    async def submit_hitl_decision(
        self, project_id: str, decision: HitlDecisionRequest
    ) -> HitlDecisionAccepted:
        """Deliver the user's gate decision to the paused graph and resume it.

        This is the write half of the HITL loop; ``run_workflow`` is the read half
        that reports the pause.

        FIELD OWNERSHIP — deliberately not written here
            ``docs/state.md`` 3 gives ``hitl_history`` and ``approved_decisions`` to
            "the HITL resume path only". That path is the second half of
            ``_gate_node``: the code after ``interrupt()`` returns, which executes
            *only* on resume and already appends both. Appending them here as well
            would duplicate every record, so this method delivers the decision and
            persists the outcome, and the gate node stays the single writer.

        Args:
            project_id: the project whose gate is being answered.
            decision: an already-validated decision. Its action/payload consistency
                is enforced by the model, so this method checks only what the model
                cannot see: whether this gate is actually the pending one.

        Returns:
            What the decision did: the stage it resolved and where the run now sits.

        Raises:
            ProjectNotFoundError: no such project.
            NoPendingCheckpointError: the project is not waiting at a gate.
            CheckpointAlreadyResolvedError: the decision names a superseded gate.
            ProjectServiceError: the resume itself failed.
        """
        state = self._repository.load(project_id)
        pending = state.pending_checkpoint

        if pending is None:
            raise NoPendingCheckpointError(
                "This project is not waiting for a review decision."
            )

        if decision.checkpoint_id != pending.checkpoint_id:
            # Compared against the checkpoint rather than trusted from the request:
            # a stale tab holding an old id must not be able to answer a gate the
            # user has not seen.
            raise CheckpointAlreadyResolvedError(
                "This review has already been answered. Reload to see the current one."
            )

        stage = pending.stage
        config = {"configurable": {"thread_id": project_id}}

        run_id = uuid4().hex
        try:
            result = await self._run_graph(
                project_id,
                # mode="json" because the payload crosses the checkpointer, which
                # serialises it; the gate re-validates it as a HitlDecisionRequest.
                Command(resume=decision.model_dump(mode="json")),
                config,
            )
        except Exception as exc:
            # Nothing from LangGraph reaches the caller: the message could name
            # internal nodes or paths (docs/security.md 6). The detail is logged,
            # and the caller gets a typed error with a safe message.
            logger.exception(
                "resume failed project_id=%s stage=%s action=%s",
                project_id,
                stage.value,
                decision.action.value,
            )
            state.run_status = RunStatus.FAILED
            state.updated_at = _utcnow()
            self._repository.save(state)
            self._emit(project_id, agui.run_error("The review could not be resumed."))
            self._emit(project_id, agui.run_finished(project_id, run_id))
            raise ProjectServiceError(
                "The workflow could not be resumed from this review."
            ) from exc

        updated, checkpoint = _state_from_result(result)

        if checkpoint is not None:
            # Another gate fired — either the next stage's, or this one again after
            # a revision re-ran it.
            updated.pending_checkpoint = checkpoint
            updated.run_status = RunStatus.AWAITING_HITL
        elif updated.run_status is not RunStatus.FAILED:
            # A failed run keeps its status; anything else is moving again.
            updated.pending_checkpoint = None
            updated.run_status = RunStatus.RUNNING

        updated.updated_at = _utcnow()
        self._repository.save(updated)
        self._emit_settled(project_id, updated, run_id)

        logger.info(
            "hitl decision applied project_id=%s stage=%s action=%s next_status=%s",
            project_id,
            stage.value,
            decision.action.value,
            updated.run_status.value,
        )

        return HitlDecisionAccepted(
            accepted=True,
            stage=stage,
            status=updated.run_status,
            resumed=True,
        )

    # --- Derived values used by the API ----------------------------------

    @property
    def max_iterations(self) -> int:
        """The automatic-correction limit, from configuration rather than a literal."""
        return self._settings.max_auto_iterations

    def preview_available(self, state: ProjectState) -> bool:
        """True when a successful build has produced a preview."""
        return state.preview_status.preview_url is not None

    def workspace_dir(self, project_id: str) -> Path:
        """Where this project's built output lives.

        Exposed so the preview route can find ``dist/`` without reaching into
        ``self._repository``. The id is validated by the repository, so an
        unknown or malformed one raises rather than resolving to a path.

        Existence is not checked here: a project can legitimately have no
        workspace yet, and the caller decides what that means.
        """
        return self._repository.workspace_dir(project_id)

    # --- Internals -------------------------------------------------------

    def _ensure_not_busy(self, state: ProjectState) -> None:
        if state.run_status is RunStatus.RUNNING and state.current_stage in _BUSY_STAGES:
            raise ProjectBusyError(f"Project is busy in stage '{state.current_stage.value}'.")
