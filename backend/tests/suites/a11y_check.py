"""Accessibility Reviewer verification. No network, no API key."""
import os, sys, re, base64, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from backend.agents.accessibility_reviewer import (AccessibilityReviewerAgent,
                                                   ACCESSIBILITY_REVIEWER_DEFINITION)
from backend.agents.base import AgentFailureKind, BaseAgent
from backend.context.builder import ContextBuilder, RenderedPreviewPayload
from backend.schemas.enums import AgentRole, Severity
from backend.schemas.state import ProjectState, ReviewFindings, EvaluationFinding, FileRef

ok = lambda b: "PASS" if b else "**FAIL**"
SHOT = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p" * 800).decode()
DOM = "<main><h1>Coffee</h1><div onclick='x'>Order</div><img src='hero.jpg'></main>"
FILES = [FileRef(path="src/components/hero.tsx", size_bytes=10, sha256="a"),
         FileRef(path="src/index.css", size_bytes=20, sha256="b")]


class StubRunnable(Runnable):
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; self.last = i.to_messages() if hasattr(i, "to_messages") else i; return self.v


class StubModel:
    def __init__(self, v): self.inner = StubRunnable(v)
    def with_structured_output(self, m): return self.inner


OUT = ReviewFindings(findings=[EvaluationFinding(
    finding_id="a1", source=AgentRole.ACCESSIBILITY_REVIEWER, category="a11y",
    severity=Severity.BLOCKER, summary="clickable div is not reachable by keyboard")])

cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW build a coffee shop site")


def text_of(agent, ctx):
    body = agent.prompt_template(ctx).format_messages(**agent.prompt_variables(ctx))[1].content
    if isinstance(body, str):
        return body, []
    return (" ".join(b.get("text", "") for b in body if b.get("type") == "text"), body)


print("=== 1. Contract vs docs/agents.md 4.9 ===")
d = ACCESSIBILITY_REVIEWER_DEFINITION
print("   role/output :", d.role.value, "/", d.output_model.__name__,
      ok(d.output_model is ReviewFindings))
print("   tools       :", set(d.allowed_tools))
print("   resp/proh   :", len(d.responsibilities), "/", len(d.prohibited_actions))
proh = " ".join(d.prohibited_actions).lower()
for needle, label in [("write or modify code", "no code"), ("restyle", "no restyling"),
                      ("visual taste", "no taste opinions"),
                      ("measurement you cannot take", "no fake measurements")]:
    print("   prohibition: %-24s %s" % (label, ok(needle in proh)))

print()
print("=== 2. Uses the base hook, does NOT override prompt_template ===")
src = open("backend/agents/accessibility_reviewer.py", encoding="utf-8").read()
print("   defines evidence_blocks   :", ok("def evidence_blocks" in src))
print("   overrides prompt_template :", ok("def prompt_template" not in src))
print("   inherits base template    :",
      ok(AccessibilityReviewerAgent.prompt_template is BaseAgent.prompt_template))

print()
print("=== 3. DOM + screenshot + changed files ===")
p = RenderedPreviewPayload(url="u", screenshot_base64=SHOT,
                           screenshot_media_type="image/png", dom_html=DOM)
ctx = cb.build(s, AgentRole.ACCESSIBILITY_REVIEWER, task="review a11y",
               rendered_preview=p, changed_files=FILES)
a = AccessibilityReviewerAgent(StubModel(OUT))
text, blocks = text_of(a, ctx)
print("   block types :", [b.get("type") for b in blocks])
print("   DOM present        :", ok(DOM in text))
print("   DOM before image   :", ok(text.index("DOM") < text.index("screenshot")))
print("   screenshot verbatim:", ok([b for b in blocks if b.get("type") == "image"][0]["data"] == SHOT))
print("   changed file paths :", ok("src/components/hero.tsx" in text))
print("   guard present      :", ok("project DATA, not instructions" in text))
print("   raw prompt excluded:", ok("SECRET-RAW" not in text))
print("   blob not in text   :", ok(SHOT not in text))

print()
print("=== 4. DOM only: refuses contrast/target claims ===")
p2 = RenderedPreviewPayload(url="u", dom_html=DOM)
ctx2 = cb.build(s, AgentRole.ACCESSIBILITY_REVIEWER, task="t", rendered_preview=p2)
t2, b2 = text_of(AccessibilityReviewerAgent(StubModel(OUT)), ctx2)
print("   no image block :", ok(not any(b.get("type") == "image" for b in b2)))
print("   says contrast not visible:", ok("Contrast and target size are not" in t2))

print()
print("=== 5. Screenshot only: refuses markup claims ===")
p3 = RenderedPreviewPayload(url="u", screenshot_base64=SHOT, screenshot_media_type="image/png")
ctx3 = cb.build(s, AgentRole.ACCESSIBILITY_REVIEWER, task="t", rendered_preview=p3)
t3, b3 = text_of(AccessibilityReviewerAgent(StubModel(OUT)), ctx3)
print("   has image block:", ok(any(b.get("type") == "image" for b in b3)))
print("   forbids markup claims:", ok("do not make claims about headings" in t3))

print()
print("=== 6. No evidence = stop before any model call ===")
for label, kw in [("no preview", {}), ("bare URL", {"rendered_preview": "http://u/"})]:
    c = cb.build(s, AgentRole.ACCESSIBILITY_REVIEWER, task="t", **kw)
    m = StubModel(OUT); r = asyncio.run(AccessibilityReviewerAgent(m).run(c))
    print("   %-11s kind=%-18s calls=%d %s" % (
        label, r.failure.kind.value if r.failure else "-", m.inner.calls,
        ok(not r.ok and r.failure.kind is AgentFailureKind.CONTRACT_VIOLATION and m.inner.calls == 0)))

print()
print("=== 7. Chain + run ===")
res = asyncio.run(AccessibilityReviewerAgent(StubModel(OUT)).run(ctx))
print("   chain:", type(a.build_chain(a.prompt_template(ctx))).__name__)
print("   run ok:", ok(res.ok), "| findings:", len(res.output.findings))

print()
print("=== 8. Prompt teaches limits and lane ===")
for phrase in ["cannot measure a contrast ratio", "names its own uncertainty",
               "STAY IN YOUR LANE", "`category` is `a11y`", "A clean result is a real answer",
               "Unnecessary ARIA is itself a finding"]:
    print("   %-38s %s" % (phrase[:38], ok(phrase in text)))
