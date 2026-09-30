"""run_workflow across pause / resume / complete / fail. Real graph, no network."""
import os, sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from backend.schemas.state import (ProjectState, NormalizedRequirements, Plan,
                                   DesignSpec, HitlCheckpoint)
from backend.schemas.enums import RunStatus, WorkflowStage
from backend.services.projects import ProjectService, _state_from_result
from backend.memory.artifacts import JsonArtifactRepository
from backend.graph.workflow import build_workflow, WorkflowDependencies
from backend.context.builder import ContextBuilder
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.config.settings import get_settings

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


def _ref_deps():
    """The two fields every WorkflowDependencies now requires."""
    return dict(reference_analyzer=_RAA(_RefStub()),
                frontend_architect=_FAA(_RefStub()),
                repository=_JAR(_P(_tf.mkdtemp(prefix="wfdeps_"))))



class FakeChat(Runnable):
    def __init__(self, **kw): pass
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        out = {NormalizedRequirements: NormalizedRequirements(goal="g", input_density="sparse"),
               Plan: Plan(objective="a coffee shop landing page"),
               DesignSpec: DesignSpec(font_families=["Inter"], visual_tone="warm")}[model]
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k): return out
        return _S()


def fresh_service(tmp):
    g = build_workflow(WorkflowDependencies(**_ref_deps(), 
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(FakeChat()),
        planner=PlannerAgent(FakeChat()),
        design_director=DesignDirectorAgent(FakeChat())), checkpointer=InMemorySaver())
    return ProjectService(repository=JsonArtifactRepository(Path(tmp)),
                          workflow=g, settings=get_settings())


tmp = tempfile.mkdtemp(prefix="svc_check_")
svc = fresh_service(tmp)

print("=== 1. A paused run no longer raises ===")
st = svc.create(prompt="build a coffee shop landing page")
pid = st.project_id
try:
    out = asyncio.run(svc.run_workflow(pid))
    print("   returned without raising:", ok(True))
except Exception as e:
    print("   RAISED:", type(e).__name__, str(e)[:90], "**FAIL**"); raise SystemExit(1)

print("   run_status       :", out.run_status.value, ok(out.run_status is RunStatus.AWAITING_HITL))
print("   pending_checkpoint set:", ok(out.pending_checkpoint is not None))
print("   checkpoint stage :", out.pending_checkpoint.stage.value,
      ok(out.pending_checkpoint.stage is WorkflowStage.PLANNING))
print("   result_ref       :", out.pending_checkpoint.result_ref, ok(out.pending_checkpoint.result_ref == "plan"))
print("   plan was produced:", ok(out.plan is not None))

print()
print("=== 2. It was PERSISTED, not just returned (survives a reload) ===")
re_read = svc.get(pid)
print("   reloaded status  :", re_read.run_status.value, ok(re_read.run_status is RunStatus.AWAITING_HITL))
print("   reloaded ckpt id matches:",
      ok(re_read.pending_checkpoint is not None
         and re_read.pending_checkpoint.checkpoint_id == out.pending_checkpoint.checkpoint_id))

print()
print("=== 3. Resume via the graph, then the second gate is recorded ===")
cfg = {"configurable": {"thread_id": pid}}
r2 = asyncio.run(svc._workflow.ainvoke(
    Command(resume={"checkpoint_id": out.pending_checkpoint.checkpoint_id, "action": "approve"}),
    config=cfg))
s2, cp2 = _state_from_result(r2)
print("   second gate stage:", cp2.stage.value, ok(cp2.stage is WorkflowStage.DESIGN))
print("   design_spec set  :", ok(s2.design_spec is not None))
print("   ids differ between gates:", ok(cp2.checkpoint_id != out.pending_checkpoint.checkpoint_id))

print()
print("=== 4. A COMPLETED run keeps normal behaviour (no gate invented) ===")
r3 = asyncio.run(svc._workflow.ainvoke(
    Command(resume={"checkpoint_id": cp2.checkpoint_id, "action": "approve"}), config=cfg))
s3, cp3 = _state_from_result(r3)
print("   no __interrupt__ :", ok("__interrupt__" not in r3))
print("   checkpoint None  :", ok(cp3 is None))
print("   status NOT overridden to awaiting_hitl:", s3.run_status.value,
      ok(s3.run_status is not RunStatus.AWAITING_HITL))
print("   both approvals kept:", len(s3.approved_decisions), ok(len(s3.approved_decisions) == 2))

print()
print("=== 5. A FAILED run keeps FAILED (malformed decision) ===")
svc2 = fresh_service(tempfile.mkdtemp(prefix="svc_check2_"))
p2 = svc2.create(prompt="x").project_id
o2 = asyncio.run(svc2.run_workflow(p2))
cfgb = {"configurable": {"thread_id": p2}}
rb = asyncio.run(svc2._workflow.ainvoke(Command(resume={"nonsense": True}), config=cfgb))
sb, cpb = _state_from_result(rb)
print("   run_status FAILED:", sb.run_status.value, ok(sb.run_status is RunStatus.FAILED))
print("   not masked as awaiting_hitl:", ok(sb.run_status is not RunStatus.AWAITING_HITL))

print()
print("=== 6. Helper edge cases ===")
plain = ProjectState(user_request="x")
print("   ProjectState passthrough:", ok(_state_from_result(plain) == (plain, None)))
d = plain.model_dump(mode="json")
print("   dict, no interrupt key  :", ok(_state_from_result(d)[1] is None))
print("   empty interrupt tuple   :", ok(_state_from_result({**d, "__interrupt__": ()})[1] is None))
print("   unreadable payload -> None, no raise:",
      ok(_state_from_result({**d, "__interrupt__": [{"bogus": 1}]})[1] is None))
good = HitlCheckpoint(stage=WorkflowStage.PLANNING, title="t", summary="s",
                      result_ref="plan", options=[]).model_dump(mode="json")
print("   bare dict payload accepted:",
      ok(_state_from_result({**d, "__interrupt__": [good]})[1] is not None))
print("   state still validated strictly:",
      ok(_state_from_result({**d, "__interrupt__": [good]})[0].project_id == plain.project_id))
try:
    _state_from_result({**d, "__interrupt__": [good], "smuggled": 1})
    print("   extra field still forbidden:", "**FAIL**")
except Exception:
    print("   extra field still forbidden:", ok(True))
