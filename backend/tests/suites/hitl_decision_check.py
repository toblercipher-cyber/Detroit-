"""submit_hitl_decision: approve / custom / errors / append-not-replace. No network."""
import os, sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from backend.schemas.state import ProjectState, NormalizedRequirements, Plan, DesignSpec
from backend.schemas.enums import RunStatus, WorkflowStage, HitlAction
from backend.schemas.api import HitlDecisionRequest
from backend.services.projects import (ProjectService, NoPendingCheckpointError,
                                       CheckpointAlreadyResolvedError, ProjectServiceError)
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
from backend.tests.suites import _tmpdir


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
    from backend.agents.fixer import FixerAgent as _FX
    from backend.agents.visual_ux_critic import VisualUxCriticAgent as _VC
    from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent as _AS
    from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent as _AR
    from backend.agents.code_reviewer import CodeReviewerAgent as _CR
    from backend.tools.build_runner import BuildRunner as _BR
    from backend.tools.impeccable import ImpeccableStatus as _IS
    return dict(reference_analyzer=_RAA(_RefStub()),
                frontend_architect=_FAA(_RefStub()),
                build_builder=lambda ws: _BA(_RefStub(), ws),
                build_runner=_BR(timeout_seconds=600),
                visual_ux_critic=_VC(_RefStub()),
                anti_slop_reviewer=_AS(_RefStub()),
                accessibility_reviewer=_AR(_RefStub()),
                code_reviewer=_CR(_RefStub()),
                build_fixer=lambda ws: _FX(_RefStub(), ws),
                max_auto_iterations=3,
                # Blocked, which is the real state of this repository.
                impeccable_probe=lambda: _IS(False, reason="not installed"),
                repository=_JAR(_P(_tmpdir.mkdtemp(prefix="wfdeps_"))))


N = {"design": 0, "plan": 0}


class FakeChat(Runnable):
    def __init__(self, **kw): pass
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        def make():
            if model is NormalizedRequirements:
                return NormalizedRequirements(goal="g", input_density="sparse")
            if model is Plan:
                N["plan"] += 1
                return Plan(objective="plan v%d" % N["plan"])
            N["design"] += 1
            return DesignSpec(font_families=["Inter"], visual_tone="tone v%d" % N["design"])
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k): return make()
        return _S()


def fresh():
    N["design"] = 0
    N["plan"] = 0
    g = build_workflow(WorkflowDependencies(**_ref_deps(), 
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(FakeChat()),
        planner=PlannerAgent(FakeChat()),
        design_director=DesignDirectorAgent(FakeChat())), checkpointer=InMemorySaver())
    svc = ProjectService(repository=JsonArtifactRepository(Path(_tmpdir.mkdtemp())),
                         workflow=g, settings=get_settings())
    pid = svc.create(prompt="build a coffee shop landing page").project_id
    st = asyncio.run(svc.run_workflow(pid))
    return svc, pid, st


print("=== 1. APPROVE advances to the next gate ===")
svc, pid, st = fresh()
cp1 = st.pending_checkpoint.checkpoint_id
acc = asyncio.run(svc.submit_hitl_decision(
    pid, HitlDecisionRequest(checkpoint_id=cp1, action=HitlAction.APPROVE)))
print("   accepted        :", ok(acc.accepted and acc.resumed))
print("   stage answered  :", acc.stage.value, ok(acc.stage is WorkflowStage.PLANNING))
print("   status          :", acc.status.value, ok(acc.status is RunStatus.AWAITING_HITL))
s = svc.get(pid)
print("   next gate is design:", s.pending_checkpoint.stage.value,
      ok(s.pending_checkpoint.stage is WorkflowStage.DESIGN))
print("   persisted status:", ok(s.run_status is RunStatus.AWAITING_HITL))

print()
print("=== 2. hitl_history APPENDED once, not replaced, not duplicated ===")
print("   hitl_history len:", len(s.hitl_history), ok(len(s.hitl_history) == 1))
print("   action recorded :", s.hitl_history[0].action.value,
      ok(s.hitl_history[0].action is HitlAction.APPROVE))
# Regression guard: resuming re-executes _gate_node, which rebuilds HitlCheckpoint
# with a fresh uuid. The records must reference the gate the USER saw, taken from
# decision.checkpoint_id, not that rebuilt id.
print("   history ckpt id == presented id :",
      ok(s.hitl_history[0].checkpoint_id == cp1))
print("   approval id     == presented id :",
      ok(s.approved_decisions[0].decision_id == cp1))
print("   approved_decisions len:", len(s.approved_decisions), ok(len(s.approved_decisions) == 1))

print()
print("=== 3. CUSTOM re-runs the SAME stage, does not advance ===")
cp2 = s.pending_checkpoint.checkpoint_id
before = N["design"]
acc2 = asyncio.run(svc.submit_hitl_decision(
    pid, HitlDecisionRequest(checkpoint_id=cp2, action=HitlAction.CUSTOM,
                             instruction="warmer palette")))
s2 = svc.get(pid)
print("   design re-ran   :", N["design"], ok(N["design"] == before + 1))
print("   planner NOT re-run:", N["plan"], ok(N["plan"] == 1))
print("   still at design :", s2.pending_checkpoint.stage.value,
      ok(s2.pending_checkpoint.stage is WorkflowStage.DESIGN))
print("   new gate id     :", ok(s2.pending_checkpoint.checkpoint_id != cp2))
print("   history grew 1->2:", len(s2.hitl_history), ok(len(s2.hitl_history) == 2))
print("   history order kept:", [h.action.value for h in s2.hitl_history],
      ok([h.action.value for h in s2.hitl_history] == ["approve", "custom"]))
print("   instruction reached the spec:", s2.design_spec.visual_tone,
      ok(s2.design_spec.visual_tone == "tone v2"))
print("   2nd record cites the 2nd gate:", ok(s2.hitl_history[1].checkpoint_id == cp2))
print("   1st record unchanged by the 2nd:", ok(s2.hitl_history[0].checkpoint_id == cp1))
print("   every record joins a distinct gate:",
      ok(len({h.checkpoint_id for h in s2.hitl_history}) == 2))

print()
print("=== 4. 410 - stale checkpoint id ===")
try:
    asyncio.run(svc.submit_hitl_decision(
        pid, HitlDecisionRequest(checkpoint_id=cp1, action=HitlAction.APPROVE)))
    print("   stale id accepted: **FAIL**")
except CheckpointAlreadyResolvedError as e:
    print("   raises CheckpointAlreadyResolvedError:", ok(True))
    print("   code = checkpoint_already_resolved  :",
          ok(e.code.value == "checkpoint_already_resolved"))
    print("   message leaks no internals          :",
          ok("node" not in str(e).lower() and os.sep not in str(e)))

print()
print("=== 5. 409 - no gate pending ===")
svc3, pid3, st3 = fresh()
cp3 = st3.pending_checkpoint.checkpoint_id
asyncio.run(svc3.submit_hitl_decision(
    pid3, HitlDecisionRequest(checkpoint_id=cp3, action=HitlAction.APPROVE)))
s3 = svc3.get(pid3)
s3.pending_checkpoint = None            # simulate a resolved / finished run
svc3._repository.save(s3)
try:
    asyncio.run(svc3.submit_hitl_decision(
        pid3, HitlDecisionRequest(checkpoint_id=cp3, action=HitlAction.APPROVE)))
    print("   accepted with no gate: **FAIL**")
except NoPendingCheckpointError as e:
    print("   raises NoPendingCheckpointError:", ok(True))
    print("   code = no_pending_checkpoint   :", ok(e.code.value == "no_pending_checkpoint"))

print()
print("=== 6. REJECT loops the stage, approves nothing ===")
svc4, pid4, st4 = fresh()
asyncio.run(svc4.submit_hitl_decision(
    pid4, HitlDecisionRequest(checkpoint_id=st4.pending_checkpoint.checkpoint_id,
                              action=HitlAction.REJECT)))
s4 = svc4.get(pid4)
print("   planner re-ran  :", N["plan"], ok(N["plan"] == 2))
print("   still at planning:", s4.pending_checkpoint.stage.value,
      ok(s4.pending_checkpoint.stage is WorkflowStage.PLANNING))
print("   no approvals    :", len(s4.approved_decisions), ok(len(s4.approved_decisions) == 0))
print("   history has the reject:", ok(len(s4.hitl_history) == 1))

print()
print("=== 7. Raw LangGraph errors never escape ===")


class Boom:
    async def ainvoke(self, *a, **k):
        raise RuntimeError("internal node C:/secret/path exploded")


svc5, pid5, st5 = fresh()
svc5._workflow = Boom()
try:
    asyncio.run(svc5.submit_hitl_decision(
        pid5, HitlDecisionRequest(checkpoint_id=st5.pending_checkpoint.checkpoint_id,
                                  action=HitlAction.APPROVE)))
    print("   **FAIL** (expected a typed error)")
except ProjectServiceError as e:
    print("   typed ProjectServiceError:", ok(True))
    print("   original text suppressed :",
          ok("exploded" not in str(e) and "secret" not in str(e)))
    print("   cause preserved for logs :", ok(isinstance(e.__cause__, RuntimeError)))
    print("   state marked failed      :", ok(svc5.get(pid5).run_status is RunStatus.FAILED))
