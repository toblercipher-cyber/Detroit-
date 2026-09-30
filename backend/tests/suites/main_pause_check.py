"""Proves the REAL composition root reaches a HITL pause. No network."""
import os, sys, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

import langchain_anthropic
from langchain_core.runnables import Runnable
from backend.schemas.state import ProjectState, NormalizedRequirements, Plan, DesignSpec

REAL = langchain_anthropic.ChatAnthropic
CALLS = []

class FakeChat(Runnable):
    """Records the per-role kwargs the composition root passed, invokes nothing."""
    def __init__(self, **kw): self.kw = kw; CALLS.append(kw)
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        out = {NormalizedRequirements: NormalizedRequirements(goal="g", input_density="sparse"),
               Plan: Plan(objective="a coffee shop landing page"),
               DesignSpec: DesignSpec(font_families=["Inter"], visual_tone="warm")}[model]
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k): return out
        return _S()

langchain_anthropic.ChatAnthropic = FakeChat
import backend.main as m
from backend.config.settings import get_settings
svc = m.build_project_service(get_settings())

print("=== 1. Composition root built three clients, one per role ===")
print("   clients built:", len(CALLS), ok(len(CALLS) == 3))
temps = [c["temperature"] for c in CALLS]
toks = [c["max_tokens"] for c in CALLS]
print("   temperatures :", temps, ok(temps == [0.0, 0.4, 0.6]))
print("   max_tokens   :", toks, ok(toks == [4000, 8000, 8000]))
print("   key not echoed in kwargs repr:",
      ok("test-only-not-a-real-key" not in str({k: v for k, v in CALLS[0].items() if k != "api_key"})))

print()
print("=== 2. The real graph pauses at the plan gate (B4 fixed) ===")
cfg = {"configurable": {"thread_id": "proj_" + "ab" * 16}}
r = asyncio.run(svc._workflow.ainvoke(ProjectState(user_request="coffee shop"), config=cfg))
paused = "__interrupt__" in r
print("   paused           :", ok(paused))
if paused:
    v = r["__interrupt__"][0].value
    print("   gate stage       :", v["stage"], ok(v["stage"] == "planning"))
    print("   result_ref       :", v["result_ref"], ok(v["result_ref"] == "plan"))

print()
print("=== 3. Resume works, reaching the design gate ===")
from langgraph.types import Command
r2 = asyncio.run(svc._workflow.ainvoke(
    Command(resume={"checkpoint_id": r["__interrupt__"][0].value["checkpoint_id"],
                    "action": "approve"}), config=cfg))
print("   paused again     :", ok("__interrupt__" in r2))
print("   second gate      :", r2["__interrupt__"][0].value["stage"],
      ok(r2["__interrupt__"][0].value["stage"] == "design"))

print()
print("=== 4. Same graph with checkpointer=None would fail (the bug that was there) ===")
from backend.graph.workflow import build_workflow, WorkflowDependencies
from backend.context.builder import ContextBuilder
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
g = build_workflow(WorkflowDependencies(
    context_builder=ContextBuilder(),
    requirement_analyzer=RequirementAnalyzerAgent(FakeChat()),
    planner=PlannerAgent(FakeChat()),
    design_director=DesignDirectorAgent(FakeChat())), checkpointer=None)
# Measured behaviour: the FIRST pause still works without a checkpointer; it is
# the RESUME that fails. A run therefore looks healthy and is unanswerable.
from langgraph.types import Command as _Cmd
cfg2 = {"configurable": {"thread_id": "proj_" + "cd" * 16}}
rn = asyncio.run(g.ainvoke(ProjectState(user_request="x"), config=cfg2))
print("   pauses even with None:", ok("__interrupt__" in rn), "(not a raise)")
try:
    asyncio.run(g.ainvoke(_Cmd(resume={"checkpoint_id": "x", "action": "approve"}), config=cfg2))
    print("   resume without checkpointer:", "**FAIL** (expected RuntimeError)")
except RuntimeError as e:
    print("   resume raises RuntimeError:", ok("without checkpointer" in str(e)))

langchain_anthropic.ChatAnthropic = REAL
