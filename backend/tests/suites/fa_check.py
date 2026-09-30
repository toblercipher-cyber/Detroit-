"""Frontend Architect verification. No network, no API key."""
import os, sys, re, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from langchain_core.messages import SystemMessage, HumanMessage
from backend.agents.frontend_architect import (FrontendArchitectAgent,
                                               FRONTEND_ARCHITECT_DEFINITION)
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder, ContextPermissionError
from backend.schemas.enums import AgentRole
from backend.schemas.state import (ProjectState, Plan, DesignSpec,
                                   FrontendArchitecture, ComponentTree, ComponentNode)

ok = lambda b: "PASS" if b else "**FAIL**"

class StubRunnable(Runnable):
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; self.last = i.to_messages() if hasattr(i, "to_messages") else i; return self.v
class StubModel:
    def __init__(self, v): self.inner = StubRunnable(v)
    def with_structured_output(self, m): return self.inner

print("=== 1. Contract vs docs/agents.md 4.5 ===")
d = FRONTEND_ARCHITECT_DEFINITION
print("   role/output :", d.role.value, "/", d.output_model.__name__,
      ok(d.role is AgentRole.FRONTEND_ARCHITECT and d.output_model is FrontendArchitecture))
print("   allowed_tools:", set(d.allowed_tools), ok(d.allowed_tools == frozenset({"repository_read"})))
print("   resp/proh   :", len(d.responsibilities), "/", len(d.prohibited_actions))
doc = open("docs/agents.md", encoding="utf-8").read().split("### 4.5 Frontend Architect")[1].split("### 4.6")[0]
docfields = set(re.findall(r"([a-z_]+)(?:\[\])?(?=[,}])", doc.split("FrontendArchitecture {")[1].split("}")[0]))
print("   output covers documented schema:", ok(not (docfields - set(FrontendArchitecture.model_fields))),
      "| documented:", len(docfields))
proh = " ".join(d.prohibited_actions).lower()
for needle, label in [("implementation code", "no code"), ("unnecessary abstraction", "no needless abstraction"),
                      ("new dependencies", "no new deps"), ("visually identical", "scoped change only")]:
    print("   prohibition: %-30s %s" % (label, ok(needle in proh)))

print()
print("=== 2. WITH an inventory: reuse framing present ===")
cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.plan = Plan(objective="sell coffee", pages=["/", "/menu"])
s.design_spec = DesignSpec(font_families=["Inter"], radius="0.375rem")
inv = ["ui/button", "ui/card", "site-nav", "hero-section"]
ctx = cb.build(s, AgentRole.FRONTEND_ARCHITECT, task="define the structure", component_inventory=inv)
agent = FrontendArchitectAgent(StubModel(FrontendArchitecture(pages=["/"], reused_components=["ui/button"])))
msgs = agent.prompt_template(ctx).format_messages(**agent.prompt_variables(ctx))
sysm, hum = msgs[0].content, msgs[1].content
print("   types       :", ok(isinstance(msgs[0], SystemMessage) and isinstance(msgs[1], HumanMessage)))
print("   inventory in prompt      :", ok("ui/button" in hum and "site-nav" in hum))
print("   reuse-first instruction  :", ok("Work through them before proposing anything new" in hum))
print("   plan + design_spec present:", ok("sell coffee" in hum and "0.375rem" in hum))
print("   raw user_request excluded:", ok("SECRET-RAW" not in hum))
print("   forbids code (system)    :", ok("Do not write implementation code" in sysm))
print("   forbids new deps (system):", ok("new dependencies" in sysm))

print()
print("=== 3. WITHOUT an inventory: states it plainly, invents nothing ===")
ctx2 = cb.build(s, AgentRole.FRONTEND_ARCHITECT, task="define the structure")
a2 = FrontendArchitectAgent(StubModel(FrontendArchitecture()))
hum2 = a2.prompt_template(ctx2).format_messages(**a2.prompt_variables(ctx2))[1].content
print("   says nothing built yet   :", ok("nothing built yet" in hum2))
print("   warns against assuming   :", ok("Do not assume a component exists" in hum2))
print("   no stale reuse wording   :", ok("Work through them" not in hum2))

print()
print("=== 4. Chain + run ===")
chain = agent.build_chain(agent.prompt_template(ctx))
print("   chain :", type(chain).__name__, ok(isinstance(chain, RunnableSequence)))
res = asyncio.run(agent.run(ctx))
print("   run ok:", ok(res.ok), "| output:", type(res.output).__name__)
print("   reused_components carried:", res.output.reused_components)

print()
print("=== 5. Scoped-technical-change rule reaches the prompt ===")
for phrase in ["visually identical", "is a redesign and is", "not yours to decide"]:
    print("   %-34s %s" % (phrase, ok(phrase in hum)))

print()
print("=== 6. Least privilege ===")
m2 = StubModel(FrontendArchitecture())
wrong = cb.build(s, AgentRole.PLANNER, task="x")
r = asyncio.run(FrontendArchitectAgent(m2).run(wrong))
print("   wrong context refused    :", ok(not r.ok and m2.inner.calls == 0), "| kind:", r.failure.kind.value)
for kw, label in [(dict(findings=[]), "findings"), (dict(rendered_preview="u"), "rendered_preview")]:
    try:
        cb.build(s, AgentRole.FRONTEND_ARCHITECT, task="x", **kw); print("   %-18s ACCEPTED **FAIL**" % label)
    except ContextPermissionError: print("   %-18s refused %s" % (label, ok(True)))

print()
print("=== 7. Purity ===")
src = open("backend/agents/frontend_architect.py", encoding="utf-8").read()
print("   reads ProjectState:", ok("ProjectState" not in src))
print("   overrides run()   :", ok("def run(" not in src))
print("   imports langgraph :", ok(not re.search(r"^\s*(from|import)\s+langgraph", src, re.M)))
