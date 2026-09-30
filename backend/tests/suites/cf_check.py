"""ChangedFilePayload verification. No network, no API key."""
import os, sys
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import ValidationError
from backend.context.builder import (ContextBuilder, ContextInput, ContextPermissionError,
                                     ChangedFilePayload, RenderedPreviewPayload)
from backend.schemas.enums import AgentRole
from backend.schemas.state import ProjectState, FileRef, Plan, DesignSpec

ok = lambda b: "PASS" if b else "**FAIL**"
CODE = "export function Hero() {\n  return <section>{title}</section>;\n}\n"

print("=== 1. Carries what FileRef could not ===")
c = ChangedFilePayload(path="src/components/hero.tsx", content=CODE)
print("   has content        :", ok("content" in ChangedFilePayload.model_fields))
print("   FileRef still lacks it:", ok("content" not in FileRef.model_fields))
print("   content preserved  :", ok(c.content == CODE))
for bad, label in [(dict(path="", content=CODE), "empty path"),
                   (dict(path="p", content=CODE, extra="x"), "extra field")]:
    try:
        ChangedFilePayload(**bad); print("   %-14s NOT REJECTED **FAIL**" % label)
    except ValidationError:
        print("   %-14s rejected %s" % (label, ok(True)))
print("   empty content allowed (a file can be emptied):",
      ok(ChangedFilePayload(path="p", content="").content == ""))

print()
print("=== 2. Braces in source survive (the template hazard) ===")
print("   JSX/TS braces kept verbatim:", ok("{title}" in c.content))

print()
print("=== 3. repr summarises, never dumps source ===")
r = repr(c)
print("   repr:", r)
print("   source absent:", ok(CODE not in r))

print()
print("=== 4. Not part of persisted state ===")
print("   ProjectState has no ChangedFilePayload field:",
      ok("ChangedFilePayload" not in str(ProjectState.model_fields)))

print()
print("=== 5. Reaches exactly the four roles granted changed_files ===")
cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.plan = Plan(objective="o"); s.design_spec = DesignSpec()
allowed, refused = [], []
for role in AgentRole:
    try:
        cb.build(s, role, task="t", changed_files=[c]); allowed.append(role.value)
    except ContextPermissionError:
        refused.append(role.value)
print("   allowed:", allowed)
print("   expected builder/a11y/code_reviewer/fixer:",
      ok(set(allowed) == {"builder", "accessibility_reviewer", "code_reviewer", "fixer"}))
print("   refused for the other %d roles %s" % (len(refused), ok(len(refused) == 8)))

print()
print("=== 6. Code Reviewer slice: files only, no project state ===")
ctx = cb.build(s, AgentRole.CODE_REVIEWER, task="review", changed_files=[c])
print("   state slice:", ctx.state, ok(ctx.state == {}))
print("   constraints:", ctx.constraints, ok(ctx.constraints == []))
print("   files in inputs:", ok("changed_files" in ctx.inputs))
print("   payload type   :", type(ctx.inputs["changed_files"][0]).__name__)
print("   raw prompt excluded:", ok("SECRET-RAW" not in str(ctx.state) + str(ctx.inputs)))

print()
print("=== 7. Accessibility Reviewer still works (it reads .path only) ===")
from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
prev = RenderedPreviewPayload(url="u", dom_html="<main><h1>x</h1></main>")
actx = cb.build(s, AgentRole.ACCESSIBILITY_REVIEWER, task="t",
                rendered_preview=prev, changed_files=[c])
a = AccessibilityReviewerAgent(None)
body = a.prompt_template(actx).format_messages(**a.prompt_variables(actx))[1].content
text = " ".join(b.get("text", "") for b in body if b.get("type") == "text")
print("   path listed:", ok("src/components/hero.tsx" in text))
print("   source NOT dumped into the a11y prompt:", ok(CODE not in text))
