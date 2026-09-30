"""Anti-Slop Reviewer verification. No network, no API key."""
import os, sys, re, base64, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent, ANTI_SLOP_REVIEWER_DEFINITION
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder, RenderedPreviewPayload
from backend.schemas.enums import AgentRole, Severity, WorkflowStage
from backend.schemas.state import (ProjectState, DesignSpec, ReviewFindings,
                                   EvaluationFinding, ApprovedDecision)

ok = lambda b: "PASS" if b else "**FAIL**"
SHOT = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p" * 1500).decode()
DOM = "<main><section class='card'>A</section></main>"

class StubRunnable(Runnable):
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; self.last = i.to_messages() if hasattr(i, "to_messages") else i; return self.v
class StubModel:
    def __init__(self, v): self.inner = StubRunnable(v)
    def with_structured_output(self, m): return self.inner

OUT = ReviewFindings(findings=[EvaluationFinding(
    finding_id="a1", source=AgentRole.ANTI_SLOP_REVIEWER, category="anti_slop",
    severity=Severity.MAJOR, summary="three identical feature cards")])

cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.design_spec = DesignSpec(font_families=["Inter"], visual_tone="warm editorial")
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN,
                                             statement="The hero gradient is intentional"))
preview = RenderedPreviewPayload(url="http://x/", screenshot_base64=SHOT,
                                 screenshot_media_type="image/png", dom_html=DOM)
detected = [EvaluationFinding(finding_id="imp-1", source=AgentRole.ANTI_SLOP_REVIEWER,
                              category="anti_slop", severity=Severity.MINOR,
                              summary="gradient detected in hero")]

print("=== 1. Contract vs docs/agents.md 4.8 ===")
d = ANTI_SLOP_REVIEWER_DEFINITION
print("   role/output :", d.role.value, "/", d.output_model.__name__,
      ok(d.output_model is ReviewFindings))
print("   allowed_tools:", set(d.allowed_tools))
print("   Impeccable NOT declared as a tool:", ok("impeccable" not in {t.lower() for t in d.allowed_tools}))
proh = " ".join(d.prohibited_actions).lower()
for needle, label in [("stand in for the detector", "no substitute"),
                      ("blocked or skipped check", "no false pass"),
                      ("proof that something is wrong", "finding != proof"),
                      ("clean run as proof", "clean != good"),
                      ("approved decision", "conflict not fix"),
                      ("novelty", "no novelty")]:
    print("   prohibition: %-22s %s" % (label, ok(needle in proh)))
print("   returns no gate fields:", ok(not ({"impeccable_available","gate_status"} & set(ReviewFindings.model_fields))))

print()
print("=== 2. Detector findings + evidence reach the prompt ===")
ctx = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="interpret",
               rendered_preview=preview, findings=detected)
a = AntiSlopReviewerAgent(StubModel(OUT))
msgs = a.prompt_template(ctx).format_messages(**a.prompt_variables(ctx))
content = msgs[1].content
text = " ".join(b.get("text","") for b in content if b.get("type")=="text")
print("   block types :", [b.get("type") for b in content])
print("   detector finding present:", ok("gradient detected in hero" in text))
print("   screenshot verbatim     :", ok([b for b in content if b.get("type")=="image"][0]["data"] == SHOT))
print("   DOM present             :", ok(DOM in text))
print("   design_spec present     :", ok("warm editorial" in text))
print("   approved decision as constraint:", ok("hero gradient is intentional" in text))
print("   checklist embedded      :", ok("glassmorphism" in text and "generic SaaS" in text))
print("   raw prompt excluded     :", ok("SECRET-RAW" not in text))
print("   blob not in text        :", ok(SHOT not in text))

print()
print("=== 3. Clean detector run ([]) is handled as its own case ===")
ctx_clean = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="interpret",
                     rendered_preview=preview, findings=[])
a2 = AntiSlopReviewerAgent(StubModel(ReviewFindings()))
t2 = " ".join(b.get("text","") for b in
              a2.prompt_template(ctx_clean).format_messages(**a2.prompt_variables(ctx_clean))[1].content
              if b.get("type")=="text")
print("   says detector reported nothing:", ok("ran and reported nothing" in t2))
print("   warns clean != good           :", ok("not the same as the design being good" in t2))
r2 = asyncio.run(a2.run(ctx_clean))
print("   runs fine with zero findings  :", ok(r2.ok), "| findings:", len(r2.output.findings))

print()
print("=== 4. NO detector run = stop before any model call ===")
ctx_none = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="interpret", rendered_preview=preview)
m = StubModel(OUT); r = asyncio.run(AntiSlopReviewerAgent(m).run(ctx_none))
print("   ok:", r.ok, ok(not r.ok), "| kind:", r.failure.kind.value)
print("   names detector_findings:", ok("detector_findings" in r.failure.message))
print("   model NOT called       :", ok(m.inner.calls == 0))

print()
print("=== 5. No rendered evidence = also stops ===")
ctx_noev = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="interpret", findings=detected)
m3 = StubModel(OUT); r3 = asyncio.run(AntiSlopReviewerAgent(m3).run(ctx_noev))
print("   refused:", ok(not r3.ok and m3.inner.calls == 0), "| names:", 
      "rendered_evidence" in r3.failure.message)

print()
print("=== 6. Chain + run ===")
print("   chain:", type(a.build_chain(a.prompt_template(ctx))).__name__)
res = asyncio.run(AntiSlopReviewerAgent(StubModel(OUT)).run(ctx))
print("   run ok:", ok(res.ok), "| findings:", len(res.output.findings))

print()
print("=== 7. Prompt teaches the three outcomes ===")
for phrase in ["1. Genuine slop", "2. An approved decision", "3. Not material here",
               "conflicts_with_approved_decision", "must never undo something the user",
               "`source` is `anti_slop_reviewer`", "Coherent and purposeful beats unusual"]:
    print("   %-40s %s" % (phrase[:40], ok(phrase in text)))
print("   forbids commenting on gate status:", ok("Do not comment" in text and "whether it ran" in text))
