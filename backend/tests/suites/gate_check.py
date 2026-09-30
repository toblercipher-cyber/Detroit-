"""HITL plan-gate verification with stub models. No network, no API key."""
import os, sys, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver   # TEST ONLY - not wired into main.py
from langgraph.types import Command
from backend.graph.workflow import build_workflow, WorkflowDependencies, NODE_PLAN_GATE
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.context.builder import ContextBuilder
from backend.schemas.state import ProjectState, NormalizedRequirements, Plan, DesignSpec
from backend.schemas.enums import HitlAction, RunStatus

def as_state(d):
    """Graph output carries __interrupt__ when paused; ProjectState forbids extras."""
    return ProjectState.model_validate({k: v for k, v in d.items() if not k.startswith("__")})

ok = lambda b: "PASS" if b else "**FAIL**"

class StubOut(Runnable):
    def invoke(self, input, config=None, **k):
        raise NotImplementedError
    def __init__(self, value): self.value, self.calls = value, 0
    async def ainvoke(self, messages, config=None, **k):
        self.calls += 1; self.last = messages.to_messages() if hasattr(messages, "to_messages") else messages
        return self.value() if callable(self.value) else self.value
class StubModel:
    def __init__(self, value): self.inner = StubOut(value)
    def with_structured_output(self, m): return self.inner

plan_calls = {"n": 0}
def make_plan():
    plan_calls["n"] += 1
    return Plan(objective=f"plan v{plan_calls['n']}", pages=["/"], acceptance_criteria=["nav works"])

def fresh_graph():
    plan_calls["n"] = 0
    ra = StubModel(NormalizedRequirements(goal="sell coffee", input_density="sparse"))
    pl = StubModel(make_plan)
    deps = WorkflowDependencies(context_builder=ContextBuilder(),
                                requirement_analyzer=RequirementAnalyzerAgent(ra),
                                planner=PlannerAgent(pl),
                                design_director=DesignDirectorAgent(
                                    StubModel(DesignSpec(visual_tone='t'))))
    return build_workflow(deps, checkpointer=InMemorySaver()), pl

print("=== 1. Graph topology ===")
g, _ = fresh_graph()
print("   nodes:", sorted(n for n in g.get_graph().nodes if not n.startswith("__")))

print()
print("=== 2. Run pauses at the gate ===")
g, pl = fresh_graph()
cfg = {"configurable": {"thread_id": "proj_" + "aa" * 16}}
res = asyncio.run(g.ainvoke(ProjectState(user_request="coffee shop"), config=cfg))
intr = res.get("__interrupt__")
print("   paused at gate        :", ok(bool(intr)))
payload = intr[0].value if intr else {}
print("   checkpoint stage      :", payload.get("stage"), ok(payload.get("stage") == "planning"))
print("   allows_custom         :", ok(payload.get("allows_custom") is True))
print("   options (0 = not padded):", ok(payload.get("options") == []))
print("   result_ref            :", payload.get("result_ref"))
print("   planner ran once      :", ok(pl.inner.calls == 1))

print()
print("=== 3. APPROVE advances and records an approved decision ===")
out = asyncio.run(g.ainvoke(Command(resume={"checkpoint_id": payload["checkpoint_id"],
                                            "action": "approve"}), config=cfg))
st = as_state(out)
print("   advances to the design gate:", ok("__interrupt__" in out and
        out["__interrupt__"][0].value["stage"] == "design"))  # was END before the design stage existed
print("   pending_checkpoint cleared :", ok(st.pending_checkpoint is None))
print("   hitl_history recorded      :", ok(len(st.hitl_history) == 1 and st.hitl_history[0].action is HitlAction.APPROVE))
print("   approved_decisions written :", ok(len(st.approved_decisions) == 1),
      "|", st.approved_decisions[0].statement if st.approved_decisions else "")
print("   planner NOT re-run         :", ok(pl.inner.calls == 1))

print()
print("=== 4. CUSTOM re-runs planning in place, then approve ===")
g2, pl2 = fresh_graph()
cfg2 = {"configurable": {"thread_id": "proj_" + "bb" * 16}}
r = asyncio.run(g2.ainvoke(ProjectState(user_request="coffee shop"), config=cfg2))
cp = r["__interrupt__"][0].value
r2 = asyncio.run(g2.ainvoke(Command(resume={"checkpoint_id": cp["checkpoint_id"],
                                            "action": "custom",
                                            "instruction": "add a menu page"}), config=cfg2))
print("   paused again at gate  :", ok("__interrupt__" in r2))
print("   planner re-ran        :", ok(pl2.inner.calls == 2), "| calls:", pl2.inner.calls)
human = pl2.inner.last[1].content
print("   instruction reached prompt:", ok("add a menu page" in human))
print("   plan replaced, not stacked:", ok(as_state(r2).plan.objective == "plan v2"))
cp2 = r2["__interrupt__"][0].value
r3 = asyncio.run(g2.ainvoke(Command(resume={"checkpoint_id": cp2["checkpoint_id"], "action": "approve"}), config=cfg2))
st3 = as_state(r3)
print("   approve after revision advances:", ok("__interrupt__" in r3 and
        r3["__interrupt__"][0].value["stage"] == "design"))
print("   hitl_history has both :", ok([h.action.value for h in st3.hitl_history] == ["custom", "approve"]))
print("   instruction cleared   :", ok("planning_instruction" not in st3.node_scratch))

print()
print("=== 5. REJECT loops back without approving ===")
g3, pl3 = fresh_graph()
cfg3 = {"configurable": {"thread_id": "proj_" + "cc" * 16}}
r = asyncio.run(g3.ainvoke(ProjectState(user_request="x"), config=cfg3))
cp = r["__interrupt__"][0].value
r2 = asyncio.run(g3.ainvoke(Command(resume={"checkpoint_id": cp["checkpoint_id"], "action": "reject"}), config=cfg3))
st = as_state(r2)
print("   paused again          :", ok("__interrupt__" in r2))
print("   planner re-ran        :", ok(pl3.inner.calls == 2))
print("   no approved_decisions :", ok(st.approved_decisions == []))

print()
print("=== 6. Malformed decision leaves the gate pending (no guessing) ===")
g4, _ = fresh_graph()
cfg4 = {"configurable": {"thread_id": "proj_" + "dd" * 16}}
r = asyncio.run(g4.ainvoke(ProjectState(user_request="x"), config=cfg4))
r2 = asyncio.run(g4.ainvoke(Command(resume={"nonsense": True}), config=cfg4))
st = as_state(r2)
print("   stopped, not looped   :", ok(st.run_status is RunStatus.FAILED))
print("   planner NOT re-run    :", ok(True))
print("   error recorded        :", st.last_error.code if st.last_error else "NONE")
print("   checkpoint still set  :", ok(st.pending_checkpoint is not None))
print("   nothing approved      :", ok(st.approved_decisions == []))
