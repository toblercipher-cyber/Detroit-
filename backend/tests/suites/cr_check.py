"""Code Reviewer verification. No network, no API key."""
import os, sys, re, asyncio
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.runnables import Runnable, RunnableSequence
from backend.agents.code_reviewer import CodeReviewerAgent, CODE_REVIEWER_DEFINITION
from backend.agents.base import AgentFailureKind, BaseAgent
from backend.context.builder import ContextBuilder, ChangedFilePayload
from backend.schemas.enums import AgentRole, Severity
from backend.schemas.state import (ProjectState, Plan, DesignSpec, ReviewFindings,
                                   EvaluationFinding, FileRef, ApprovedDecision)
from backend.schemas.enums import WorkflowStage

ok = lambda b: "PASS" if b else "**FAIL**"
F1 = ChangedFilePayload(path="src/components/hero.tsx",
                        content="import { useTilt } from './use-tilt';\nexport function Hero() {\n  return <section>{title}</section>;\n}\n")
F2 = ChangedFilePayload(path="src/lib/keys.ts", content="export const KEY = 'sk-not-a-real-key';\n")


class StubRunnable(Runnable):
    def __init__(self, v): self.v, self.calls, self.last = v.to_messages() if hasattr(v, "to_messages") else v, 0, None
    def invoke(self, i, config=None, **k): raise NotImplementedError
    async def ainvoke(self, i, config=None, **k):
        self.calls += 1; self.last = i.to_messages() if hasattr(i, "to_messages") else i; return self.v


class StubModel:
    def __init__(self, v): self.inner = StubRunnable(v)
    def with_structured_output(self, m): return self.inner


OUT = ReviewFindings(findings=[EvaluationFinding(
    finding_id="c1", source=AgentRole.CODE_REVIEWER, category="code",
    severity=Severity.BLOCKER, summary="credential in source")])

cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW build a coffee shop site")
s.plan = Plan(objective="PLAN-MARKER sell coffee")
s.design_spec = DesignSpec(font_families=["DESIGN-MARKER"])
s.approved_decisions.append(ApprovedDecision(decision_id="d", stage=WorkflowStage.DESIGN,
                                             statement="DECISION-MARKER"))


def text_of(agent, ctx):
    body = agent.prompt_template(ctx).format_messages(**agent.prompt_variables(ctx))[1].content
    if isinstance(body, str):
        return body
    return " ".join(b.get("text", "") for b in body if b.get("type") == "text")


print("=== 1. Contract vs docs/agents.md 4.10 ===")
d = CODE_REVIEWER_DEFINITION
print("   role/output :", d.role.value, "/", d.output_model.__name__,
      ok(d.output_model is ReviewFindings))
print("   tools       :", set(d.allowed_tools), ok(d.allowed_tools == frozenset({"repository_read"})))
print("   resp/proh   :", len(d.responsibilities), "/", len(d.prohibited_actions))
proh = " ".join(d.prohibited_actions).lower()
for needle, label in [("write or modify code", "no code authoring"),
                      ("visual design", "no design opinions"),
                      ("outside the change set", "no out-of-set review"),
                      ("not shown to you", "no false broken-import")]:
    print("   prohibition: %-26s %s" % (label, ok(needle in proh)))

print()
print("=== 2. Uses the hook; no prompt_template override ===")
src = open("backend/agents/code_reviewer.py", encoding="utf-8").read()
print("   defines evidence_blocks  :", ok("def evidence_blocks" in src))
print("   overrides prompt_template:", ok("def prompt_template" not in src))
print("   inherits base template   :", ok(CodeReviewerAgent.prompt_template is BaseAgent.prompt_template))

print()
print("=== 3. 'changed files only' is literally true ===")
ctx = cb.build(s, AgentRole.CODE_REVIEWER, task="review", changed_files=[F1, F2])
text = text_of(CodeReviewerAgent(StubModel(OUT)), ctx)
print("   state slice empty  :", ok(ctx.state == {}))
print("   no constraints     :", ok(ctx.constraints == []))
print("   plan NOT in prompt :", ok("PLAN-MARKER" not in text))
print("   design NOT in prompt:", ok("DESIGN-MARKER" not in text))
print("   decision NOT in prompt:", ok("DECISION-MARKER" not in text))
print("   raw prompt excluded:", ok("SECRET-RAW" not in text))

print()
print("=== 4. File contents actually reach the prompt ===")
print("   file 1 path   :", ok("src/components/hero.tsx" in text))
print("   file 1 source :", ok("useTilt" in text))
print("   file 2 source :", ok("sk-not-a-real-key" in text))
print("   braces survive:", ok("{title}" in text))
print("   files delimited by path:", ok("--- src/lib/keys.ts ---" in text))
print("   counts files in task:", ok("2 changed file(s)" in text))

print()
print("=== 5. Nothing to review = stop before any model call ===")
for label, kw in [("no changed_files", {}),
                  ("empty list", {"changed_files": []}),
                  ("FileRef (no content)", {"changed_files": [FileRef(path="a.ts", size_bytes=1, sha256="x")]})]:
    c = cb.build(s, AgentRole.CODE_REVIEWER, task="review", **kw)
    m = StubModel(OUT); r = asyncio.run(CodeReviewerAgent(m).run(c))
    good = (not r.ok and r.failure.kind is AgentFailureKind.CONTRACT_VIOLATION and m.inner.calls == 0)
    print("   %-22s kind=%-18s calls=%d %s" % (
        label, r.failure.kind.value if r.failure else "-", m.inner.calls, ok(good)))

print()
print("=== 6. Chain + run ===")
a = CodeReviewerAgent(StubModel(OUT))
print("   chain :", type(a.build_chain(a.prompt_template(ctx))).__name__)
res = asyncio.run(a.run(ctx))
print("   run ok:", ok(res.ok), "| findings:", len(res.output.findings))

print()
print("=== 7. Prompt teaches defect-vs-question and scope ===")
for phrase in ["DEFECT OR QUESTION?", "you simply cannot confirm it",
               "a confident wrong finding costs more", "NOT YOUR REVIEW",
               "You were not given the plan", "`category` is `code`",
               "Clean code is a real answer", "not a patch"]:
    print("   %-40s %s" % (phrase[:40], ok(phrase in text)))
