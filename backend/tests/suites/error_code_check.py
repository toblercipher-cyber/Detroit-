"""_code_for: carried codes win, fallbacks intact, no injection. No network."""
import os, sys
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from fastapi import HTTPException
from backend.main import _code_for, _STATUS_TO_CODE
from backend.schemas.api import ErrorCode
from backend.services.projects import (NoPendingCheckpointError,
                                       CheckpointAlreadyResolvedError,
                                       ProjectBusyError, ProjectServiceError)
from backend.memory.artifacts import ProjectNotFoundError, InvalidProjectIdError


def raised(inner, status_code):
    """Reproduce exactly what a route does: HTTPException(...) from None."""
    try:
        try:
            raise inner
        except type(inner):
            raise HTTPException(status_code=status_code, detail="x") from None
    except HTTPException as e:
        return e


print("=== 1. Service errors contribute their own code ===")
for err, sc, want in [
        (NoPendingCheckpointError("a"), 409, "no_pending_checkpoint"),
        (CheckpointAlreadyResolvedError("b"), 410, "checkpoint_already_resolved"),
        (ProjectBusyError("c"), 409, "invalid_stage_for_action")]:
    got = _code_for(raised(err, sc), sc).value
    print("   %-32s %s -> %-28s %s" % (type(err).__name__, sc, got, ok(got == want)))

print()
print("=== 2. The two 409s are now distinguishable ===")
a = _code_for(raised(NoPendingCheckpointError("a"), 409), 409)
b = _code_for(raised(ProjectBusyError("c"), 409), 409)
print("   same status, different codes:", a.value, "vs", b.value, ok(a is not b))

print()
print("=== 3. Fallback to the status map is unchanged ===")
for sc, want in [(404, "project_not_found"), (403, "approval_required"),
                 (410, "checkpoint_already_resolved"), (422, "validation_error"),
                 (503, "quality_gate_blocked"), (502, "upstream_error")]:
    got = _code_for(HTTPException(status_code=sc, detail="x"), sc).value
    print("   bare %-4s -> %-28s %s" % (sc, got, ok(got == want)))
print("   unmapped 418 -> internal_error:",
      ok(_code_for(HTTPException(status_code=418), 418) is ErrorCode.INTERNAL_ERROR))

print()
print("=== 4. Errors without a code fall back correctly ===")
for err, sc, want in [(ProjectNotFoundError("nope"), 404, "project_not_found"),
                      (InvalidProjectIdError("bad"), 404, "project_not_found"),
                      (ProjectServiceError("resume failed"), 500, "internal_error")]:
    got = _code_for(raised(err, sc), sc).value
    print("   %-24s %s -> %-20s %s" % (type(err).__name__, sc, got, ok(got == want)))

print()
print("=== 5. A foreign 'code' attribute cannot be injected ===")


class Sneaky(Exception):
    code = "quality_gate_blocked"          # a str, not an ErrorCode


class SneakyEnumish(Exception):
    code = 999


for err in (Sneaky("x"), SneakyEnumish("y")):
    got = _code_for(raised(err, 409), 409)
    print("   %-16s ignored, fell back to %-24s %s" % (
        type(err).__name__, got.value, ok(got is ErrorCode.INVALID_STAGE_FOR_ACTION)))

print()
print("=== 6. Chain walking terminates ===")
e1 = NoPendingCheckpointError("a")
loop = HTTPException(status_code=409, detail="x")
loop.__context__ = loop                      # self-referential
print("   self-referential context does not hang:",
      ok(_code_for(loop, 409) is ErrorCode.INVALID_STAGE_FOR_ACTION))
deep = e1
for _ in range(50):
    nxt = HTTPException(status_code=409, detail="x")
    nxt.__context__ = deep
    deep = nxt
print("   50-deep chain still finds the code:",
      ok(_code_for(deep, 409) is ErrorCode.NO_PENDING_CHECKPOINT))
print("   __cause__ preferred over __context__:",
      ok(isinstance(_code_for(raised(CheckpointAlreadyResolvedError('z'), 410), 410),
                    ErrorCode)))
