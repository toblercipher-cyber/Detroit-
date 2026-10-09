"""The two-phase approval gate: no mutation, single-use, short-TTL, 403/410/501."""
import os, sys, logging, io as _io
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from backend.services.external_actions import (
    ExternalActionService, PendingPlan, describe_action,
    CapabilityUnavailableError, ApprovalRequiredError, ApprovalExpiredError,
    DEFAULT_TTL_SECONDS,
)
from backend.schemas.api import (ExternalActionRequest, ExternalActionExecuteRequest,
                                 ExternalActionPlan, ErrorCode)

PID = "proj_" + "a" * 32
OTHER = "proj_" + "b" * 32
ALL_ACTIONS = ("github_branch", "github_commit", "github_push", "github_pr")


class Clock:
    """A movable clock, so expiry is tested without sleeping."""
    def __init__(self): self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def __call__(self): return self.now
    def advance(self, seconds): self.now += timedelta(seconds=seconds)


def svc(supported=ALL_ACTIONS, ttl=DEFAULT_TTL_SECONDS):
    clock = Clock()
    return ExternalActionService(lambda a: a in supported, ttl_seconds=ttl,
                                 clock=clock), clock


def req(action="github_pr", **params):
    return ExternalActionRequest(action_type=action, params=params)


print("=== 1. No discovery -> every action is 501 (docs/mcp.md 1, 4) ===")
none = ExternalActionService()  # capability_check=None: nothing discovered
for action in ALL_ACTIONS:
    try:
        none.plan(PID, req(action))
        print("   %-16s PLANNED **FAIL**" % action)
    except CapabilityUnavailableError as e:
        print("   %-16s -> %s %s" % (action, e.code.value,
                                     ok(e.code is ErrorCode.CAPABILITY_UNAVAILABLE)))
print("   -> 'no server connected' and 'server lacks it' give the same answer")

print()
print("=== 2. A discovered-but-absent capability is also 501, never substituted ===")
s, _ = svc(supported=("github_branch",))
print("   supported action plans   :", ok(isinstance(s.plan(PID, req("github_branch")),
                                                     ExternalActionPlan)))
try:
    s.plan(PID, req("github_pr"))
    print("   unsupported PLANNED **FAIL**")
except CapabilityUnavailableError as e:
    print("   unsupported -> 501       :", ok(True))
    print("   names it, offers no alternative:",
          ok("github_pr" in str(e) and "instead" not in str(e).lower()))

print()
print("=== 3. Planning mutates nothing and issues a usable token ===")
s, clock = svc()
plan = s.plan(PID, req("github_pr", repository="octo/demo", branch="feat"))
print("   returns ExternalActionPlan:", ok(isinstance(plan, ExternalActionPlan)))
print("   plan_id issued            :", ok(plan.plan_id.startswith("plan_")))
print("   token is long and random  :", ok(len(plan.approval_token) >= 32))
print("   expires in the future     :", ok(plan.expires_at > clock.now))
print("   TTL honoured              :",
      ok(plan.expires_at == clock.now + timedelta(seconds=DEFAULT_TTL_SECONDS)))
print("   summary names the action  :", ok("github_pr" in plan.summary))
print("   changes describe the target:",
      ok(any("octo/demo" in c for c in plan.changes)))
print("   states history is not rewritten:",
      ok(any("history is not rewritten" in c for c in plan.changes)))
print("   two plans get different tokens:",
      ok(s.plan(PID, req()).approval_token != plan.approval_token))

print()
print("=== 4. The raw token is never stored ===")
stored = s._plans[plan.plan_id]
print("   store holds a digest, not the token:",
      ok(plan.approval_token not in stored.token_digest))
print("   token absent from the whole record :",
      ok(plan.approval_token not in repr(stored)))
print("   digest is sha256-shaped            :", ok(len(stored.token_digest) == 64))

print()
print("=== 5. Execution requires a valid token -> 403 ===")
s, clock = svc()
p = s.plan(PID, req())
bad = [
    ("wrong token",     ExternalActionExecuteRequest(plan_id=p.plan_id, approval_token="nope")),
    ("empty token",     ExternalActionExecuteRequest(plan_id=p.plan_id, approval_token=" ")),
    ("unknown plan_id", ExternalActionExecuteRequest(plan_id="plan_deadbeef", approval_token=p.approval_token)),
]
for label, request in bad:
    try:
        s.consume(PID, request)
        print("   %-16s ACCEPTED **FAIL**" % label)
    except ApprovalRequiredError as e:
        print("   %-16s -> 403 %s %s" % (label, e.code.value,
                                         ok(e.code is ErrorCode.APPROVAL_REQUIRED)))

print()
print("=== 6. A token is scoped to its project ===")
try:
    s.consume(OTHER, ExternalActionExecuteRequest(plan_id=p.plan_id,
                                                  approval_token=p.approval_token))
    print("   cross-project ACCEPTED **FAIL**")
except ApprovalRequiredError:
    print("   another project's token rejected:", ok(True))
except ApprovalExpiredError:
    print("   rejected, but as 410 -- leaks that the plan exists **FAIL**")
print("   still usable by its OWN project:",
      ok(s.consume(PID, ExternalActionExecuteRequest(
          plan_id=p.plan_id, approval_token=p.approval_token)).plan_id == p.plan_id))

print()
print("=== 7. Single use -> the second attempt is 410 ===")
s, clock = svc()
p = s.plan(PID, req())
ex = ExternalActionExecuteRequest(plan_id=p.plan_id, approval_token=p.approval_token)
first = s.consume(PID, ex)
print("   first use succeeds :", ok(first.plan_id == p.plan_id))
print("   marked spent       :", ok(first.is_spent))
try:
    s.consume(PID, ex)
    print("   REPLAY ACCEPTED **FAIL**")
except ApprovalExpiredError as e:
    print("   replay -> 410      :", e.code.value, ok(e.code is ErrorCode.APPROVAL_EXPIRED))
    print("   message says used  :", ok("already been used" in str(e)))

print()
print("=== 8. Expiry -> 410, and it is the clock that decides ===")
s, clock = svc(ttl=60)
p = s.plan(PID, req())
ex = ExternalActionExecuteRequest(plan_id=p.plan_id, approval_token=p.approval_token)
clock.advance(59)
print("   valid just before TTL:", ok(s._plans[p.plan_id].is_expired(clock.now) is False))
clock.advance(2)
try:
    s.consume(PID, ex)
    print("   EXPIRED TOKEN ACCEPTED **FAIL**")
except ApprovalExpiredError as e:
    print("   after TTL -> 410     :", ok(e.code is ErrorCode.APPROVAL_EXPIRED))
    print("   message says expired :", ok("expired" in str(e)))

print()
print("=== 9. A bad token on a spent plan is 403, not 410 ===")
# Order matters: someone without the token must not learn the plan's state.
s, clock = svc()
p = s.plan(PID, req())
s.consume(PID, ExternalActionExecuteRequest(plan_id=p.plan_id,
                                            approval_token=p.approval_token))
try:
    s.consume(PID, ExternalActionExecuteRequest(plan_id=p.plan_id,
                                                approval_token="guess"))
    print("   ACCEPTED **FAIL**")
except ApprovalRequiredError:
    print("   wrong token on a spent plan -> 403:", ok(True), "(state not disclosed)")
except ApprovalExpiredError:
    print("   -> 410 leaks that the plan was used **FAIL**")

print()
print("=== 10. Expired plans are purged, spent ones are kept until they expire ===")
s, clock = svc(ttl=60)
a = s.plan(PID, req())
s.consume(PID, ExternalActionExecuteRequest(plan_id=a.plan_id, approval_token=a.approval_token))
print("   spent plan retained   :", ok(a.plan_id in s._plans), "(so a replay says 'used')")
clock.advance(61)
s.plan(PID, req())  # any new plan triggers the purge
print("   expired plan purged   :", ok(a.plan_id not in s._plans))
print("   store does not grow   :", ok(len(s._plans) == 1))

print()
print("=== 11. Nothing sensitive reaches the logs ===")
buf = _io.StringIO()
h = logging.StreamHandler(buf)
lg = logging.getLogger("backend.services.external_actions")
lg.addHandler(h); lg.setLevel(logging.INFO)
s, clock = svc()
p = s.plan(PID, req("github_push", repository="octo/demo"))
try:
    s.consume(PID, ExternalActionExecuteRequest(plan_id=p.plan_id, approval_token="wrong"))
except ApprovalRequiredError:
    pass
s.consume(PID, ExternalActionExecuteRequest(plan_id=p.plan_id,
                                            approval_token=p.approval_token))
lg.removeHandler(h)
out = buf.getvalue()
print("   token never logged :", ok(p.approval_token not in out))
print("   digest never logged:", ok(s._plans[p.plan_id].token_digest not in out))
print("   plan_id IS logged  :", ok(p.plan_id in out), "(needed to trace an action)")

print()
print("=== 12. describe_action restates the request, inventing nothing ===")
summary, changes = describe_action("github_branch", {"repository": "octo/demo",
                                                     "branch": "feat", "secret": "x" * 9})
print("   allowlisted params shown :", ok(any("octo/demo" in c for c in changes)))
print("   non-allowlisted withheld :", ok(not any("xxxxxxxxx" in c for c in changes)))
print("   but its presence counted :", ok(any("further parameter" in c for c in changes)))
print("   no GitHub tool names     :",
      ok(not any(t in summary + " ".join(changes)
                 for t in ("create_branch", "create_pull_request", "push_files"))))
empty_summary, empty_changes = describe_action("github_pr", {})
print("   empty params handled     :", ok("No parameters" in empty_changes[0]))

print()
print("=== 13. The gate executes nothing ===")
src = open("backend/services/external_actions.py", encoding="utf-8").read()
print("   no call_tool        :", ok("call_tool" not in src))
print("   no MCP client import:", ok("mcp_client" not in src and "from mcp" not in src))
print("   no github tool names:",
      ok(not any(t in src for t in ("create_branch", "create_pull_request",
                                    "push_files", "merge_pull_request"))))
print("   public methods      :",
      [m for m in dir(ExternalActionService) if not m.startswith("_")],
      ok(set(m for m in dir(ExternalActionService) if not m.startswith("_"))
         == {"plan", "consume"}))
