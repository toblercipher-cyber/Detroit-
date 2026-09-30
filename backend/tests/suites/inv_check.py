"""COMPONENT_INVENTORY grant verification. No network, no API key."""
import os, sys
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from backend.context.builder import ContextBuilder, ContextInput, ContextPermissionError, ReferenceImagePayload
from backend.schemas.enums import AgentRole
from backend.schemas.state import ProjectState, Plan, DesignSpec

ok = lambda b: "PASS" if b else "**FAIL**"
cb = ContextBuilder()
s = ProjectState(user_request="SECRET-RAW")
s.plan = Plan(objective="sell coffee")
s.design_spec = DesignSpec(font_families=["Inter"])
inv = ["ui/button", "ui/card", "ui/input", "site-nav", "hero-section"]

print("=== 1. Enum member added ===")
print("   COMPONENT_INVENTORY:", ContextInput.COMPONENT_INVENTORY.value,
      ok(ContextInput.COMPONENT_INVENTORY.value == "component_inventory"))
print("   total inputs:", len(list(ContextInput)))

print()
print("=== 2. Frontend Architect receives it ===")
ctx = cb.build(s, AgentRole.FRONTEND_ARCHITECT, task="design the component tree",
               component_inventory=inv)
print("   runtime allowlist :", sorted(i.value for i in cb.runtime_allowlist(AgentRole.FRONTEND_ARCHITECT)))
print("   inventory in inputs:", ok("component_inventory" in ctx.inputs))
print("   value preserved    :", ok(ctx.inputs["component_inventory"] == inv))
print("   state grants intact:", sorted(ctx.state), ok(set(ctx.state) == {"plan", "design_spec"}))
print("   raw prompt excluded:", ok("SECRET-RAW" not in str(ctx.state) + str(ctx.inputs)))

print()
print("=== 3. Every other role is refused it ===")
refused = []
for role in AgentRole:
    if role is AgentRole.FRONTEND_ARCHITECT:
        continue
    try:
        cb.build(s, role, task="x", component_inventory=inv)
        print("   %-24s ACCEPTED **FAIL**" % role.value)
    except ContextPermissionError:
        refused.append(role)
print("   refused for %d of %d other roles %s" % (len(refused), len(list(AgentRole)) - 1,
                                                  ok(len(refused) == len(list(AgentRole)) - 1)))

print()
print("=== 4. Architect still refused inputs it should not get ===")
for kw, label in [(dict(reference_image=ReferenceImagePayload(image_id="i", content_type="image/png", data_base64="x")), "reference_image"),
                  (dict(findings=[]), "findings"),
                  (dict(rendered_preview="http://x"), "rendered_preview")]:
    try:
        cb.build(s, AgentRole.FRONTEND_ARCHITECT, task="x", **kw)
        print("   %-18s ACCEPTED **FAIL**" % label)
    except ContextPermissionError:
        print("   %-18s refused %s" % (label, ok(True)))

print()
print("=== 5. Omitting the inventory is still valid (no forced input) ===")
ctx2 = cb.build(s, AgentRole.FRONTEND_ARCHITECT, task="x")
print("   builds without it :", ok("component_inventory" not in ctx2.inputs))
