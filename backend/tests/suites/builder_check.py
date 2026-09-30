"""Builder agent verification — real filesystem, stub model. No network, no API key."""
import os, sys, tempfile, shutil, asyncio
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from langchain_core.messages import AIMessage
from backend.agents.builder import BuilderAgent, BUILDER_DEFINITION
from backend.agents.base import AgentFailureKind
from backend.context.builder import ContextBuilder, ChangedFilePayload
from backend.schemas.enums import AgentRole
from backend.schemas.state import ProjectState, Plan, DesignSpec, ComponentTree, ComponentNode, BuildResult
from backend.tools.workspace import WorkspaceFiles

ok = lambda b: "PASS" if b else "**FAIL**"


class ScriptedModel:
    """Replays a scripted list of AIMessage replies; records what it was sent."""
    def __init__(self, replies): self.replies, self.calls, self.seen = list(replies), 0, []
    def bind_tools(self, tools): self.tools = tools; return self
    def with_structured_output(self, m): raise AssertionError("builder should not use structured output")
    async def ainvoke(self, messages, config=None, **k):
        self.calls += 1; self.seen.append(messages)
        return self.replies.pop(0) if self.replies else AIMessage(content="done")


def call(name, args, cid):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


root = Path(tempfile.mkdtemp(prefix="builder-ws-"))
try:
    (root / "src").mkdir()
    (root / "package.json").write_text('{"name":"t"}', encoding="utf-8")
    (root / "src" / "App.tsx").write_text("old", encoding="utf-8")
    ws = WorkspaceFiles(root)

    cb = ContextBuilder()
    s = ProjectState(user_request="SECRET-RAW")
    s.plan = Plan(objective="sell coffee", acceptance_criteria=["nav works"])
    s.design_spec = DesignSpec(font_families=["Inter"], radius="0.375rem")
    s.component_tree = ComponentTree(root=[ComponentNode(name="Hero")])
    ctx = cb.build(s, AgentRole.BUILDER, task="build the hero")

    print("=== 1. Contract vs docs/agents.md 4.6 ===")
    d = BUILDER_DEFINITION
    print("   output model:", d.output_model.__name__, ok(d.output_model is BuildResult))
    print("   tools       :", sorted(d.allowed_tools))
    proh = " ".join(d.prohibited_actions).lower()
    for needle, label in [("outside the plan", "no out-of-plan files"),
                          ("rewrite unrelated", "no unrelated rewrites"),
                          ("regenerate the whole project", "no full regen"),
                          ("pin a dependency", "no dependency changes"),
                          ("builder-owned template", "no template edits"),
                          ("backend for the site", "frontend only")]:
        print("   prohibition: %-26s %s" % (label, ok(needle in proh)))

    print()
    print("=== 2. Happy path: writes land on disk, report is observed ===")
    m = ScriptedModel([
        AIMessage(content="", tool_calls=[
            call("write_workspace_file", {"path": "src/Hero.tsx", "content": "// hero\nexport const Hero=()=>null\n"}, "1"),
            call("write_workspace_file", {"path": "src/App.tsx", "content": "// app\n"}, "2"),
        ]),
        AIMessage(content="Added Hero and wired it into App."),
    ])
    res = asyncio.run(BuilderAgent(m, ws).run(ctx))
    print("   run ok        :", ok(res.ok))
    r = res.output
    print("   files_written :", r.files_written, ok(r.files_written == ["src/Hero.tsx"]))
    print("   files_modified:", r.files_modified, ok(r.files_modified == ["src/App.tsx"]))
    print("   new file on disk  :", ok((root / "src" / "Hero.tsx").read_text(encoding="utf-8").startswith("// hero")))
    print("   existing overwritten:", ok((root / "src" / "App.tsx").read_text(encoding="utf-8") == "// app\n"))
    print("   closing note kept :", ok(any("Added Hero" in n for n in r.notes)))
    print("   no deviations     :", ok(r.deviations_from_plan == []))

    print()
    print("=== 3. Refused writes: boundary holds, model is told, report records it ===")
    m2 = ScriptedModel([
        AIMessage(content="", tool_calls=[
            call("write_workspace_file", {"path": "vite.config.ts", "content": "pwned"}, "1"),
            call("write_workspace_file", {"path": "../escape.txt", "content": "pwned"}, "2"),
            call("write_workspace_file", {"path": "package.json", "content": "{}"}, "3"),
        ]),
        AIMessage(content="could not write config"),
    ])
    res2 = asyncio.run(BuilderAgent(m2, ws).run(ctx))
    r2 = res2.output
    print("   run still ok (refusal is not a crash):", ok(res2.ok))
    print("   nothing written:", ok(r2.files_written == [] and r2.files_modified == []))
    print("   deviations recorded:", len(r2.deviations_from_plan), ok(len(r2.deviations_from_plan) == 3))
    for dv in r2.deviations_from_plan:
        print("     -", dv[:78])
    print("   vite.config.ts untouched:", ok((root / "vite.config.ts").exists() is False))
    print("   package.json untouched  :", ok((root / "package.json").read_text(encoding="utf-8") == '{"name":"t"}'))
    print("   nothing escaped the root:", ok(not (root.parent / "escape.txt").exists()))
    tool_msgs = [msg for msg in m2.seen[-1] if type(msg).__name__ == "ToolMessage"]
    print("   model told why (ToolMessage):", ok(any("Refused" in str(t.content) for t in tool_msgs)))

    print()
    print("=== 4. Unknown tool is refused, not executed ===")
    m3 = ScriptedModel([
        AIMessage(content="", tool_calls=[call("run_shell", {"cmd": "rm -rf /"}, "1")]),
        AIMessage(content="ok"),
    ])
    r3 = asyncio.run(BuilderAgent(m3, ws).run(ctx)).output
    print("   recorded as deviation:", ok(any("No such tool" in d for d in r3.deviations_from_plan)))

    print()
    print("=== 5. Runaway loop is bounded ===")
    loop = [AIMessage(content="", tool_calls=[call("write_workspace_file", {"path": "vite.config.ts", "content": "x"}, str(i))]) for i in range(50)]
    m4 = ScriptedModel(loop)
    r4 = asyncio.run(BuilderAgent(m4, ws, max_tool_iterations=3).run(ctx)).output
    print("   model calls capped:", m4.calls, ok(m4.calls == 3))
    print("   note explains the stop:", ok(any("Stopped after 3 tool rounds" in n for n in r4.notes)))

    print()
    print("=== 6. Context and prompt ===")
    sent = m.seen[0]
    text = sent[1].content if isinstance(sent[1].content, str) else str(sent[1].content)
    print("   plan present      :", ok("sell coffee" in text))
    print("   design spec present:", ok("0.375rem" in text))
    print("   acceptance criteria:", ok("nav works" in text))
    print("   raw prompt excluded:", ok("SECRET-RAW" not in text))
    print("   states src/ limit  :", ok("Paths under `src/` only" in text))
    print("   forbids dependency :", ok("cannot add a dependency" in text))

    print()
    print("=== 7. Wrong context refused before any call ===")
    wrong = cb.build(s, AgentRole.PLANNER, task="x")
    m5 = ScriptedModel([])
    r5 = asyncio.run(BuilderAgent(m5, ws).run(wrong))
    print("   refused:", ok(not r5.ok and m5.calls == 0), "| kind:", r5.failure.kind.value)

    print()
    print("=== 8. Provider failure classified ===")
    class Boom(ScriptedModel):
        async def ainvoke(self, messages, config=None, **k):
            self.calls += 1; raise RuntimeError("connection reset")
    r6 = asyncio.run(BuilderAgent(Boom([]), ws).run(ctx))
    print("   kind:", r6.failure.kind.value, ok(r6.failure.kind is AgentFailureKind.PROVIDER_ERROR))
    print("   no provider text leaked:", ok("connection reset" not in r6.failure.message))
finally:
    shutil.rmtree(root, ignore_errors=True)
print()
print("  (all writes in a temp workspace; project tree untouched)")
