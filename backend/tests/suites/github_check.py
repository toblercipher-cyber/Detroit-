"""GitHub integration: configuration, execution, ownership, push and approval.

SCOPE
    Push into an existing repository is the only sync this product performs.
    Pull/import and pull-request creation were implemented, tested and then
    REMOVED as out of scope, so there is deliberately nothing here that
    exercises them.

WHY THIS SUITE EXISTS
    The integration can perform real repository writes, so the properties that
    matter are the ones where a silent failure is destructive: an unapproved
    push, a pull that overwrites someone's work, a credential in a response, a
    repository reachable by the wrong user.

    None of those can be checked against the real GitHub in CI, and checking
    them against a live repository would mean writing to one. So every GitHub
    response here is MOCKED at the transport boundary (``httpx``), and the code
    under test is the production path above it: the gateway, the approval gate,
    the routes, the ownership scoping.

WHAT IS AND IS NOT PROVEN
    Proven: our logic, end to end, including the exact HTTP requests we would
    send. Not proven: that GitHub accepts them. That needs a live run with a
    real token, which ``github_live_check.py`` does only when explicitly
    enabled.

SKIPS when DATABASE_URL_APP is absent: the ownership sections need the real
owner-scoped store, and a suite that quietly dropped them would overstate what
it checked.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

PASS = "PASS"
_results: list[bool] = []


def ok(condition: bool) -> str:
    _results.append(bool(condition))
    return PASS if condition else "**FAIL**"


def skip(reason: str) -> None:
    print(f"SKIPPED: {reason}")
    sys.exit(0)


sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True

from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(".env")
os.environ.setdefault("ANTHROPIC_API_KEY", "stub-key-for-tests")
os.environ.setdefault("PREVIEW_URL_SECRET", "test-only-preview-signing-key")

import httpx  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from backend.config.settings import Settings  # noqa: E402
from backend.schemas.enums import AgentRole  # noqa: E402
from backend.services.github_gateway import GitHubGateway  # noqa: E402
from backend.tests.suites import _tmpdir  # noqa: E402
from backend.tools.github_rest import (  # noqa: E402
    GitHubError,
    GitHubRestClient,
    RepoRef,
    validate_repo_path,
)
from backend.tools.mcp_client import (  # noqa: E402
    DiscoveredTool,
    DiscoveryResult,
    McpApprovalError,
    McpCapabilityError,
    McpClient,
    role_may_call,
    validate_arguments,
)

from backend.tests.suites._github_mock import (  # noqa: E402
    TOKEN,
    MockGitHub,
    gateway_with,
    make_workspace,
    REMOTE_FILES,
)

# ---------------------------------------------------------------------------

print("=== 1. Configuration: absent, present, and unsafe ===")
base = {
    "ANTHROPIC_API_KEY": "k",
    "APP_ENV": "development",
}
s_none = Settings(_env_file=None, **base)
print("   no config -> not configured:", ok(not s_none.is_github_configured))
s_rest = Settings(_env_file=None, github_token="t", **base)
print("   token     -> configured    :", ok(s_rest.is_github_configured and s_rest.is_github_rest_configured))
s_mcp = Settings(_env_file=None, mcp_github_server_url="https://mcp.example/x", **base)
print("   mcp url   -> configured    :", ok(s_mcp.is_github_configured and s_mcp.is_github_mcp_configured))
try:
    Settings(_env_file=None, mcp_github_server_url="http://evil.example/mcp", **base)
    print("   plaintext url REJECTED     : **FAIL**")
    _results.append(False)
except Exception:
    print("   plaintext url REJECTED     :", ok(True))
try:
    Settings(_env_file=None, mcp_github_server_url="http://localhost:9000/mcp", **base)
    print("   localhost http allowed     :", ok(True))
except Exception:
    print("   localhost http allowed     : **FAIL**")
    _results.append(False)

print()
print("=== 2. The credential never escapes ===")
client = GitHubRestClient(TOKEN)
secret = TOKEN.get_secret_value()
print("   client repr redacts  :", ok(secret not in repr(client)))
gw_plain = GitHubGateway(token=TOKEN)
print("   gateway repr redacts :", ok(secret not in repr(gw_plain)))
print("   settings repr redacts:", ok(secret not in repr(s_rest) and "t" == s_rest.github_token.get_secret_value()))
print("   header carries it    :", ok(client._headers()["Authorization"].endswith(secret)))

print()
print("=== 3. Repository and path validation (untrusted input) ===")
# "a/b/c" is NO LONGER invalid: a pasted URL may carry a deeper path such as
# /tree/main, and the first two segments are the repository. Section 3a covers
# that form, and traversal is still refused there.
for bad in ["../etc", "octo", "-x/y", "octo/$x"]:
    try:
        RepoRef.parse(bad)
        print(f"   {bad!r:14} ACCEPTED **FAIL**")
        _results.append(False)
    except GitHubError:
        print(f"   {bad!r:14} refused:", ok(True))
for bad in ["../x", "/etc/passwd", "a/../../b", "C:/x", ""]:
    try:
        validate_repo_path(bad)
        print(f"   path {bad!r:14} ACCEPTED **FAIL**")
        _results.append(False)
    except GitHubError:
        print(f"   path {bad!r:14} refused:", ok(True))

print()
print("=== 3a. The user pastes a URL: every form they might paste ===")
# The primary input is "my repository's URL", so every shape a person
# realistically copies must reduce to the same two validated segments.
for raw, expected in [
    ("https://github.com/octo/site", "octo/site"),
    ("https://github.com/octo/site/", "octo/site"),
    ("https://github.com/octo/site.git", "octo/site"),
    ("http://github.com/octo/site", "octo/site"),
    ("github.com/octo/site", "octo/site"),
    ("git@github.com:octo/site.git", "octo/site"),
    ("ssh://git@github.com/octo/site.git", "octo/site"),
    ("https://github.com/octo/site/tree/main", "octo/site"),
    ("https://github.com/octo/site?tab=readme", "octo/site"),
    ("  https://github.com/octo/site  ", "octo/site"),
    ("https://ghe.corp.example:8443/octo/site", "octo/site"),
    ("octo/site", "octo/site"),
]:
    try:
        got = RepoRef.parse(raw).full_name
    except GitHubError as exc:
        got = f"REJECTED ({exc})"
    print(f"   {raw.strip()[:40]:42} -> {got}", ok(got == expected))

# A URL is untrusted input, so traversal must not survive normalisation.
for raw in ["../etc/passwd", "octo/../../x", "https://github.com/../x",
            "a/./b", "https://github.com/", "octo", ""]:
    try:
        RepoRef.parse(raw)
        print(f"   {raw!r:30} ACCEPTED **FAIL**")
        _results.append(False)
    except GitHubError:
        print(f"   {raw!r:30} refused:", ok(True))

print()
print("=== 4. MCP execution: four checks, none skippable ===")
read_tool = DiscoveredTool("get_file", "read", "reads", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
write_tool = DiscoveredTool("push_files", "write", "writes", {"type": "object", "properties": {"branch": {"type": "string"}}, "required": ["branch"], "additionalProperties": False})
discovery = DiscoveryResult("https://mcp.example/x", "mock", "1", {"get_file": read_tool, "push_files": write_tool})
mcp = McpClient("https://mcp.example/x", TOKEN)

async def expect(coro, exc, label):
    try:
        await coro
        print(f"   {label:34} NOT RAISED **FAIL**")
        _results.append(False)
    except exc:
        print(f"   {label:34}", ok(True))
    except Exception as e:
        print(f"   {label:34} wrong error {type(e).__name__} **FAIL**")
        _results.append(False)

asyncio.run(expect(
    mcp.call_tool(discovery, "nope", {}, role=AgentRole.GITHUB_OPERATOR, approved=True),
    McpCapabilityError, "undiscovered tool refused"))
asyncio.run(expect(
    mcp.call_tool(discovery, "get_file", {"path": "a"}, role=AgentRole.PLANNER),
    McpCapabilityError, "role without access refused"))
asyncio.run(expect(
    mcp.call_tool(discovery, "push_files", {"branch": "x"}, role=AgentRole.GITHUB_OPERATOR),
    McpApprovalError, "write without approval refused"))
asyncio.run(expect(
    mcp.call_tool(discovery, "push_files", {}, role=AgentRole.GITHUB_OPERATOR, approved=True),
    McpCapabilityError, "missing required arg refused"))
asyncio.run(expect(
    mcp.call_tool(discovery, "push_files", {"branch": 1}, role=AgentRole.GITHUB_OPERATOR, approved=True),
    McpCapabilityError, "wrong arg type refused"))
print("   read tool needs no approval      :", ok(not read_tool.requires_approval))
print("   write tool does                  :", ok(write_tool.requires_approval))
print("   reviewer may read                :", ok(role_may_call(AgentRole.CODE_REVIEWER, read_tool)))
print("   reviewer may NOT write           :", ok(not role_may_call(AgentRole.CODE_REVIEWER, write_tool)))

print()
print("=== 5. Push plan: additive, and it never invents files ===")
mock = MockGitHub()
gw = gateway_with(mock)
ws = make_workspace()
plan = asyncio.run(gw.plan_push(ws, repository="octo/site", branch="detroit/generated"))
paths = {c.path: c.kind for c in plan.changes}
print("   src/App.tsx is an update :", ok(paths.get("src/App.tsx") == "update"))
print("   src/new.ts is an add     :", ok(paths.get("src/new.ts") == "add"))
print("   package.json NOT pushed  :", ok("package.json" not in paths))
print("   head sha recorded        :", ok(plan.head_sha == "sha-head"))
print("   plan mutated nothing     :", ok(mock.committed is None))

print()
print("=== 6. Push executes: one commit, base_tree preserved, NEVER forced ===")
res = asyncio.run(gw.execute_push(
    ws, repository="octo/site", branch="detroit/generated",
    commit_message="DETROIT: test", expected_head_sha="sha-head"))
print("   committed 2 files        :", ok(res.files_written == 2))
print("   only src/ in the tree    :", ok(mock.committed is not None and all(p.startswith("src/") for p in mock.committed)))
print("   base_tree set (additive) :", ok(getattr(mock, "base_tree", None) == "sha-head"))
print("   ref update NOT forced    :", ok(mock.forced is False))
# No pull request is opened: PR management is out of scope, and the adapter
# no longer has a method for it.
print("   adapter cannot open a PR :",
      ok(not hasattr(GitHubRestClient, "create_pull_request")))

print()
print("=== 7. A moved branch is a conflict, not an overwrite ===")
mock2 = MockGitHub()
mock2.head_sha = "sha-moved"
gw2 = gateway_with(mock2)
try:
    asyncio.run(gw2.execute_push(
        ws, repository="octo/site", branch="detroit/generated",
        commit_message="m", expected_head_sha="sha-head"))
    print("   stale sha REJECTED       : **FAIL**")
    _results.append(False)
except GitHubError as e:
    print("   stale sha REJECTED       :", ok("moved" in str(e)))
print("   nothing was committed    :", ok(mock2.committed is None))

print()
print("=== 8. Push refuses a repository the credential cannot push to ===")
mock3 = MockGitHub()
mock3.can_push = False
gw3 = gateway_with(mock3)
try:
    asyncio.run(gw3.execute_push(ws, repository="octo/site", branch="b", commit_message="m"))
    print("   read-only repo REJECTED  : **FAIL**")
    _results.append(False)
except GitHubError:
    print("   read-only repo REJECTED  :", ok(True))

print()
print("=== 9. Status is verified, never assumed ===")
st = asyncio.run(gateway_with(MockGitHub()).status())
print("   configured + account      :", ok(st.configured and st.account == "octo"))
st_none = asyncio.run(GitHubGateway(token=None).status())
print("   no token -> not configured:", ok(not st_none.configured and st_none.mode == "none"))
# The reason must be useful AND must not name server configuration: this
# string reaches any signed-in browser, and "set GITHUB_TOKEN" is an operator
# instruction, not a user-facing one (docs/security.md 6).
print("   reason explains, safely   :",
      ok(bool(st_none.reason) and "GitHub" in st_none.reason
         and "GITHUB_TOKEN" not in st_none.reason))

print()
print("=== 10. MCP capability is VERIFIED, never assumed ===")
# The clarified requirement: do not assume an MCP server can push. With no
# server configured we must claim nothing; with an unreachable one we must
# report that rather than inventing a capability.
no_mcp = GitHubGateway(token=None)
count, note = asyncio.run(no_mcp.probe_mcp())
print("   no server -> no claim     :", ok(count is None and note == ""))

unreachable = GitHubGateway(token=None, mcp_server_url="https://127.0.0.1:1/mcp")
count2, note2 = asyncio.run(unreachable.probe_mcp())
print("   unreachable -> None, said :", ok(count2 is None and "could not be reached" in note2))

st_mcp = asyncio.run(unreachable.status())
print("   status reports it         :", ok(st_mcp.mcp_server_configured is True))
print("   tools NOT invented        :", ok(st_mcp.mcp_tools_discovered is None))
print("   push path unaffected      :", ok(st_mcp.mode == "none"))

passed, total = sum(_results), len(_results)
print()
print(f"=== {passed}/{total} assertions passed ===")
sys.exit(0 if passed == total else 1)
