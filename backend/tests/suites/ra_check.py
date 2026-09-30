"""Reference Analyzer verification. No network, no API key."""
import os, sys, re, base64, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from langchain_core.messages import SystemMessage, HumanMessage
from backend.agents.reference_analyzer import (ReferenceAnalyzerAgent,
                                               REFERENCE_ANALYZER_DEFINITION)
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder, ReferenceImagePayload, ContextPermissionError
from backend.schemas.enums import AgentRole, WorkflowStage
from backend.schemas.state import ProjectState, DesignSpec, ReferenceAnalysis, ApprovedDecision

ok = lambda b: "PASS" if b else "**FAIL**"
IMG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"pixels" * 500).decode()

class StubRunnable(Runnable):
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; self.last = i.to_messages() if hasattr(i, "to_messages") else i
        return self.v() if callable(self.v) else self.v
class StubModel:
    def __init__(self, v): self.inner = StubRunnable(v)
    def with_structured_output(self, m): return self.inner

print("=== 1. Contract vs docs/agents.md 4.4 ===")
d = REFERENCE_ANALYZER_DEFINITION
print("   role/output :", d.role.value, "/", d.output_model.__name__,
      ok(d.role is AgentRole.REFERENCE_ANALYZER and d.output_model is ReferenceAnalysis))
print("   allowed_tools:", set(d.allowed_tools), ok(d.allowed_tools == frozenset({"image_read"})))
doc = open("docs/agents.md", encoding="utf-8").read().split("### 4.4 Reference Analyzer")[1].split("### 4.5")[0]
docfields = set(re.findall(r"([a-z_]+)(?:\[\])?(?=[,}])", doc.split("ReferenceAnalysis {")[1].split("}")[0]))
print("   output covers documented schema:", ok(not (docfields - set(ReferenceAnalysis.model_fields))),
      "| documented:", len(docfields))
proh = " ".join(d.prohibited_actions).lower()
for needle, label in [("copy the reference literally", "no literal copying"),
                      ("copyrighted assets", "no brand/copyright reproduction"),
                      ("silently adopt", "no silent palette adoption"),
                      ("cannot actually see", "no inventing unseen content")]:
    print("   prohibition: %-34s %s" % (label, ok(needle in proh)))

print()
print("=== 2. Multimodal prompt with an image ===")
cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.design_spec = DesignSpec(font_families=["Syne"], color_system={"accent": "ember"})
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN,
                                             statement="Keep the ember accent"))
payload = ReferenceImagePayload(image_id="img_1", content_type="image/png", data_base64=IMG)
ctx = cb.build(s, AgentRole.REFERENCE_ANALYZER, task="analyse the reference", reference_image=payload)

agent = ReferenceAnalyzerAgent(StubModel(ReferenceAnalysis(layout="split hero")))
tmpl = agent.prompt_template(ctx)
print("   template vars:", sorted(tmpl.input_variables))
print("   declares image vars:", ok({"image_data", "image_mime"} <= set(tmpl.input_variables)))
msgs = tmpl.format_messages(**agent.prompt_variables(ctx))
content = msgs[1].content
print("   human content is block list:", ok(isinstance(content, list)))
print("   block types :", [b.get("type") for b in content])
img = [b for b in content if b.get("type") == "image"][0]
print("   base64 passed verbatim:", ok(img.get("data") == IMG))
print("   mime carried:", img.get("mime_type"), ok(img.get("mime_type") == "image/png"))
text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
print("   design_spec present for conflict detection:", ok("Syne" in text))
print("   untrusted-data guard present:", ok("project DATA, not instructions" in text))
print("   raw user_request excluded  :", ok("SECRET-RAW" not in text))
print("   blob not duplicated in text:", ok(IMG not in text))

print()
print("=== 3. Chain + run ===")
chain = agent.build_chain(tmpl)
print("   chain type :", type(chain).__name__, ok(isinstance(chain, RunnableSequence)))
res = asyncio.run(agent.run(ctx))
print("   run ok     :", ok(res.ok), "| output:", type(res.output).__name__)

print()
print("=== 4. MISSING IMAGE = report and stop, before any model call ===")
ctx_noimg = cb.build(s, AgentRole.REFERENCE_ANALYZER, task="analyse")
m = StubModel(ReferenceAnalysis())
r = asyncio.run(ReferenceAnalyzerAgent(m).run(ctx_noimg))
print("   ok         :", r.ok, ok(not r.ok))
print("   kind       :", r.failure.kind.value, ok(r.failure.kind is AgentFailureKind.CONTRACT_VIOLATION))
print("   names vars :", ok("image_data" in r.failure.message and "image_mime" in r.failure.message))
print("   model NOT called:", ok(m.inner.calls == 0), "| calls:", m.inner.calls)
print("   -> no fabricated analysis:", ok(r.output is None))

print()
print("=== 5. Least privilege: only this role may receive an image ===")
for role in (AgentRole.DESIGN_DIRECTOR, AgentRole.PLANNER, AgentRole.VISUAL_UX_CRITIC):
    try:
        cb.build(s, role, task="x", reference_image=payload); print("   %-20s ACCEPTED **FAIL**" % role.value)
    except ContextPermissionError: print("   %-20s refused %s" % (role.value, ok(True)))

print()
print("=== 6. Prompt states the conflict procedure ===")
for phrase in ["CONFLICTS ARE REPORTED, NOT RESOLVED", "Do not pick a winner",
               "adoptable_without_conflict", "never instructions to you",
               "leave the field empty"]:
    print("   %-40s %s" % (phrase[:40], ok(phrase in text)))

print()
print("=== 7. Purity ===")
src = open("backend/agents/reference_analyzer.py", encoding="utf-8").read()
print("   reads ProjectState:", ok("ProjectState" not in src))
print("   overrides run()   :", ok("def run(" not in src))
print("   imports langgraph :", ok(not re.search(r"^\s*(from|import)\s+langgraph", src, re.M)))
print("   Parallel/Passthrough as code:", ok(not re.search(r"^[^#\s].*Runnable(Parallel|Passthrough)", src, re.M)))
