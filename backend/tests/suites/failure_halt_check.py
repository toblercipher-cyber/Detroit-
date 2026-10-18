"""A failed stage must STOP the run, never open a HITL gate.

WHY THIS SUITE EXISTS
    Every node already recorded failure correctly -- ``_failure_update`` sets
    ``run_status = FAILED`` and a safe ``last_error``. But the edges BETWEEN
    stages were unconditional, so a failed stage still advanced into the next
    one and into that stage's gate. The gate node interrupts before any router
    runs, so ``route_after_gate``'s ``run_status is FAILED -> "stop"`` never got
    a chance to fire.

    Observed against a real run with an invalid provider key, before the fix:

        run_status : awaiting_hitl        (not failed)
        stage      : planning             (the planner never ran)
        GET /hitl  : 200
        summary    : "A plan is ready for review."
        question   : None     options: []

    The user was asked to approve a plan that did not exist, with nothing to
    approve it with, while the real cause sat unread in ``last_error``.

    This suite pins the corrected behaviour at every stage boundary, because
    the bug was not in any one node -- it was in the shape of the graph, and a
    future edge added with ``add_edge`` instead of ``_advance`` would silently
    reintroduce it.

NO NETWORK, NO API KEY. The failing model is a stub that raises.
"""

import os
import sys

sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True
os.environ.setdefault("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
os.environ.setdefault("PREVIEW_URL_SECRET", "test-only-preview-signing-key")

import asyncio
from pathlib import Path as _P

from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver  # TEST ONLY

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
from backend.graph.workflow import (
    WorkflowDependencies,
    build_workflow,
    halt_if_failed,
)
from backend.memory.artifacts import JsonArtifactRepository
from backend.schemas.enums import RunStatus, WorkflowStage
from backend.schemas.state import (
    DesignSpec,
    NormalizedRequirements,
    Plan,
    ProjectState,
)
from backend.tests.suites import _tmpdir
from backend.tools.build_runner import BuildRunner
from backend.tools.impeccable import ImpeccableStatus

_results: list[bool] = []


def ok(b: bool) -> str:
    _results.append(bool(b))
    return "PASS" if b else "**FAIL**"


class _Raises(Runnable):
    """A model whose call fails the way a bad credential does.

    ``AgentBase`` maps any non-timeout exception to ``PROVIDER_ERROR`` and
    returns immediately without retrying, which is exactly the path an invalid
    API key takes in production.
    """

    def invoke(self, i, config=None, **k):
        raise NotImplementedError

    def with_structured_output(self, model):
        class _S(Runnable):
            def invoke(s, i, config=None, **k):
                raise NotImplementedError

            async def ainvoke(s, i, config=None, **k):
                raise RuntimeError("simulated provider failure")

        return _S()

    def bind_tools(self, tools):
        class _T(Runnable):
            def invoke(s, i, config=None, **k):
                raise NotImplementedError

            async def ainvoke(s, i, config=None, **k):
                raise RuntimeError("simulated provider failure")

        return _T()


class _Returns(Runnable):
    """A model that succeeds, returning whatever type was asked for."""

    def __init__(self, value=None):
        self.value = value

    def invoke(self, i, config=None, **k):
        raise NotImplementedError

    def with_structured_output(self, model):
        value = self.value

        class _S(Runnable):
            def invoke(s, i, config=None, **k):
                raise NotImplementedError

            async def ainvoke(s, i, config=None, **k):
                if value is not None and isinstance(value, model):
                    return value
                return model()

        return _S()

    def bind_tools(self, tools):
        from langchain_core.messages import AIMessage

        class _T(Runnable):
            def invoke(s, i, config=None, **k):
                raise NotImplementedError

            async def ainvoke(s, i, config=None, **k):
                return AIMessage(content="no changes needed")

        return _T()


def build(*, failing: str):
    """A graph in which exactly one stage's model fails.

    ``failing`` names the stage: requirement_analysis, planning, design, or
    "none" for the control case.
    """

    def model_for(stage: str, value=None):
        return _Raises() if failing == stage else _Returns(value)

    deps = WorkflowDependencies(
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(
            model_for(
                "requirement_analysis",
                NormalizedRequirements(goal="sell coffee", input_density="sparse"),
            )
        ),
        planner=PlannerAgent(
            model_for(
                "planning",
                Plan(objective="a plan", pages=["/"], acceptance_criteria=["nav"]),
            )
        ),
        design_director=DesignDirectorAgent(
            model_for("design", DesignSpec(visual_tone="warm"))
        ),
        reference_analyzer=ReferenceAnalyzerAgent(_Returns()),
        frontend_architect=FrontendArchitectAgent(_Returns()),
        build_builder=lambda ws: BuilderAgent(_Returns(), ws),
        build_fixer=lambda ws: FixerAgent(_Returns(), ws),
        build_runner=BuildRunner(timeout_seconds=600),
        visual_ux_critic=VisualUxCriticAgent(_Returns()),
        anti_slop_reviewer=AntiSlopReviewerAgent(_Returns()),
        accessibility_reviewer=AccessibilityReviewerAgent(_Returns()),
        code_reviewer=CodeReviewerAgent(_Returns()),
        impeccable_probe=lambda: ImpeccableStatus(False, reason="not installed"),
        max_auto_iterations=3,
        repository=JsonArtifactRepository(_P(_tmpdir.mkdtemp(prefix="fh_"))),
    )
    return build_workflow(deps, checkpointer=InMemorySaver())


def run(graph, thread: str):
    cfg = {"configurable": {"thread_id": thread}}
    return asyncio.run(graph.ainvoke(ProjectState(user_request="coffee shop"), config=cfg))


def as_state(d):
    return ProjectState.model_validate(
        {k: v for k, v in d.items() if not k.startswith("__")}
    )


print("=== 1. halt_if_failed, in isolation ===")
failed = ProjectState(user_request="x", run_status=RunStatus.FAILED)
running = ProjectState(user_request="x", run_status=RunStatus.RUNNING)
print("   FAILED  -> stop    :", ok(halt_if_failed(failed) == "stop"))
print("   RUNNING -> continue:", ok(halt_if_failed(running) == "continue"))

print()
print("=== 2. Requirement Analysis fails -> run stops there ===")
out = run(build(failing="requirement_analysis"), "proj_" + "a" * 32)
st = as_state(out)
print("   run_status FAILED       :", st.run_status.value,
      ok(st.run_status is RunStatus.FAILED))
print("   stage is where it failed:", st.current_stage.value,
      ok(st.current_stage is WorkflowStage.REQUIREMENT_ANALYSIS))
print("   NO interrupt raised     :", ok("__interrupt__" not in out))
print("   NO pending checkpoint   :", ok(st.pending_checkpoint is None))
print("   nothing asked of a user :", ok(not st.hitl_history))
print("   last_error recorded     :", ok(st.last_error is not None
                                         and st.last_error.code == "upstream_error"))
print("   error names no exception detail:",
      ok(st.last_error is not None and "simulated" not in st.last_error.message))

print()
print("=== 3. Planning fails -> never reaches the plan gate ===")
out = run(build(failing="planning"), "proj_" + "b" * 32)
st = as_state(out)
print("   run_status FAILED    :", ok(st.run_status is RunStatus.FAILED))
print("   stage is planning    :", st.current_stage.value,
      ok(st.current_stage is WorkflowStage.PLANNING))
print("   NO interrupt raised  :", ok("__interrupt__" not in out))
print("   NO pending checkpoint:", ok(st.pending_checkpoint is None))
# The exact symptom from the bug report: a gate claiming a plan is ready.
print("   no plan was invented :", ok(st.plan is None))

print()
print("=== 4. Design fails -> never reaches the design gate ===")
g = build(failing="design")
cfg = {"configurable": {"thread_id": "proj_" + "c" * 32}}
out = asyncio.run(g.ainvoke(ProjectState(user_request="coffee shop"), config=cfg))
# Approve the plan gate so the run advances into Design, which then fails.
from langgraph.types import Command  # noqa: E402

payload = out["__interrupt__"][0].value
out2 = asyncio.run(
    g.ainvoke(
        Command(resume={"checkpoint_id": payload["checkpoint_id"], "action": "approve"}),
        config=cfg,
    )
)
st = as_state(out2)
print("   plan gate did open   :", ok(payload.get("stage") == "planning"))
print("   then design failed   :", ok(st.run_status is RunStatus.FAILED))
print("   stage is design      :", st.current_stage.value,
      ok(st.current_stage is WorkflowStage.DESIGN))
print("   NO second interrupt  :", ok("__interrupt__" not in out2))
print("   NO pending checkpoint:", ok(st.pending_checkpoint is None))
print("   only ONE gate answered:", ok(len(st.hitl_history) == 1))

print()
print("=== 5. CONTROL: a healthy run still pauses for review ===")
# Without this, "nothing ever interrupts" would pass every check above.
out = run(build(failing="none"), "proj_" + "d" * 32)
intr = out.get("__interrupt__")
print("   gate opened          :", ok(bool(intr)))
print("   at the planning stage:", ok(bool(intr) and intr[0].value.get("stage") == "planning"))
print("   run NOT failed       :", ok(as_state(out).run_status is not RunStatus.FAILED))
print("   gate carries a summary:",
      ok(bool(intr) and bool(intr[0].value.get("summary"))))

print()
print("=== 6. Every stage-to-stage edge is failure-aware ===")
# Guards the shape of the graph rather than one path through it: an edge added
# with add_edge instead of _advance would reintroduce the bug silently.
import inspect  # noqa: E402

import backend.graph.workflow as wf  # noqa: E402

src = inspect.getsource(wf.build_workflow)
unconditional = [
    line.strip()
    for line in src.splitlines()
    if "graph.add_edge(" in line and "START" not in line
]
print("   unconditional stage edges:", unconditional, ok(not unconditional))

passed, total = sum(_results), len(_results)
print()
print(f"=== {passed}/{total} assertions passed ===")
sys.exit(0 if passed == total else 1)
