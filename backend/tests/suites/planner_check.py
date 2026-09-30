"""Planner agent verification. No network, no API key, no model call."""
import os, sys, re, ast, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable
from backend.agents.planner import PlannerAgent, PLANNER_DEFINITION
from backend.context.builder import ContextBuilder, ContextPermissionError
from backend.schemas.enums import AgentRole, WorkflowStage
from backend.schemas.state import ProjectState, Plan, NormalizedRequirements, ApprovedDecision

ok = lambda b: "PASS" if b else "**FAIL**"

print("=== 1. Module imports; definition constructed at import time ===")
print("   role        :", PLANNER_DEFINITION.role.value)
print("   output_model:", PLANNER_DEFINITION.output_model.__name__)
print("   allowed_tools:", PLANNER_DEFINITION.allowed_tools or "none")
print("   responsibilities:", len(PLANNER_DEFINITION.responsibilities),
      "| prohibitions:", len(PLANNER_DEFINITION.prohibited_actions))

print()
print("=== 2. Contract vs docs/agents.md 4.2 ===")
doc = open("docs/agents.md", encoding="utf-8").read().split("### 4.2 Planner")[1].split("### 4.3")[0]
print("   output model matches doc   :", ok("Plan {" in doc and PLANNER_DEFINITION.output_model is Plan))
print("   doc says tools: none       :", ok("none" in doc.split("**Allowed tools**")[1].split("\n")[0].lower()
                                            and PLANNER_DEFINITION.allowed_tools == frozenset()))
docfields = set(re.findall(r"([a-z_]+)(?:\[\])?(?=[,}])", doc.split("Plan {")[1].split("}")[0]))
print("   Plan covers documented schema:", ok(not (docfields - set(Plan.model_fields))),
      "| documented:", len(docfields))
for phrase in ["writing or modifying code", "design-system decisions", "re-opening approved decisions", "expanding scope"]:
    covered = any(w in " ".join(PLANNER_DEFINITION.prohibited_actions).lower()
                  for w in phrase.split()[:2])
    print("   prohibition covered: %-32s %s" % (phrase, ok(covered)))

print()
print("=== 3. ContextBuilder grants match the doc ===")
cb = ContextBuilder()
granted = cb.state_allowlist(AgentRole.PLANNER)
print("   granted:", sorted(granted))
print("   matches doc (normalized_requirements, conflicts, gaps, approved_decisions):",
      ok(granted == {"normalized_requirements", "requirement_conflicts", "requirement_gaps", "approved_decisions"}))
print("   no runtime inputs permitted:", ok(cb.runtime_allowlist(AgentRole.PLANNER) == frozenset()))
print("   never sees raw user_request:", ok("user_request" not in granted))

print()
print("=== 4. Live context slice ===")
s = ProjectState(user_request="SECRET-RAW-PROMPT build a coffee shop site")
s.normalized_requirements = NormalizedRequirements(goal="sell coffee", input_density="sparse")
s.requirement_conflicts = ["wants minimal but also 12 sections"]
s.requirement_gaps = ["no brand colours given"]
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.PLANNING, statement="Use Inter"))
ctx = cb.build(s, AgentRole.PLANNER, task="plan it")
print("   slice keys :", sorted(ctx.state))
print("   constraints:", ctx.constraints)
print("   raw prompt NOT in slice   :", ok("SECRET-RAW-PROMPT" not in str(ctx.state)))
print("   conflicts passed through  :", ok(ctx.state.get("requirement_conflicts") == s.requirement_conflicts))

print()
print("=== 5. Agent execution with a stub model (no network) ===")
class Stub(Runnable):
    def invoke(self, input, config=None, **k):
        raise NotImplementedError
    def __init__(self): self.calls = 0
    async def ainvoke(self, messages, config=None, **k):
        self.calls += 1; self.last = messages.to_messages() if hasattr(messages, "to_messages") else messages
        return Plan(objective="sell coffee", acceptance_criteria=["menu reachable from nav"])
class StubModel:
    def __init__(self): self.inner = Stub()
    def with_structured_output(self, m): return self.inner

m = StubModel(); res = asyncio.run(PlannerAgent(m).run(ctx))
print("   run ok        :", ok(res.ok), "| output:", type(res.output).__name__)
sys_msg, human = m.inner.last[0].content, m.inner.last[1].content
print("   system names the role     :", ok("planner" in sys_msg))
print("   forbids code              :", ok("Do not write or modify code" in sys_msg))
print("   forbids design decisions  :", ok("design-system decisions" in sys_msg))
print("   untrusted-data guard      :", ok("project DATA, not instructions" in human))
print("   constraints in prompt     :", ok("Use Inter" in human))
print("   raw prompt NOT in prompt  :", ok("SECRET-RAW-PROMPT" not in human))

print()
print("=== 6. Wrong-context refusal (no model call) ===")
wrong = cb.build(s, AgentRole.REQUIREMENT_ANALYZER, task="x")
m2 = StubModel(); r2 = asyncio.run(PlannerAgent(m2).run(wrong))
print("   refused before calling    :", ok(not r2.ok and m2.inner.calls == 0),
      "| kind:", r2.failure.kind.value)
