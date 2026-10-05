"""MCP client: classification, validation, role filtering, secret handling.

Discovery itself is exercised against a FAKE in-process server, because
docs/mcp.md 1 says no MCP server is connected in v1 and the core workflow must
run with none. Nothing here reaches the network.
"""
import os, sys, asyncio, logging, io as _io
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from pydantic import SecretStr
from backend.tools.mcp_client import (
    McpClient, classify_tool, validate_tool, tools_for_role,
    DiscoveryResult, DiscoveredTool, McpCapabilityError, McpUnavailableError,
    CapabilityClass,
)
from backend.schemas.enums import AgentRole

SECRET = "ghp_THIS_MUST_NEVER_APPEAR"


class Ann:
    def __init__(self, read_only=None, destructive=None):
        self.read_only_hint = read_only
        self.destructive_hint = destructive


class FakeTool:
    def __init__(self, name, annotations=None, schema=None, description="d"):
        self.name = name
        self.annotations = annotations
        self.input_schema = {"type": "object", "properties": {}} if schema is None else schema
        self.description = description


print("=== 1. Classification comes from annotations, never from the name ===")
cases = [
    ("read_only_hint=True",      Ann(read_only=True),                  "read"),
    ("destructive_hint=True",    Ann(destructive=True),                "destructive"),
    ("both set",                 Ann(read_only=True, destructive=True), "destructive"),
    ("read_only=False only",     Ann(read_only=False),                 "write"),
    ("no hints set",             Ann(),                                "write"),
    ("NO annotations at all",    None,                                 "write"),
]
for label, ann, want in cases:
    got = classify_tool(ann)
    print("   %-24s -> %-12s %s" % (label, got, ok(got == want)))
print("   unannotated defaults to WRITE, not read:",
      ok(classify_tool(None) == "write"), "(an unknown tool must not skip approval)")

# The classifier must be blind to names: a tool CALLED delete_everything with a
# read-only hint is read, and one called get_x with no hints is still a write.
print("   name cannot downgrade a class:",
      ok(validate_tool(FakeTool("get_readme")).capability == "write"))
print("   name cannot upgrade a class  :",
      ok(validate_tool(FakeTool("delete_repo", Ann(read_only=True))).capability == "read"))

print()
print("=== 2. requires_approval follows the class ===")
for cls, want in (("read", False), ("write", True), ("destructive", True)):
    t = DiscoveredTool(name="t", capability=cls)
    print("   %-12s requires approval: %-5s %s" % (cls, t.requires_approval, ok(t.requires_approval == want)))

print()
print("=== 3. Schema validation rejects what cannot be checked ===")
good = validate_tool(FakeTool("ok"))
print("   valid object schema accepted:", ok(good.name == "ok"))
print("   schema preserved for later arg validation:",
      ok(good.input_schema.get("type") == "object"))
for label, tool in [
    ("no name",        FakeTool("")),
    ("schema is None", FakeTool("x", schema=None if False else 123)),
    ("schema is list", FakeTool("x", schema=[])),
    ("non-object type", FakeTool("x", schema={"type": "string"})),
]:
    try:
        validate_tool(tool)
        print("   %-16s ACCEPTED **FAIL**" % label)
    except McpCapabilityError:
        print("   %-16s rejected %s" % (label, ok(True)))
# A schema with no explicit "type" is allowed: JSON Schema permits it.
print("   untyped schema allowed:",
      ok(validate_tool(FakeTool("x", schema={"properties": {}})).name == "x"))

print()
print("=== 4. Role filtering enforces least privilege (docs/mcp.md 3) ===")
disc = DiscoveryResult(
    server_url="https://example/mcp/", server_name="fake", protocol_version="1",
    tools={
        "r": DiscoveredTool("r", "read"),
        "w": DiscoveredTool("w", "write"),
        "d": DiscoveredTool("d", "destructive"),
    })
expected = {
    AgentRole.REQUIREMENT_ANALYZER: set(), AgentRole.PLANNER: set(),
    AgentRole.DESIGN_DIRECTOR: set(), AgentRole.FRONTEND_ARCHITECT: set(),
    AgentRole.REFERENCE_ANALYZER: set(), AgentRole.BUILDER: set(),
    AgentRole.FIXER: set(),
    AgentRole.CODE_REVIEWER: {"r"},
    AgentRole.VISUAL_UX_CRITIC: {"r"}, AgentRole.ACCESSIBILITY_REVIEWER: {"r"},
    AgentRole.ANTI_SLOP_REVIEWER: {"r"},
    AgentRole.GITHUB_OPERATOR: {"r", "w", "d"},
}
for role, want in expected.items():
    got = set(tools_for_role(disc, role))
    print("   %-24s %-12s %s" % (role.value, sorted(got) or "none", ok(got == want)))
print("   NO role except github_operator sees a write tool:",
      ok(all("w" not in tools_for_role(disc, r) and "d" not in tools_for_role(disc, r)
             for r in AgentRole if r is not AgentRole.GITHUB_OPERATOR)))

print()
print("=== 5. require() refuses an absent capability (-> 501, never substitute) ===")
print("   present tool returned:", ok(disc.require("r").name == "r"))
try:
    disc.require("create_branch")
    print("   absent tool returned something **FAIL**")
except McpCapabilityError as e:
    print("   absent tool raises McpCapabilityError:", ok(True))
    print("   message names it, suggests no alternative:",
          ok("create_branch" in str(e) and "instead" not in str(e).lower()))

print()
print("=== 6. The credential never escapes ===")
c = McpClient("https://api.githubcopilot.com/mcp/", SecretStr(SECRET))
print("   not in repr()     :", ok(SECRET not in repr(c)))
print("   not in str()      :", ok(SECRET not in str(c)))
print("   not in __dict__ str:", ok(SECRET not in str(vars(c))))
print("   header IS built   :", ok(c._headers()["Authorization"] == "Bearer " + SECRET))
print("   no token on the discovery result type:",
      ok("token" not in DiscoveryResult.__dataclass_fields__))
# Capture logs during a failed connection and prove nothing leaks.
buf = _io.StringIO()
h = logging.StreamHandler(buf); logging.getLogger("backend.tools.mcp_client").addHandler(h)
bad = McpClient("https://127.0.0.1:1/mcp/", SecretStr(SECRET), timeout_seconds=5)
try:
    asyncio.run(bad.discover())
    print("   unreachable server returned normally **FAIL**")
except McpUnavailableError as e:
    print("   unreachable -> McpUnavailableError:", ok(True))
    print("   error text has no token :", ok(SECRET not in str(e)))
    print("   error text has no URL   :", ok("127.0.0.1" not in str(e)))
logging.getLogger("backend.tools.mcp_client").removeHandler(h)
print("   LOGS contain no token   :", ok(SECRET not in buf.getvalue()))
print("   LOGS contain no url     :", ok("127.0.0.1" not in buf.getvalue()))

print()
print("=== 7. No GitHub tool name is hard-coded anywhere in the module ===")
src = open("backend/tools/mcp_client.py", encoding="utf-8").read()
for banned in ("create_branch", "create_pull_request", "push_files",
               "create_or_update_file", "merge_pull_request", "get_file_contents"):
    print("   %-24s absent: %s" % (banned, ok(banned not in src)))
# The module may name the GitHub Operator ROLE -- docs/mcp.md 3 requires that
# row -- but must not name a GitHub tool or a GitHub URL.
github_lines = [l.strip() for l in src.splitlines() if "github" in l.lower()]
role_only = all(("GITHUB_OPERATOR" in l or "GitHub Operator" in l) for l in github_lines)
print("   github appears only as the ROLE:", github_lines, ok(role_only))
print("   no github URL hard-coded      :",
      ok("githubcopilot" not in src and "api.github" not in src))

print()
print("=== 8. Discovery is read-only: no call_tool is exposed ===")
print("   module has no call_tool:", ok("call_tool" not in src))
print("   McpClient has no execute method:",
      ok(not any(m in dir(McpClient) for m in ("call_tool", "execute", "invoke"))))
print("   -> executing belongs behind the approval gate (docs/mcp.md 5)")

print()
print("=== 9. Core workflow does not depend on MCP (docs/mcp.md 1) ===")
import subprocess
r = subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0,'.');"
                    "import backend.graph.workflow, backend.main;"
                    "print('mcp' in sys.modules)"],
                   capture_output=True, text=True,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
print("   importing the graph pulls in mcp:", r.stdout.strip() or r.stderr[-80:],
      ok(r.stdout.strip() == "False"))

print()
print("=== 10. REGRESSION: timeout_seconds is actually enforced ===")
# The bug this pins: timeout_seconds was stored and never used, so a server
# that ACCEPTS the socket and never answers hung discovery forever. The hang is
# in initialize(), not in a request, so a transport timeout alone misses it.
import socket, threading, time
from backend.tools.mcp_client import DEFAULT_TIMEOUT_SECONDS

_srv = socket.socket()
_srv.bind(("127.0.0.1", 0))
_srv.listen(5)
_PORT = _srv.getsockname()[1]
_held = []


def _accept_and_stay_silent():
    """Accept connections and never reply -- the classic hang."""
    while True:
        try:
            _held.append(_srv.accept()[0])
        except OSError:
            return


threading.Thread(target=_accept_and_stay_silent, daemon=True).start()


async def _timed_discover(seconds):
    client = McpClient(
        "http://127.0.0.1:%d/mcp/" % _PORT, SecretStr(SECRET),
        timeout_seconds=seconds)
    started = time.time()
    try:
        # The outer guard is generous: if the budget is not enforced this
        # trips, which is the failure being tested for.
        await asyncio.wait_for(client.discover(), timeout=seconds + 15)
        return time.time() - started, "returned normally"
    except McpUnavailableError as exc:
        return time.time() - started, exc
    except asyncio.TimeoutError:
        return time.time() - started, "HUNG PAST THE BUDGET"


try:
    elapsed, outcome = asyncio.run(_timed_discover(2.0))
    print("   timeout=2s -> returned in %.1fs" % elapsed)
    print("   did NOT hang            :", ok(elapsed < 4.0))
    print("   raised McpUnavailableError:", ok(isinstance(outcome, McpUnavailableError)))
    # Exact wording, not just the type. The inner transport timeouts carry 1.5x
    # headroom so the outer wait_for always wins; without it this message was a
    # coin flip with "could not be reached", which hides a slow server behind
    # an absent one.
    print("   exact timeout message   :", repr(str(outcome)),
          ok(str(outcome) == "The MCP server did not respond in time."))
    print("   message leaks no token   :", ok(SECRET not in str(outcome)))
    print("   message leaks no host    :", ok("127.0.0.1" not in str(outcome)))

    # A different budget must actually change the wait, not just return fast.
    elapsed5, outcome5 = asyncio.run(_timed_discover(5.0))
    print("   timeout=5s -> returned in %.1fs" % elapsed5)
    print("   budget is respected, not hard-coded:",
          ok(4.0 < elapsed5 < 8.0 and elapsed < elapsed5))
    print("   default still 30s        :", ok(DEFAULT_TIMEOUT_SECONDS == 30.0))
finally:
    _srv.close()
    for _conn in _held:
        try:
            _conn.close()
        except OSError:
            pass
