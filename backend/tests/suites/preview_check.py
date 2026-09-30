"""RenderedPreviewPayload verification. No network, no API key."""
import os, sys, base64
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import ValidationError
from backend.context.builder import (ContextBuilder, ContextInput, ContextPermissionError,
                                     RenderedPreviewPayload)
from backend.schemas.enums import AgentRole
from backend.schemas.state import ProjectState, DesignSpec, Plan

ok = lambda b: "PASS" if b else "**FAIL**"
SHOT = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p" * 3000).decode()
DOM = "<main><h1>Coffee</h1><section aria-label='menu'>...</section></main>"

print("=== 1. A URL alone is rejected — that was the whole problem ===")
try:
    RenderedPreviewPayload(url="http://localhost/preview/")
    print("   url-only ACCEPTED **FAIL**")
except ValidationError as e:
    print("   url-only rejected:", ok(True))
    print("   message:", str(e).splitlines()[1].strip()[:88])

print()
print("=== 2. Either kind of evidence is enough; both is fine ===")
for kw, label in [(dict(screenshot_base64=SHOT, screenshot_media_type="image/png"), "screenshot only"),
                  (dict(dom_html=DOM), "DOM only"),
                  (dict(screenshot_base64=SHOT, screenshot_media_type="image/png", dom_html=DOM), "both")]:
    try:
        RenderedPreviewPayload(url="http://x/", **kw); print("   %-16s accepted %s" % (label, ok(True)))
    except ValidationError as e:
        print("   %-16s REJECTED **FAIL** %s" % (label, e))

print()
print("=== 3. A screenshot without its media type is rejected ===")
try:
    RenderedPreviewPayload(url="http://x/", screenshot_base64=SHOT)
    print("   ACCEPTED **FAIL**")
except ValidationError:
    print("   rejected:", ok(True))

print()
print("=== 4. repr summarises, never dumps ===")
p = RenderedPreviewPayload(url="http://x/", screenshot_base64=SHOT,
                           screenshot_media_type="image/png", dom_html=DOM)
r = repr(p)
print("   repr:", r)
print("   screenshot blob absent:", ok(SHOT not in r))
print("   dom absent            :", ok(DOM not in r))

print()
print("=== 5. Reaches exactly the three reviewers granted it ===")
cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.design_spec = DesignSpec(font_families=["Inter"]); s.plan = Plan(objective="sell coffee",
                                                                  acceptance_criteria=["nav reachable"])
allowed, refused = [], []
for role in AgentRole:
    try:
        cb.build(s, role, task="x", rendered_preview=p); allowed.append(role.value)
    except ContextPermissionError:
        refused.append(role.value)
print("   allowed :", allowed)
print("   expected: visual_ux_critic, anti_slop_reviewer, accessibility_reviewer",
      ok(set(allowed) == {"visual_ux_critic", "anti_slop_reviewer", "accessibility_reviewer"}))
print("   refused for the other %d roles %s" % (len(refused), ok(len(refused) == 9)))

print()
print("=== 6. Live slice for the Visual & UX Critic ===")
ctx = cb.build(s, AgentRole.VISUAL_UX_CRITIC, task="critique", rendered_preview=p)
print("   state keys :", sorted(ctx.state))
print("   acceptance criteria derived:", ctx.state.get("acceptance_criteria"))
print("   preview in inputs:", ok("rendered_preview" in ctx.inputs))
print("   payload type     :", type(ctx.inputs["rendered_preview"]).__name__)
print("   raw prompt excluded:", ok("SECRET-RAW" not in str(ctx.state)))
