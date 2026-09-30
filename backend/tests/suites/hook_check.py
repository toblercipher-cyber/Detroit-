"""evidence_blocks hook verification. No network, no API key."""
import os, sys, base64, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable
from langchain_core.messages import SystemMessage, HumanMessage
from backend.agents.base import AgentDefinition, BaseAgent, AgentFailureKind
from backend.context.builder import ContextBuilder
from backend.schemas.enums import AgentRole, WorkflowStage
from backend.schemas.state import (ProjectState, Plan, DesignSpec, ApprovedDecision,
                                   NormalizedRequirements, ReviewFindings)

ok = lambda b: "PASS" if b else "**FAIL**"
MARK = "MARKER-APPROVED-DECISION"

cb = ContextBuilder()
s = ProjectState(user_request="build a coffee shop site")
s.normalized_requirements = NormalizedRequirements(goal="g", input_density="sparse")
s.plan = Plan(objective="sell coffee")
s.design_spec = DesignSpec(font_families=["Inter"])
s.approved_decisions.append(
    ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN, statement=MARK)
)

print("=== 1. Default hook returns no blocks; text path unchanged ===")
from backend.agents.planner import PlannerAgent
p = PlannerAgent(None)
ctx = cb.build(s, AgentRole.PLANNER, task="plan it")
print("   evidence_blocks default:", p.evidence_blocks(ctx), ok(p.evidence_blocks(ctx) == []))
msgs = p.prompt_template(ctx).format_messages(**p.prompt_variables(ctx))
print("   message count/types:", len(msgs), [type(m).__name__ for m in msgs],
      ok(isinstance(msgs[0], SystemMessage) and isinstance(msgs[1], HumanMessage)))
print("   human content is a string (not blocks):", ok(isinstance(msgs[1].content, str)))
print("   constraints present:", ok(MARK in msgs[1].content))
print("   state present      :", ok("normalized_requirements" in msgs[1].content))  # planner is granted this, not `plan`
print("   guard present      :", ok("project DATA, not instructions" in msgs[1].content))

print()
print("=== 2. A subclass overriding ONLY evidence_blocks cannot drop a section ===")


class Probe(BaseAgent[ReviewFindings]):
    _DEF = AgentDefinition(
        role=AgentRole.ANTI_SLOP_REVIEWER,
        purpose="probe",
        responsibilities=("r",),
        prohibited_actions=("p",),
        output_model=ReviewFindings,
    )

    @property
    def definition(self):
        return self._DEF

    def evidence_blocks(self, context):
        return [{"type": "text", "text": "EVIDENCE\n{probe_evidence}"}]

    def prompt_variables(self, context):
        v = super().prompt_variables(context)
        v["probe_evidence"] = "some evidence"
        return v


probe = Probe(None)
pctx = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="t", rendered_preview=None)
body = probe.prompt_template(pctx).format_messages(**probe.prompt_variables(pctx))[1].content
text = " ".join(b.get("text", "") for b in body if b.get("type") == "text")
print("   human content is blocks:", ok(isinstance(body, list)), "| types:", [b.get("type") for b in body])
print("   task present       :", ok("t" in text))
print("   CONSTRAINTS present:", ok(MARK in text))
print("   state present      :", ok("Inter" in text))
print("   evidence present   :", ok("some evidence" in text))
print("   guard present      :", ok("project DATA, not instructions" in text))
print("   generic TASK INPUTS dump skipped:", ok("TASK INPUTS" not in text))

print()
print("=== 3. Unsupplied evidence placeholder still stops the run before a call ===")


class Broken(Probe):
    def prompt_variables(self, context):
        return BaseAgent.prompt_variables(self, context)  # forgets probe_evidence


class StubRunnable(Runnable):
    def __init__(self): self.calls = 0
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; return ReviewFindings()


class StubModel:
    def __init__(self): self.inner = StubRunnable()
    def with_structured_output(self, m): return self.inner


m = StubModel()
r = asyncio.run(Broken(m).run(pctx))
print("   ok:", r.ok, ok(not r.ok), "| kind:", r.failure.kind.value)
print("   names the placeholder:", ok("probe_evidence" in r.failure.message))
print("   model NOT called     :", ok(m.inner.calls == 0))
