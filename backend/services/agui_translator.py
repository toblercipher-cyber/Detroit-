"""Translate real LangGraph execution into genuine AG-UI protocol events.

WHY THIS FILE EXISTS
    The graph speaks in node names and ``ProjectState`` updates. A browser
    speaks AG-UI. This is the only place that converts one into the other, so
    the event vocabulary is defined once and the workflow stays unaware that
    anything is watching it.

EVERY EVENT CLASS HERE COMES FROM THE OFFICIAL SDK
    ``ag_ui.core`` (package ``ag-ui-protocol``). Nothing in this module invents
    an event shape or a type string. ``EventType.STEP_STARTED`` is the
    protocol's, not ours, and the encoder serialises it to the protocol's
    camelCase wire form.

WHAT MAPS TO WHAT, AND WHY

    graph fact                         AG-UI event
    ---------------------------------  ----------------------------------------
    run begins                         RUN_STARTED
    a node starts / finishes           STEP_STARTED / STEP_FINISHED
    state changed after a node         STATE_SNAPSHOT
    an agent produced prose            TEXT_MESSAGE_START/CONTENT/END
    a tool ran                         TOOL_CALL_START/ARGS/END/RESULT
    DETROIT-specific activity          CUSTOM
    run settles                        RUN_FINISHED
    run or stage failed                RUN_ERROR

    CUSTOM carries the things the protocol has no first-class event for —
    waiting at a HITL gate, the Impeccable verdict, the correction iteration.
    That is what CUSTOM is for, and using it is honest; inventing a
    ``QUALITY_GATE`` event type and calling the result AG-UI would not be.

WHAT IS DELIBERATELY NOT EMITTED
    Chain-of-thought. The graph does not expose model reasoning, and this
    module does not fabricate any. ``REASONING_*`` events exist in the SDK and
    are never used, because DETROIT has no reasoning stream to report. Activity
    labels describe what the system is DOING, which is observable, rather than
    what a model is thinking, which is not.

    No progress percentages either. Nothing in the graph produces one, so
    inventing a number would be a lie with a progress bar attached.

SAFETY
    ``_safe`` truncates and strips anything bound for a browser. Tool arguments
    and results come from model output and from a user's repository, so they
    are untrusted content (``docs/security.md`` 5) and never rendered raw or
    unbounded. No credential, path or raw provider payload is ever placed in an
    event.

Spec: docs/api.md 2.8; docs/security.md 5, 6; CLAUDE.md 15, 25.
"""

from __future__ import annotations

import logging
from typing import Any

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
)

from backend.schemas.state import ProjectState

logger = logging.getLogger(__name__)

# What the user reads while a node runs. Keyed by the graph's own node names,
# so a node that is renamed stops matching here instead of silently showing a
# stale label — and an unmapped node falls back to its own name rather than to
# an invented one.
ACTIVITY_LABELS: dict[str, str] = {
    "requirement_analysis": "Analyzing the request",
    "planning": "Planning the website",
    "plan_gate": "Waiting for human approval",
    "design": "Applying the design",
    "design_gate": "Waiting for human approval",
    "reference_analysis": "Reading the reference image",
    "reference_gate": "Waiting for human approval",
    "frontend_architecture": "Designing the component tree",
    "build": "Generating and building files",
    "preview": "Rendering the preview",
    "evaluation": "Running Impeccable quality checks",
    "targeted_fix": "Fixing detected issues",
    "re_evaluation": "Re-running quality checks",
    "final_validation": "Final validation",
    "final_validation_gate": "Waiting for human approval",
}

# A colour hint per activity class, so the timeline is consistent without the
# frontend duplicating this table. Values are semantic, not CSS.
ACTIVITY_TONE: dict[str, str] = {
    "plan_gate": "waiting",
    "design_gate": "waiting",
    "reference_gate": "waiting",
    "final_validation_gate": "waiting",
    "evaluation": "quality",
    "re_evaluation": "quality",
    "targeted_fix": "fixing",
    "build": "building",
    "preview": "building",
}

MAX_TEXT = 400


def label_for(node: str) -> str:
    return ACTIVITY_LABELS.get(node, node.replace("_", " ").capitalize())


def tone_for(node: str) -> str:
    return ACTIVITY_TONE.get(node, "running")


def _safe(value: Any, limit: int = MAX_TEXT) -> str:
    """One-line, bounded, display-safe text from untrusted input."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_started(project_id: str, run_id: str) -> RunStartedEvent:
    return RunStartedEvent(
        type=EventType.RUN_STARTED, thread_id=project_id, run_id=run_id
    )


def run_finished(project_id: str, run_id: str) -> RunFinishedEvent:
    return RunFinishedEvent(
        type=EventType.RUN_FINISHED, thread_id=project_id, run_id=run_id
    )


def run_error(message: str) -> RunErrorEvent:
    """A failure, in words safe to show. Never an exception string."""
    return RunErrorEvent(type=EventType.RUN_ERROR, message=_safe(message))


def step_started(node: str) -> StepStartedEvent:
    return StepStartedEvent(type=EventType.STEP_STARTED, step_name=node)


def step_finished(node: str) -> StepFinishedEvent:
    return StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=node)


def activity(node: str, detail: str = "") -> CustomEvent:
    """The readable timeline entry for a node.

    STEP_STARTED carries the node name, which is a graph fact. This carries the
    human label and the colour class, which are presentation. Keeping them in
    separate events means a client that only understands core AG-UI still gets
    a correct, if plainer, timeline.
    """
    return CustomEvent(
        type=EventType.CUSTOM,
        name="detroit.activity",
        value={
            "node": node,
            "label": label_for(node),
            "tone": tone_for(node),
            "detail": _safe(detail) if detail else "",
        },
    )


def state_snapshot(state: ProjectState) -> StateSnapshotEvent:
    """The run's public state, as the protocol's own snapshot event.

    A deliberately small projection, not the whole document: this crosses to a
    browser on every node, and ``ProjectState`` carries agent output that has
    its own routes and its own redaction rules.
    """
    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={
            "projectId": state.project_id,
            "stage": state.current_stage.value,
            "status": state.run_status.value,
            "iterationCount": state.iteration_count,
            "previewAvailable": state.preview_status.preview_url is not None,
            "awaitingCheckpointId": (
                state.pending_checkpoint.checkpoint_id
                if state.pending_checkpoint
                else None
            ),
            "lastError": (
                {"code": state.last_error.code, "message": state.last_error.message}
                if state.last_error
                else None
            ),
        },
    )


def hitl_waiting(state: ProjectState) -> CustomEvent:
    """The run has paused for a person. Carries what they are being asked."""
    checkpoint = state.pending_checkpoint
    return CustomEvent(
        type=EventType.CUSTOM,
        name="detroit.hitl",
        value={
            "checkpointId": checkpoint.checkpoint_id if checkpoint else None,
            "stage": checkpoint.stage.value if checkpoint else state.current_stage.value,
            "title": _safe(checkpoint.title) if checkpoint else "Review required",
            "summary": _safe(checkpoint.summary) if checkpoint else "",
            "optionCount": len(checkpoint.options) if checkpoint else 0,
        },
    )


def quality_gate(state: ProjectState) -> CustomEvent | None:
    """The Impeccable verdict, reported exactly as recorded.

    A BLOCKED gate is reported as blocked. ``CLAUDE.md`` 15 forbids ever
    presenting an unavailable or failed quality gate as a pass, and this event
    is the one the timeline renders, so it must carry the real verdict rather
    than a cheerful summary of it.
    """
    report = state.evaluation_results
    if report is None:
        return None
    return CustomEvent(
        type=EventType.CUSTOM,
        name="detroit.quality",
        value={
            "gateStatus": report.gate_status,
            "impeccableAvailable": report.impeccable_available,
            "findingCount": len(report.findings),
            "blockers": sum(1 for f in report.findings if f.severity == "blocker"),
            "iteration": state.iteration_count,
        },
    )


def from_node_update(node: str, state: ProjectState | None) -> list[BaseEvent]:
    """Every event one completed node should produce.

    Called once per ``astream`` update, so the order here is the order the
    browser sees: the step closes, then anything that node specifically means.
    """
    events: list[BaseEvent] = [step_finished(node)]
    if state is None:
        return events

    events.append(state_snapshot(state))

    if node in ("evaluation", "re_evaluation"):
        verdict = quality_gate(state)
        if verdict is not None:
            events.append(verdict)

    if state.last_error is not None and node not in ("evaluation", "re_evaluation"):
        events.append(
            CustomEvent(
                type=EventType.CUSTOM,
                name="detroit.stage_error",
                value={
                    "node": node,
                    "code": state.last_error.code,
                    "message": _safe(state.last_error.message),
                },
            )
        )
    return events
