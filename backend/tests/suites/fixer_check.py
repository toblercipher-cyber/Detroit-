"""Fixer agent verification — real filesystem, scripted model. No network, no API key."""
import os, sys, tempfile, shutil, asyncio
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.messages import AIMessage
from backend.agents.fixer import FixerAgent, FIXER_DEFINITION
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder, ChangedFilePayload
from backend.schemas.enums import AgentRole, Severity, WorkflowStage
from backend.schemas.state import (ProjectState, ApprovedDecision, EvaluationFinding,
                                   FixResult)
from backend.tools.workspace import WorkspaceFiles
from backend.tests.suites import _tmpdir

ok = lambda b: "PASS" if b else "**FAIL**"


class ScriptedModel:
    """Replays tool-call replies, then a structured FixResult."""
    def __init__(self, replies, report=None):
        self.replies, self.report = list(replies), report
        self.calls, self.structured_calls, self.seen = 0, 0, []

    def bind_tools(self, tools): self.tools = tools; return self

    def with_structured_output(self, schema):
        outer = self
        class _S:
            async def ainvoke(self, messages, config=None, **k):
                outer.structured_calls += 1; outer.report_messages = messages
                return outer.report if outer.report is not None else FixResult()
        return _S()

    async def ainvoke(self, messages, config=None, **k):
        self.calls += 1; self.seen.append(messages)
        return self.replies.pop(0) if self.replies else AIMessage(content="done")


def call(path, content, cid, name="write_workspace_file"):
    return {"name": name, "args": {"path": path, "content": content}, "id": cid, "type": "tool_call"}


root = Path(_tmpdir.mkdtemp(prefix="fixer-ws-"))
try:
    (root / "src").mkdir()
    (root / "src" / "Hero.tsx").write_text("old hero", encoding="utf-8")
    (root / "src" / "Other.tsx").write_text("untouched", encoding="utf-8")
    ws = WorkspaceFiles(root)

    cb = ContextBuilder()
    s = ProjectState(user_request="SECRET-RAW")
    s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN,
                                                 statement="The hero gradient is intentional"))
    hero = ChangedFilePayload(path="src/Hero.tsx", content="old hero")

    f_ok = EvaluationFinding(finding_id="f1", source=AgentRole.CODE_REVIEWER, category="code",
                             severity=Severity.MAJOR, summary="unused import",
                             location="src/Hero.tsx", suggested_fix="remove it")
    f_conf = EvaluationFinding(finding_id="f9", source=AgentRole.ANTI_SLOP_REVIEWER,
                               category="anti_slop", severity=Severity.MINOR,
                               summary="gradient in hero", location="src/Hero.tsx",
                               conflicts_with_approved_decision=True)

    print("=== 1. Contract vs docs/agents.md 4.11 ===")
    d = FIXER_DEFINITION
    print("   output:", d.output_model.__name__, ok(d.output_model is FixResult))
    print("   tools :", sorted(d.allowed_tools))
    proh = " ".join(d.prohibited_actions).lower()
    for needle, label in [("unrelated modifications", "no unrelated edits"),
                          ("redesign", "no redesign"),
                          ("regenerate the whole project", "no full regen"),
                          ("conflicts with an approved decision", "escalate conflicts"),
                          ("files you were not given", "scoped writes")]:
        print("   prohibition: %-24s %s" % (label, ok(needle in proh)))

    print()
    print("=== 2. Happy path: fixes in-scope file, report reconciled ===")
    ctx = cb.build(s, AgentRole.FIXER, task="apply fixes", findings=[f_ok], changed_files=[hero])
    m = ScriptedModel([AIMessage(content="", tool_calls=[call("src/Hero.tsx", "new hero", "1")]),
                       AIMessage(content="done")],
                      report=FixResult(findings_addressed=["f1"], rationale="removed the import"))
    res = asyncio.run(FixerAgent(m, ws).run(ctx))
    r = res.output
    print("   run ok         :", ok(res.ok))
    print("   files_modified :", r.files_modified, ok(r.files_modified == ["src/Hero.tsx"]))
    print("   on disk        :", ok((root / "src" / "Hero.tsx").read_text(encoding="utf-8") == "new hero"))
    print("   addressed      :", r.findings_addressed, ok(r.findings_addressed == ["f1"]))
    print("   nothing escalated:", ok(r.escalated_to_user == []))
    print("   structured report requested once:", ok(m.structured_calls == 1))

    print()
    print("=== 3. Out-of-scope write refused even though src/ would allow it ===")
    m2 = ScriptedModel([AIMessage(content="", tool_calls=[call("src/Other.tsx", "hacked", "1")]),
                        AIMessage(content="done")],
                       report=FixResult(findings_addressed=["f1"]))
    r2 = asyncio.run(FixerAgent(m2, ws).run(ctx)).output
    print("   not written    :", ok(r2.files_modified == []))
    print("   file untouched :", ok((root / "src" / "Other.tsx").read_text(encoding="utf-8") == "untouched"))
    print("   recorded unresolved:", ok(any("out of scope" in u for u in r2.unresolved)))
    tool_msgs = [x for x in m2.seen[-1] if type(x).__name__ == "ToolMessage"]
    print("   model told why :", ok(any("not one of the affected files" in str(t.content) for t in tool_msgs)))

    print()
    print("=== 4. Conflicted finding cannot be reported as fixed ===")
    ctx2 = cb.build(s, AgentRole.FIXER, task="apply fixes",
                    findings=[f_ok, f_conf], changed_files=[hero])
    m3 = ScriptedModel([AIMessage(content="", tool_calls=[call("src/Hero.tsx", "v2", "1")]),
                        AIMessage(content="done")],
                       # model dishonestly claims it fixed the conflicted one too
                       report=FixResult(findings_addressed=["f1", "f9"]))
    r3 = asyncio.run(FixerAgent(m3, ws).run(ctx2)).output
    print("   addressed strips f9:", r3.findings_addressed, ok(r3.findings_addressed == ["f1"]))
    print("   escalated computed :", r3.escalated_to_user, ok(r3.escalated_to_user == ["f9"]))
    print("   rationale notes it :", ok("escalated" in (r3.rationale or "")))
    print("   conflicted finding NOT in prompt:",
          ok("gradient in hero" not in str(m3.seen[0][1].content)))

    print()
    print("=== 5. All findings conflict = no model call at all ===")
    ctx3 = cb.build(s, AgentRole.FIXER, task="apply fixes", findings=[f_conf], changed_files=[hero])
    m4 = ScriptedModel([])
    r4 = asyncio.run(FixerAgent(m4, ws).run(ctx3))
    print("   ok:", ok(r4.ok), "| escalated:", r4.output.escalated_to_user)
    print("   zero model calls  :", ok(m4.calls == 0 and m4.structured_calls == 0))
    print("   nothing addressed :", ok(r4.output.findings_addressed == []))

    print()
    print("=== 6. Missing findings or files stops before any call ===")
    for label, kw in [("no findings", dict(changed_files=[hero])),
                      ("no files", dict(findings=[f_ok]))]:
        c = cb.build(s, AgentRole.FIXER, task="t", **kw)
        m5 = ScriptedModel([])
        r5 = asyncio.run(FixerAgent(m5, ws).run(c))
        print("   %-12s kind=%-18s calls=%d %s" % (
            label, r5.failure.kind.value if r5.failure else "-", m5.calls,
            ok(not r5.ok and r5.failure.kind is AgentFailureKind.CONTRACT_VIOLATION and m5.calls == 0)))

    print()
    print("=== 7. Loop bounded; wrong context refused ===")
    loop = [AIMessage(content="", tool_calls=[call("src/Other.tsx", "x", str(i))]) for i in range(30)]
    m6 = ScriptedModel(loop, report=FixResult())
    asyncio.run(FixerAgent(m6, ws, max_tool_iterations=3).run(ctx))
    print("   capped at 3:", m6.calls, ok(m6.calls == 3))
    wrong = cb.build(s, AgentRole.CODE_REVIEWER, task="x", changed_files=[hero])
    m7 = ScriptedModel([])
    r7 = asyncio.run(FixerAgent(m7, ws).run(wrong))
    print("   wrong context refused:", ok(not r7.ok and m7.calls == 0), "|", r7.failure.kind.value)

    print()
    print("=== 8. Prompt content ===")
    text = str(m.seen[0][1].content)
    print("   finding listed     :", ok("[f1]" in text and "unused import" in text))
    print("   file contents given:", ok("old hero" in text))
    print("   scope enumerated   :", ok("src/Hero.tsx" in text))
    print("   approved decision  :", ok("hero gradient is intentional" in text))
    print("   raw prompt excluded:", ok("SECRET-RAW" not in text))
    print("   smallest-change rule:", ok("smallest change" in text.lower()))
finally:
    shutil.rmtree(root, ignore_errors=True)
print()
print("  (all writes in a temp workspace; project tree untouched)")
