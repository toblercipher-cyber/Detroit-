"""Visual & UX Critic verification. No network, no API key."""
import os, sys, re, base64, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from langchain_core.messages import SystemMessage, HumanMessage
from backend.agents.visual_ux_critic import VisualUxCriticAgent, VISUAL_UX_CRITIC_DEFINITION
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder, RenderedPreviewPayload, ContextPermissionError
from backend.schemas.enums import AgentRole, Severity
from backend.schemas.state import (ProjectState, Plan, DesignSpec, ReviewFindings,
                                   EvaluationFinding)

ok = lambda b: "PASS" if b else "**FAIL**"
SHOT = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p" * 2000).decode()
DOM = "<main><h1>Coffee</h1><nav>...</nav></main>"

class StubRunnable(Runnable):
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; self.last = i.to_messages() if hasattr(i, "to_messages") else i; return self.v
class StubModel:
    def __init__(self, v): self.inner = StubRunnable(v)
    def with_structured_output(self, m): return self.inner

OUT = ReviewFindings(findings=[EvaluationFinding(
    finding_id="v1", source=AgentRole.VISUAL_UX_CRITIC, category="ux",
    severity=Severity.MAJOR, summary="headings do not lead")])

print("=== 1. Contract vs docs/agents.md 4.7 ===")
d = VISUAL_UX_CRITIC_DEFINITION
print("   role/output :", d.role.value, "/", d.output_model.__name__,
      ok(d.role is AgentRole.VISUAL_UX_CRITIC and d.output_model is ReviewFindings))
print("   tools       :", set(d.allowed_tools))
print("   resp/proh   :", len(d.responsibilities), "/", len(d.prohibited_actions))
proh = " ".join(d.prohibited_actions).lower()
for needle, label in [("write or modify code", "no code"), ("redesign", "no redesigning"),
                      ("source alone", "no source-only judging"), ("mark it as a conflict", "conflict marking")]:
    print("   prohibition: %-26s %s" % (label, ok(needle in proh)))
doc = open("docs/agents.md", encoding="utf-8").read().split("### 4.7 Visual & UX Critic")[1].split("### 4.8")[0]
print("   doc output is ReviewFindings:", ok("ReviewFindings" in doc))

cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.design_spec = DesignSpec(font_families=["Inter"], radius="0.375rem")
s.plan = Plan(objective="sell coffee", acceptance_criteria=["menu reachable from nav"])

print()
print("=== 2. Screenshot + DOM: both reach the prompt ===")
p = RenderedPreviewPayload(url="http://x/", screenshot_base64=SHOT,
                           screenshot_media_type="image/png", dom_html=DOM)
ctx = cb.build(s, AgentRole.VISUAL_UX_CRITIC, task="critique", rendered_preview=p)
agent = VisualUxCriticAgent(StubModel(OUT))
tmpl = agent.prompt_template(ctx)
msgs = tmpl.format_messages(**agent.prompt_variables(ctx))
content = msgs[1].content
print("   block types :", [b.get("type") for b in content])
img = [b for b in content if b.get("type") == "image"][0]
print("   screenshot verbatim:", ok(img.get("data") == SHOT), "| mime:", img.get("mime_type"))
text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
print("   DOM present        :", ok(DOM in text))
print("   design_spec present:", ok("0.375rem" in text))
print("   acceptance criteria:", ok("menu reachable from nav" in text))
print("   guard present      :", ok("project DATA, not instructions" in text))
print("   raw prompt excluded:", ok("SECRET-RAW" not in text))
print("   blob not in text   :", ok(SHOT not in text))
print("   names both kinds   :", ok("a screenshot" in text and "the DOM" in text))

print()
print("=== 3. Screenshot only: does not claim to have the DOM ===")
p2 = RenderedPreviewPayload(url="http://x/", screenshot_base64=SHOT, screenshot_media_type="image/png")
ctx2 = cb.build(s, AgentRole.VISUAL_UX_CRITIC, task="critique", rendered_preview=p2)
a2 = VisualUxCriticAgent(StubModel(OUT))
c2 = a2.prompt_template(ctx2).format_messages(**a2.prompt_variables(ctx2))[1].content
t2 = " ".join(b.get("text", "") for b in c2 if b.get("type") == "text")
print("   has image block :", ok(any(b.get("type") == "image" for b in c2)))
print("   no DOM block    :", ok("RENDERED PAGE — DOM" not in t2))
print("   says screenshot only:", ok("a screenshot" in t2 and "the DOM, which" not in t2))

print()
print("=== 4. DOM only: no image block, warns off appearance claims ===")
p3 = RenderedPreviewPayload(url="http://x/", dom_html=DOM)
ctx3 = cb.build(s, AgentRole.VISUAL_UX_CRITIC, task="critique", rendered_preview=p3)
a3 = VisualUxCriticAgent(StubModel(OUT))
c3 = a3.prompt_template(ctx3).format_messages(**a3.prompt_variables(ctx3))[1].content
t3 = " ".join(b.get("text", "") for b in c3 if b.get("type") == "text")
print("   no image block  :", ok(not any(b.get("type") == "image" for b in c3)))
print("   DOM present     :", ok(DOM in t3))
print("   warns not to infer:", ok("do not infer the part you were not" in t3))

print()
print("=== 5. NO evidence = stop before any model call ===")
for label, kw in [("no preview at all", {}),
                  ("bare URL string", {"rendered_preview": "http://just-a-url/"})]:
    c = cb.build(s, AgentRole.VISUAL_UX_CRITIC, task="critique", **kw)
    m = StubModel(OUT); r = asyncio.run(VisualUxCriticAgent(m).run(c))
    print("   %-18s ok=%-5s kind=%-18s calls=%d %s" % (
        label, r.ok, r.failure.kind.value if r.failure else "-", m.inner.calls,
        ok(not r.ok and r.failure.kind is AgentFailureKind.CONTRACT_VIOLATION and m.inner.calls == 0)))
    print("                      names the missing variable:", ok("rendered_evidence" in r.failure.message))

print()
print("=== 6. Chain + run ===")
chain = agent.build_chain(tmpl)
print("   chain :", type(chain).__name__, ok(isinstance(chain, RunnableSequence)))
res = asyncio.run(VisualUxCriticAgent(StubModel(OUT)).run(ctx))
print("   run ok:", ok(res.ok), "| findings:", len(res.output.findings))

print()
print("=== 7. Prompt teaches the conflict rule and no-quota rule ===")
for phrase in ["conflicts_with_approved_decision", "Taste is not a finding",
               "Returning no findings is a real", "`source` is `visual_ux_critic`",
               "not the remedy"]:
    print("   %-38s %s" % (phrase[:38], ok(phrase in text)))

print()
print("=== 8. Purity ===")
src = open("backend/agents/visual_ux_critic.py", encoding="utf-8").read()
print("   reads ProjectState:", ok("ProjectState" not in src))
print("   overrides run()   :", ok("def run(" not in src))
print("   imports langgraph :", ok(not re.search(r"^\s*(from|import)\s+langgraph", src, re.M)))
