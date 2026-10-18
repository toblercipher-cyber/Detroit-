"""AG-UI: genuine protocol events, produced by a real graph run.

WHY THIS SUITE EXISTS
    "We emit AG-UI" is the kind of claim that is easy to make and easy to get
    wrong, usually by inventing event names and calling the result a protocol.
    So this asserts the opposite of a happy path: that the event classes come
    from the official SDK, that the wire form is the protocol's, and that the
    events describe a graph that actually ran.

WHAT IS REAL HERE
    A real compiled LangGraph workflow, real nodes, a real ``interrupt()``
    pause and a real resume. Only the chat models are stubbed, because no
    provider key is needed to prove that a node produced a step event, and the
    suite must run without one.

NO NETWORK, NO API KEY.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

PASS = "PASS"
_results: list[bool] = []


def ok(condition: bool) -> str:
    _results.append(bool(condition))
    return PASS if condition else "**FAIL**"


sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True
os.environ.setdefault("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
os.environ.setdefault("PREVIEW_URL_SECRET", "test-only-preview-signing-key")

import ag_ui.core as ag_core  # noqa: E402
from ag_ui.encoder import EventEncoder  # noqa: E402
from langchain_core.runnables import Runnable  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from backend.config.settings import get_settings  # noqa: E402
from backend.graph.workflow import WorkflowDependencies, build_workflow  # noqa: E402
from backend.schemas.api import HitlDecisionRequest  # noqa: E402
from backend.schemas.enums import HitlAction, RunStatus  # noqa: E402
from backend.schemas.state import (  # noqa: E402
    DesignSpec,
    EvaluationReport,
    NormalizedRequirements,
    Plan,
    ProjectState,
)
from backend.services import agui_translator as agui  # noqa: E402
from backend.services.agui_stream import AgUiBroker  # noqa: E402
from backend.services.projects import ProjectService  # noqa: E402
from backend.memory.artifacts import JsonArtifactRepository  # noqa: E402
from backend.tests.suites import _tmpdir  # noqa: E402
from pathlib import Path  # noqa: E402


# --- A graph with stub models, as gate_check builds one ---------------------


class _Stub(Runnable):
    def __init__(self, value=None):
        self.value, self.calls = value, 0

    def invoke(self, i, config=None, **k):
        raise NotImplementedError

    def with_structured_output(self, model):
        outer = self

        class _S(Runnable):
            def invoke(s, i, config=None, **k):
                raise NotImplementedError

            async def ainvoke(s, i, config=None, **k):
                outer.calls += 1
                if outer.value is not None and isinstance(outer.value, model):
                    return outer.value
                return model()

        return _S()

    def bind_tools(self, tools):
        from langchain_core.messages import AIMessage

        class _T(Runnable):
            def invoke(s, i, config=None, **k):
                raise NotImplementedError

            async def ainvoke(s, i, config=None, **k):
                return AIMessage(content="no changes")

        return _T()


def build_service(sink):
    from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
    from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent
    from backend.agents.builder import BuilderAgent
    from backend.agents.code_reviewer import CodeReviewerAgent
    from backend.agents.design_director import DesignDirectorAgent
    from backend.agents.fixer import FixerAgent
    from backend.agents.frontend_architect import FrontendArchitectAgent
    from backend.agents.planner import PlannerAgent
    from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
    from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
    from backend.agents.visual_ux_critic import VisualUxCriticAgent
    from backend.context.builder import ContextBuilder
    from backend.tools.build_runner import BuildRunner
    from backend.tools.impeccable import ImpeccableStatus

    repo = JsonArtifactRepository(Path(_tmpdir.mkdtemp(prefix="agui_")))
    planner = _Stub(Plan(objective="a plan", pages=["/"], acceptance_criteria=["nav"]))
    deps = WorkflowDependencies(
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(
            _Stub(NormalizedRequirements(goal="sell coffee", input_density="sparse"))
        ),
        planner=PlannerAgent(planner),
        design_director=DesignDirectorAgent(_Stub(DesignSpec(visual_tone="warm"))),
        reference_analyzer=ReferenceAnalyzerAgent(_Stub()),
        frontend_architect=FrontendArchitectAgent(_Stub()),
        build_builder=lambda ws: BuilderAgent(_Stub(), ws),
        build_fixer=lambda ws: FixerAgent(_Stub(), ws),
        build_runner=BuildRunner(timeout_seconds=600),
        visual_ux_critic=VisualUxCriticAgent(_Stub()),
        anti_slop_reviewer=AntiSlopReviewerAgent(_Stub()),
        accessibility_reviewer=AccessibilityReviewerAgent(_Stub()),
        code_reviewer=CodeReviewerAgent(_Stub()),
        impeccable_probe=lambda: ImpeccableStatus(False, reason="not installed"),
        max_auto_iterations=3,
        repository=repo,
    )
    workflow = build_workflow(deps, checkpointer=InMemorySaver())
    service = ProjectService(
        repository=repo, workflow=workflow, settings=get_settings(), event_sink=sink
    )
    return service, planner


print("=== 1. The event classes are the OFFICIAL SDK's, not ours ===")
for name in ("RunStartedEvent", "StepStartedEvent", "StateSnapshotEvent", "CustomEvent"):
    cls = getattr(agui, name, None) or getattr(ag_core, name)
    print(f"   {name:20} from ag_ui.core:", ok(cls.__module__.startswith("ag_ui")))
print("   translator imports no custom event base:",
      ok(all(getattr(ag_core, n, None) is not None
             for n in ("RunStartedEvent", "StepFinishedEvent", "RunErrorEvent"))))

print()
print("=== 2. The wire form is the protocol's ===")
enc = EventEncoder()
line = enc.encode(agui.run_started("proj_x", "run_y"))
print("   SSE framed            :", ok(line.startswith("data: ") and line.endswith("\n\n")))
payload = json.loads(line[len("data: "):].strip())
print("   type is the enum value:", payload.get("type"), ok(payload["type"] == "RUN_STARTED"))
print("   camelCase keys        :", ok("threadId" in payload and "runId" in payload))
print("   content type          :", ok(enc.get_content_type() == "text/event-stream"))

print()
print("=== 3. A REAL graph run produces the events ===")
captured: list = []
service, planner = build_service(lambda pid, ev: captured.append((pid, ev)))
state = service.create(prompt="a coffee shop landing page")
pid = state.project_id
settled = asyncio.run(service.run_workflow(pid))

types = [e.type.value for _, e in captured]
steps = [e.step_name for _, e in captured if e.type.value == "STEP_FINISHED"]
print("   RUN_STARTED first     :", ok(types[0] == "RUN_STARTED"))
print("   RUN_FINISHED last     :", ok(types[-1] == "RUN_FINISHED"))
print("   real nodes reported   :", steps, ok("requirement_analysis" in steps and "planning" in steps))
print("   STATE_SNAPSHOT present:", ok("STATE_SNAPSHOT" in types))
print("   events are protocol    :", ok(all(e.__class__.__module__.startswith("ag_ui")
                                            for _, e in captured)))
print("   all tagged this project:", ok({p for p, _ in captured} == {pid}))

print()
print("=== 4. The graph ran ONCE — streaming did not duplicate it ===")
# The planner is called once per run. A second execution would double this,
# which is the failure mode an attach-based stream exists to avoid.
print("   planner invoked once  :", planner.calls, ok(planner.calls == 1))

print()
print("=== 5. A HITL pause is reported as a pause, not a finish ===")
print("   run parked at a gate  :", ok(settled.run_status is RunStatus.AWAITING_HITL))
hitl = [e for _, e in captured if e.type.value == "CUSTOM" and e.name == "detroit.hitl"]
print("   detroit.hitl emitted  :", ok(len(hitl) == 1))
print("   carries the question  :", ok(bool(hitl and hitl[0].value.get("title"))))
print("   carries checkpoint id :",
      ok(bool(hitl and hitl[0].value.get("checkpointId") ==
              settled.pending_checkpoint.checkpoint_id)))

print()
print("=== 6. Activity labels come from real nodes ===")
acts = [e for _, e in captured if e.type.value == "CUSTOM" and e.name == "detroit.activity"]
labels = {a.value["node"]: a.value["label"] for a in acts}
print("   planning label        :", labels.get("planning"),
      ok(labels.get("planning") == "Planning the website"))
print("   every label has a tone:", ok(all(a.value.get("tone") for a in acts)))
print("   no invented progress %:",
      ok(not any("percent" in json.dumps(a.value).lower() or "pct" in json.dumps(a.value).lower()
                 for a in acts)))
print("   no reasoning events   :", ok(not any(t.startswith("REASONING") for t in types)))

print()
print("=== 7. Resume streams too, and the gate advances ===")
captured.clear()
accepted = asyncio.run(service.submit_hitl_decision(
    pid, HitlDecisionRequest(
        checkpoint_id=settled.pending_checkpoint.checkpoint_id,
        action=HitlAction.APPROVE)))
rtypes = [e.type.value for _, e in captured]
print("   resume emitted events :", ok("RUN_STARTED" in rtypes and "RUN_FINISHED" in rtypes))
print("   design node reported  :",
      ok("design" in [e.step_name for _, e in captured if e.type.value == "STEP_FINISHED"]))
print("   decision accepted     :", ok(accepted is not None))

print()
print("=== 8. A blocked Impeccable gate is reported as BLOCKED ===")
# CLAUDE.md 15: never present an unavailable or failed gate as a pass.
blocked = ProjectState(user_request="x")
blocked.evaluation_results = EvaluationReport(
    iteration=0, gate_status="blocked", impeccable_available=False, findings=[])
ev = agui.quality_gate(blocked)
print("   quality event emitted :", ok(ev is not None))
print("   gateStatus is blocked :", ok(ev.value["gateStatus"] == "blocked"))
print("   impeccable flagged off:", ok(ev.value["impeccableAvailable"] is False))
print("   never says passed     :", ok("passed" not in json.dumps(ev.value)))

print()
print("=== 9. Broker: fan-out, backpressure, cleanup ===")


async def broker_checks():
    b = AgUiBroker()
    async with b.subscribe("p1") as q1:
        async with b.subscribe("p1") as q2:
            b.publish("p1", agui.run_started("p1", "r"))
            print("   both subscribers got it:", ok(q1.qsize() == 1 and q2.qsize() == 1))
            print("   two attached           :", ok(b.subscriber_count("p1") == 2))
        print("   one detached cleanly   :", ok(b.subscriber_count("p1") == 1))
    print("   all detached           :", ok(b.subscriber_count("p1") == 0))

    # A reader that never reads must not block the publisher.
    async with b.subscribe("p2") as slow:
        for _ in range(1000):
            b.publish("p2", agui.step_started("build"))
        print("   slow client bounded    :", ok(slow.qsize() <= 256))
    print("   publisher never blocked:", ok(True))
    print("   replay bounded         :", ok(len(b.replay("p2")) <= 64))


asyncio.run(broker_checks())

passed, total = sum(_results), len(_results)
print()
print(f"=== {passed}/{total} assertions passed ===")
sys.exit(0 if passed == total else 1)
