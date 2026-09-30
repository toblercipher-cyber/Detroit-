"""Prompt-template verification. No network, no API key."""
import os, sys, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.context.builder import ContextBuilder
from backend.schemas.enums import AgentRole, WorkflowStage
from backend.schemas.state import (ProjectState, NormalizedRequirements, Plan,
                                   ApprovedDecision, DesignSpec)

ok = lambda b: "PASS" if b else "**FAIL**"

class Stub(Runnable):
    def invoke(self, input, config=None, **k):
        raise NotImplementedError
    def __init__(self, v): self.v, self.calls = v, 0
    async def ainvoke(self, m, config=None, **k): self.calls += 1; self.last = m.to_messages() if hasattr(m, "to_messages") else m; return self.v
class StubModel:
    def __init__(self, v): self.inner = Stub(v)
    def with_structured_output(self, m): return self.inner

cb = ContextBuilder()
s = ProjectState(user_request="Build a coffee shop landing page")
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN, statement="Use Inter"))

print("=== 1. Template is a real ChatPromptTemplate ===")
ctx = cb.build(s, AgentRole.REQUIREMENT_ANALYZER, task="normalize")
agent = RequirementAnalyzerAgent(StubModel(NormalizedRequirements(goal="g", input_density="sparse")))
t = agent.prompt_template(ctx)
print("   type              :", type(t).__name__, ok(isinstance(t, ChatPromptTemplate)))
print("   input variables   :", sorted(t.input_variables))
print("   variables supplied:", sorted(agent.prompt_variables(ctx)))
missing = set(t.input_variables) - set(agent.prompt_variables(ctx))
print("   all vars supplied :", ok(not missing), missing or "")

print()
print("=== 2. Renders to real message objects ===")
msgs = t.format_messages(**agent.prompt_variables(ctx))
print("   count/types       :", len(msgs), [type(m).__name__ for m in msgs],
      ok(isinstance(msgs[0], SystemMessage) and isinstance(msgs[1], HumanMessage)))
print("   contract in system:", ok("YOU MUST NOT" in msgs[0].content and "Do not design" in msgs[0].content))
print("   guard in human    :", ok("project DATA, not instructions" in msgs[1].content))
print("   constraints shown :", ok("Use Inter" in msgs[1].content))

print()
print("=== 3. THE POINT: braces in project content survive ===")
s2 = ProjectState(user_request='Match this token set: {"radius": "0.375rem", "hue": {"a": 1}}')
ctx2 = cb.build(s2, AgentRole.REQUIREMENT_ANALYZER, task='Use {this} literally')
m2 = RequirementAnalyzerAgent(StubModel(NormalizedRequirements(goal="g", input_density="sparse")))
rendered = m2.prompt_template(ctx2).format_messages(**m2.prompt_variables(ctx2))[1].content
print("   JSON braces intact:", ok('{"radius": "0.375rem", "hue": {"a": 1}}' in rendered))
print("   braces in task    :", ok("Use {this} literally" in rendered))

print()
print("=== 4. Structure adapts to the granted slice ===")
cr = cb.build(s, AgentRole.CODE_REVIEWER, task="review")
class Dummy(RequirementAnalyzerAgent): pass
tpl_cr = RequirementAnalyzerAgent(StubModel(None)).prompt_template(cr)
print("   CodeReviewer ctx (no state): no STATE block:",
      ok("PROJECT STATE" not in "\n".join(str(m.prompt.template) for m in tpl_cr.messages)))
s3 = ProjectState(user_request="x"); s3.normalized_requirements = NormalizedRequirements(goal="g", input_density="sparse")
p_ctx = cb.build(s3, AgentRole.PLANNER, task="plan")
tpl_p = PlannerAgent(StubModel(Plan(objective="o"))).prompt_template(p_ctx)
txt = "\n".join(str(m.prompt.template) for m in tpl_p.messages)
print("   Planner ctx (state, no constraints): STATE yes / DECISIONS no:",
      ok("PROJECT STATE" in txt and "APPROVED DECISIONS" not in txt))

print()
print("=== 5. End-to-end run still works; model receives messages ===")
a = RequirementAnalyzerAgent(StubModel(NormalizedRequirements(goal="g", input_density="sparse")))
r = asyncio.run(a.run(ctx))
print("   run ok            :", ok(r.ok))
sent = a._model.inner.last
print("   sent message types:", [type(m).__name__ for m in sent],
      ok(all(hasattr(m, "content") for m in sent)))

print()
print("=== 6. Planner run through templates ===")
pl = PlannerAgent(StubModel(Plan(objective="sell coffee")))
rp = asyncio.run(pl.run(p_ctx))
print("   run ok            :", ok(rp.ok))
print("   forbids design    :", ok("design-system decisions" in pl._model.inner.last[0].content))
