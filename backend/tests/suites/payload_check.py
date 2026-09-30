"""ReferenceImagePayload + builder wiring. No network, no API key."""
import os, sys, base64
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import ValidationError
from backend.context.builder import (ContextBuilder, ContextPermissionError,
                                     ReferenceImagePayload, ContextInput)
from backend.schemas.enums import AgentRole
from backend.schemas.state import ProjectState, DesignSpec, ReferenceImageRef

ok = lambda b: "PASS" if b else "**FAIL**"
data = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 4000).decode()

print("=== 1. Payload model ===")
p = ReferenceImagePayload(image_id="img_1", content_type="image/png", data_base64=data)
print("   constructs            :", ok(True), "| data len:", len(p.data_base64))
for bad, label in [(dict(image_id="", content_type="image/png", data_base64=data), "empty image_id"),
                   (dict(image_id="i", content_type="", data_base64=data), "empty content_type"),
                   (dict(image_id="i", content_type="image/png", data_base64=""), "empty data"),
                   (dict(image_id="i", content_type="image/png", data_base64=data, extra="x"), "extra field")]:
    try:
        ReferenceImagePayload(**bad); print("   %-22s NOT REJECTED **FAIL**" % label)
    except ValidationError: print("   %-22s rejected %s" % (label, ok(True)))

print()
print("=== 2. repr never dumps the blob (logs/tracebacks) ===")
r = repr(p)
print("   repr:", r)
print("   blob absent from repr :", ok(data not in r))
print("   blob absent from str  :", ok(data not in str(p)))

print()
print("=== 3. Payload is NOT persisted state ===")
print("   not a ProjectState field type:",
      ok("ReferenceImagePayload" not in str(ProjectState.model_fields)))
print("   ProjectState still stores ReferenceImageRef (metadata only):",
      ok("data" not in ReferenceImageRef.model_fields and "size_bytes" in ReferenceImageRef.model_fields))

print()
print("=== 4. ContextBuilder accepts it for the Reference Analyzer only ===")
cb = ContextBuilder()
s = ProjectState(user_request="x"); s.design_spec = DesignSpec(font_families=["Inter"])
ctx = cb.build(s, AgentRole.REFERENCE_ANALYZER, task="analyse", reference_image=p)
print("   granted state keys    :", sorted(ctx.state))
print("   image in inputs       :", ok(ContextInput.REFERENCE_IMAGE.value in ctx.inputs))
print("   input is the payload  :", ok(isinstance(ctx.inputs["reference_image"], ReferenceImagePayload)))
print("   design_spec present for conflict detection:", ok("design_spec" in ctx.state))
for role in (AgentRole.PLANNER, AgentRole.DESIGN_DIRECTOR, AgentRole.BUILDER):
    try:
        cb.build(s, role, task="x", reference_image=p); print("   %-18s ACCEPTED **FAIL**" % role.value)
    except ContextPermissionError: print("   %-18s refused %s" % (role.value, ok(True)))
