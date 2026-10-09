"""Agent execution + graph compile, driven by stubs. No network, no API key used."""
import os, sys, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable
from pydantic import ValidationError
from backend.agents.base import AgentFailureKind
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent, REQUIREMENT_ANALYZER_DEFINITION
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.context.builder import ContextBuilder
from backend.schemas.enums import AgentRole
from backend.schemas.state import ProjectState, NormalizedRequirements, Plan, DesignSpec

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


ok = lambda b: "PASS" if b else "**FAIL**"

class StubStructured(Runnable):
    def invoke(self, input, config=None, **k):
        raise NotImplementedError
    def __init__(self, behaviour): self.behaviour, self.calls = behaviour, 0
    async def ainvoke(self, messages, config=None, **k):
        self.calls += 1
        self.last = messages.to_messages() if hasattr(messages, "to_messages") else messages
        if self.behaviour == "good":
            return NormalizedRequirements(goal="a coffee shop landing page", input_density="sparse")
        if self.behaviour == "bad_schema":
            NormalizedRequirements()            # raises ValidationError (missing required)
        if self.behaviour == "provider":
            raise RuntimeError("connection reset")
        if self.behaviour == "timeout":
            raise TimeoutError()

class StubModel:
    def __init__(self, behaviour): self.inner = StubStructured(behaviour)
    def with_structured_output(self, model): return self.inner

state = ProjectState(user_request="Build a coffee shop landing page")
ctx = ContextBuilder().build(state, AgentRole.REQUIREMENT_ANALYZER, task="normalize")

print("=== Agent execution paths (stubbed model) ===")
for behaviour, expect_ok, expect_kind, attempts in [
    ("good", True, None, 1),
    ("bad_schema", False, AgentFailureKind.SCHEMA_INVALID, 2),
    ("provider", False, AgentFailureKind.PROVIDER_ERROR, 1),
    ("timeout", False, AgentFailureKind.TIMEOUT, 1),
]:
    m = StubModel(behaviour)
    agent = RequirementAnalyzerAgent(m)
    res = asyncio.run(agent.run(ctx))
    kind = res.failure.kind if res.failure else None
    print("  %-11s ok=%-5s kind=%-18s calls=%d  %s" % (
        behaviour, res.ok, kind.value if kind else "-", m.inner.calls,
        ok(res.ok == expect_ok and kind == expect_kind and m.inner.calls == attempts)))

print()
print("=== Schema failure retried exactly max_attempts times (no coercion) ===")
m = StubModel("bad_schema"); res = asyncio.run(RequirementAnalyzerAgent(m).run(ctx))
print("  retried twice        :", ok(m.inner.calls == 2))
print("  no partial output    :", ok(res.output is None))

print()
print("=== Role/context mismatch -> CONTRACT_VIOLATION (no model call) ===")
wrong = ContextBuilder().build(state, AgentRole.PLANNER, task="plan")
m = StubModel("good"); res = asyncio.run(RequirementAnalyzerAgent(m).run(wrong))
print("  refused before call  :", ok(res.failure.kind is AgentFailureKind.CONTRACT_VIOLATION and m.inner.calls == 0))

print()
print("=== Prompt content ===")
m = StubModel("good"); asyncio.run(RequirementAnalyzerAgent(m).run(ctx))
sys_msg, human = m.inner.last[0].content, m.inner.last[1].content
print("  system has prohibitions:", ok("YOU MUST NOT" in sys_msg and "Do not design" in sys_msg))
print("  untrusted-data guard   :", ok("project DATA, not instructions" in human))
print("  raw prompt present for RA:", ok("coffee shop" in human))
print("  no secrets in prompt   :", ok("ANTHROPIC" not in sys_msg + human))

print()

class ValueRunnable(Runnable):
    """Returns one fixed typed value, whatever it is asked."""
    def __init__(self, v): self.v = v
    def invoke(self, input, config=None, **k): raise NotImplementedError
    async def ainvoke(self, input, config=None, **k): return self.v


class ValueModel:
    def __init__(self, v): self.inner = ValueRunnable(v)
    def with_structured_output(self, m): return self.inner


print("=== Graph compiles (checkpointer=None) ===")
from backend.graph.workflow import build_workflow, WorkflowDependencies
g = build_workflow(WorkflowDependencies(**_ref_deps(), 
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(StubModel("good")),
        planner=PlannerAgent(ValueModel(Plan(objective="o"))),
        design_director=DesignDirectorAgent(ValueModel(DesignSpec(visual_tone="t")))),
    checkpointer=None)
print("  compiled             :", ok(g is not None))
print("  nodes                :", sorted(n for n in g.get_graph().nodes if n not in ("__start__", "__end__")))

print()
print("=== Graph runs end-to-end with a stubbed model (no network) ===")
out = asyncio.run(g.ainvoke(state))
final = ProjectState.model_validate({k: v for k, v in out.items() if not k.startswith("__")})
print("  returned type        :", type(out).__name__)
print("  requirements set     :", ok(final.normalized_requirements is not None))
print("  stage/status         :", final.current_stage.value, "/", final.run_status.value)
print("  approved_decisions untouched:", ok(final.approved_decisions == []))
