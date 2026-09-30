"""RunnableSequence verification. No network, no API key."""
import os, sys, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder
from backend.schemas.enums import AgentRole, WorkflowStage
from backend.schemas.state import ProjectState, NormalizedRequirements, Plan, ApprovedDecision

ok = lambda b: "PASS" if b else "**FAIL**"

class StubRunnable(Runnable):
    """Stands in for ChatAnthropic: records what the prompt step produced."""
    def __init__(self, value): self.value, self.calls, self.last = value.to_messages() if hasattr(value, "to_messages") else value, 0, None
    def invoke(self, input, config=None, **kw): raise NotImplementedError
    async def ainvoke(self, input, config=None, **kw):
        self.calls += 1; self.last = input.to_messages() if hasattr(input, "to_messages") else input
        return self.value() if callable(self.value) else self.value
class StubModel:
    def __init__(self, value): self.inner = StubRunnable(value)
    def with_structured_output(self, model): return self.inner

cb = ContextBuilder()
s = ProjectState(user_request="Build a coffee shop landing page")
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN, statement="Use Inter"))
ctx = cb.build(s, AgentRole.REQUIREMENT_ANALYZER, task="normalize")

print("=== 1. build_chain() returns a RunnableSequence ===")
agent = RequirementAnalyzerAgent(StubModel(NormalizedRequirements(goal="g", input_density="sparse")))
tmpl = agent.prompt_template(ctx)
chain = agent.build_chain(tmpl)
print("   type            :", type(chain).__name__, ok(isinstance(chain, RunnableSequence)))
print("   is a Runnable   :", ok(isinstance(chain, Runnable)))
steps = list(getattr(chain, "steps", []))
print("   steps           :", [type(x).__name__ for x in steps])
print("   first step is the prompt template:", ok(isinstance(steps[0], ChatPromptTemplate)))

print()
print("=== 2. Chain takes VARIABLES and renders messages internally ===")
out = asyncio.run(chain.ainvoke(agent.prompt_variables(ctx)))
sent = agent._model.inner.last
msgs = sent.to_messages() if hasattr(sent, "to_messages") else sent
print("   model received  :", type(sent).__name__, "->", [type(m).__name__ for m in msgs])
print("   System+Human    :", ok(isinstance(msgs[0], SystemMessage) and isinstance(msgs[1], HumanMessage)))
print("   contract present:", ok("YOU MUST NOT" in msgs[0].content))
print("   guard present   :", ok("project DATA, not instructions" in msgs[1].content))
print("   returns typed output:", type(out).__name__, ok(isinstance(out, NormalizedRequirements)))

print()
print("=== 3. run() uses the chain end to end ===")
a2 = RequirementAnalyzerAgent(StubModel(NormalizedRequirements(goal="g", input_density="detailed")))
r = asyncio.run(a2.run(ctx))
print("   ok              :", ok(r.ok), "| calls:", a2._model.inner.calls)

print()
print("=== 4. Failure classification preserved through the chain ===")
def raise_validation():
    NormalizedRequirements()          # pydantic ValidationError
def raise_provider():
    raise RuntimeError("connection reset")
def raise_timeout():
    raise TimeoutError()
for fn, kind, calls in [(raise_validation, AgentFailureKind.SCHEMA_INVALID, 2),
                        (raise_provider, AgentFailureKind.PROVIDER_ERROR, 1),
                        (raise_timeout, AgentFailureKind.TIMEOUT, 1)]:
    m = StubModel(fn); res = asyncio.run(RequirementAnalyzerAgent(m).run(ctx))
    print("   %-18s -> %-18s calls=%d %s" % (fn.__name__, res.failure.kind.value,
          m.inner.calls, ok(res.failure.kind is kind and m.inner.calls == calls)))

print()
print("=== 5. Missing template variable = CONTRACT_VIOLATION, no model call ===")
class Broken(RequirementAnalyzerAgent):
    def prompt_variables(self, context):
        v = super().prompt_variables(context); v.pop("purpose"); return v
m = StubModel(NormalizedRequirements(goal="g", input_density="sparse"))
res = asyncio.run(Broken(m).run(ctx))
print("   kind            :", res.failure.kind.value, ok(res.failure.kind is AgentFailureKind.CONTRACT_VIOLATION))
print("   names the variable:", ok("purpose" in res.failure.message))
print("   model NOT called:", ok(m.inner.calls == 0))

print()
print("=== 6. No RunnableParallel / RunnablePassthrough in the agent layer ===")
import re, pathlib
hits = []
for p in pathlib.Path("backend/agents").glob("*.py"):
    src = p.read_text(encoding="utf-8")
    for name in ("RunnableParallel", "RunnablePassthrough"):
        # Imported or called only. A docstring saying WHY it is not used is
        # compliance, not a violation.
        pat = "(^[ 	]*(from|import) .*" + name + "|" + name + " *\()"
        if re.search(pat, src, re.M): hits.append((p.name, name))
print("   hits            :", hits or "NONE", ok(not hits))

print()
print("=== 7. Planner uses the same chain ===")
s2 = ProjectState(user_request="x"); s2.normalized_requirements = NormalizedRequirements(goal="g", input_density="sparse")
pctx = cb.build(s2, AgentRole.PLANNER, task="plan")
pm = StubModel(Plan(objective="sell coffee"))
pa = PlannerAgent(pm)
print("   chain type      :", type(pa.build_chain(pa.prompt_template(pctx))).__name__)
rp = asyncio.run(pa.run(pctx))
print("   run ok          :", ok(rp.ok), "| output:", type(rp.output).__name__)
