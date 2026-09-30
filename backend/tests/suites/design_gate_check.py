"""Design stage + generalised gate. Real LangGraph, stub models. No network."""
import os, sys, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver   # TEST ONLY
from langgraph.types import Command
from backend.graph.workflow import (build_workflow, WorkflowDependencies,
                                    NODE_PLANNING, NODE_PLAN_GATE, NODE_DESIGN,
                                    NODE_DESIGN_GATE)
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.context.builder import ContextBuilder
from backend.schemas.state import (ProjectState, NormalizedRequirements, Plan,
                                   DesignSpec)
from backend.schemas.enums import HitlAction, RunStatus, WorkflowStage

# --- WorkflowDependencies gained reference_analysis collaborators ---
# Self-contained: it must not depend on whatever stub shape this suite uses.
import tempfile as _tf
from pathlib import Path as _P
from langchain_core.runnables import Runnable as _R
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent as _RAA
from backend.agents.frontend_architect import FrontendArchitectAgent as _FAA
from backend.memory.artifacts import JsonArtifactRepository as _JAR
from backend.schemas.state import ReferenceAnalysis as _RA


class _RefStub(_R):
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        # Return the model that was ASKED for. Every field on these outputs is
        # optional, so an empty instance is valid -- and returning a fixed type
        # would hand one node another node's output.
        class _S(_R):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                return model()
        return _S()

    def bind_tools(self, tools):
        # The Builder is a tool-calling agent. This stub writes nothing and
        # returns immediately, which is enough for the topology tests here --
        # build_runner_check covers the real compile path.
        from langchain_core.messages import AIMessage
        class _T(_R):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                return AIMessage(content="no changes needed")
        return _T()


def _ref_deps():
    """Every collaborator WorkflowDependencies requires beyond the core three."""
    from backend.agents.builder import BuilderAgent as _BA
    from backend.tools.build_runner import BuildRunner as _BR
    return dict(reference_analyzer=_RAA(_RefStub()),
                frontend_architect=_FAA(_RefStub()),
                build_builder=lambda ws: _BA(_RefStub(), ws),
                build_runner=_BR(timeout_seconds=600),
                repository=_JAR(_P(_tf.mkdtemp(prefix="wfdeps_"))))


ok = lambda b: "PASS" if b else "**FAIL**"


def as_state(d):
    return ProjectState.model_validate({k: v for k, v in d.items() if not k.startswith("__")})


class StubOut(Runnable):
    def invoke(self, input, config=None, **k):
        raise NotImplementedError
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    async def ainvoke(self, m, config=None, **k):
        self.calls += 1; self.last = m.to_messages() if hasattr(m, "to_messages") else m
        return self.v() if callable(self.v) else self.v


class StubModel:
    def __init__(self, v): self.inner = StubOut(v)
    def with_structured_output(self, m): return self.inner


design_n = {"n": 0}


def make_design():
    design_n["n"] += 1
    return DesignSpec(font_families=["Inter"], visual_tone=f"tone v{design_n['n']}")


def fresh():
    design_n["n"] = 0
    ra = StubModel(NormalizedRequirements(goal="sell coffee", input_density="sparse"))
    pl = StubModel(Plan(objective="a coffee shop landing page", pages=["/"]))
    dd = StubModel(make_design)
    deps = WorkflowDependencies(**_ref_deps(), context_builder=ContextBuilder(),
                                requirement_analyzer=RequirementAnalyzerAgent(ra),
                                planner=PlannerAgent(pl),
                                design_director=DesignDirectorAgent(dd))
    return build_workflow(deps, checkpointer=InMemorySaver()), pl, dd


print("=== 1. Topology now includes the design stage ===")
g, _, _ = fresh()
nodes = sorted(n for n in g.get_graph().nodes if not n.startswith("__"))
print("   nodes:", nodes)
print("   expected 10:", ok(set(nodes) == {NODE_PLANNING, NODE_PLAN_GATE, NODE_DESIGN,
                                           NODE_DESIGN_GATE, "requirement_analysis",
                                           "reference_analysis", "reference_gate",
                                           "frontend_architecture", "build",
                                           "preview"}))

print()
print("=== 2. Plan approval now advances to design (not END) ===")
g, pl, dd = fresh()
cfg = {"configurable": {"thread_id": "proj_" + "aa" * 16}}
r = asyncio.run(g.ainvoke(ProjectState(user_request="coffee"), config=cfg))
cp = r["__interrupt__"][0].value
print("   first gate stage:", cp["stage"], ok(cp["stage"] == "planning"))
r2 = asyncio.run(g.ainvoke(Command(resume={"checkpoint_id": cp["checkpoint_id"],
                                           "action": "approve"}), config=cfg))
print("   paused again    :", ok("__interrupt__" in r2))
cp2 = r2["__interrupt__"][0].value
print("   second gate     :", cp2["stage"], ok(cp2["stage"] == "design"))
print("   result_ref      :", cp2["result_ref"], ok(cp2["result_ref"] == "design_spec"))
print("   summary is the design tone:", cp2["summary"], ok(cp2["summary"] == "tone v1"))
print("   design ran once :", ok(dd.inner.calls == 1))
st2 = as_state(r2)
print("   design_spec set :", ok(st2.design_spec is not None))
print("   plan approval recorded:", ok(len(st2.approved_decisions) == 1))

print()
print("=== 3. Design CUSTOM re-runs design only, not planning ===")
r3 = asyncio.run(g.ainvoke(Command(resume={"checkpoint_id": cp2["checkpoint_id"],
                                           "action": "custom",
                                           "instruction": "warmer palette"}), config=cfg))
print("   paused again   :", ok("__interrupt__" in r3))
print("   design re-ran  :", dd.inner.calls, ok(dd.inner.calls == 2))
print("   planner NOT re-run:", pl.inner.calls, ok(pl.inner.calls == 1))
human = dd.inner.last[1].content
print("   instruction reached the design prompt:", ok("warmer palette" in str(human)))
st3 = as_state(r3)
print("   design replaced, not stacked:", ok(st3.design_spec.visual_tone == "tone v2"))

print()
print("=== 4. Per-stage instruction keys do not collide ===")
print("   instruction still held while paused (cleared by the next decision):",
      ok(st3.node_scratch.get("design_instruction") == "warmer palette"))
print("   planning_instruction absent            :",
      ok("planning_instruction" not in st3.node_scratch))

print()
print("=== 5. Design approval completes the run ===")
cp3 = r3["__interrupt__"][0].value
r4 = asyncio.run(g.ainvoke(Command(resume={"checkpoint_id": cp3["checkpoint_id"],
                                           "action": "approve"}), config=cfg))
st4 = as_state(r4)
print("   finished        :", ok("__interrupt__" not in r4))
print("   hitl history    :", [h.action.value for h in st4.hitl_history],
      ok([h.action.value for h in st4.hitl_history] == ["approve", "custom", "approve"]))
print("   two approvals recorded:", ok(len(st4.approved_decisions) == 2))
stages = [d.stage.value for d in st4.approved_decisions]
print("   approval stages :", stages, ok(stages == ["planning", "design"]))
print("   design approval names design_spec:",
      ok("design_spec" in st4.approved_decisions[1].statement))

print()
print("=== 6. Design REJECT loops design, approves nothing ===")
g2, pl2, dd2 = fresh()
cfg2 = {"configurable": {"thread_id": "proj_" + "bb" * 16}}
a = asyncio.run(g2.ainvoke(ProjectState(user_request="x"), config=cfg2))
a2 = asyncio.run(g2.ainvoke(Command(resume={"checkpoint_id": a["__interrupt__"][0].value["checkpoint_id"],
                                            "action": "approve"}), config=cfg2))
a3 = asyncio.run(g2.ainvoke(Command(resume={"checkpoint_id": a2["__interrupt__"][0].value["checkpoint_id"],
                                            "action": "reject"}), config=cfg2))
sa = as_state(a3)
print("   paused again  :", ok("__interrupt__" in a3))
print("   design re-ran :", ok(dd2.inner.calls == 2))
print("   still only the plan approval:", ok(len(sa.approved_decisions) == 1))

print()
print("=== 7. Malformed decision at the design gate stops, does not loop ===")
g3, _, dd3 = fresh()
cfg3 = {"configurable": {"thread_id": "proj_" + "cc" * 16}}
b = asyncio.run(g3.ainvoke(ProjectState(user_request="x"), config=cfg3))
b2 = asyncio.run(g3.ainvoke(Command(resume={"checkpoint_id": b["__interrupt__"][0].value["checkpoint_id"],
                                            "action": "approve"}), config=cfg3))
before = dd3.inner.calls
b3 = asyncio.run(g3.ainvoke(Command(resume={"nonsense": True}), config=cfg3))
sb = as_state(b3)
print("   run_status FAILED:", ok(sb.run_status is RunStatus.FAILED))
print("   design NOT re-run:", ok(dd3.inner.calls == before))
print("   checkpoint kept  :", ok(sb.pending_checkpoint is not None))
print("   stage recorded   :", sb.current_stage.value, ok(sb.current_stage is WorkflowStage.DESIGN))
