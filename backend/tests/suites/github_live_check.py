"""OPTIONAL live GitHub test. Skipped unless explicitly enabled.

WHY THIS IS OPT-IN AND NOT PART OF THE NORMAL RUN
    Everything else in this repository runs offline against mocks. This one
    talks to the real GitHub with a real credential, so it cannot be a default:
    a suite that silently needs a token fails for everyone who does not have
    one, and a suite that silently WRITES to a repository is a trap.

    It therefore requires two deliberate signals, not one:

        GITHUB_LIVE_TESTS=1      you meant to do this
        GITHUB_TOKEN=...         the credential
        GITHUB_TEST_REPO=o/r     which repository (read-only use below)

    Missing any of them is a SKIP, never a failure.

WHAT IT DOES AND DOES NOT TOUCH
    Reads only: the authenticated user's repositories, one repository's
    metadata, and its branch list. It creates no branch, writes no file and
    opens no pull request.

    That is deliberate. The purpose here is to prove the credential, the
    base URL, the auth header and the response shapes are right — the parts a
    mock cannot prove. Proving the write path needs a throwaway repository and
    a human who chose it, which is a decision for the operator, not for a test
    file. ``github_check`` already proves the write logic, including that the
    ref update is never forced.

Spec: docs/mcp.md 2.1; docs/security.md 2.
"""

from __future__ import annotations

import asyncio
import os
import sys

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

_env = {**dotenv_values(".env"), **os.environ}

if _env.get("GITHUB_LIVE_TESTS") not in ("1", "true", "yes"):
    skip("GITHUB_LIVE_TESTS is not set; live GitHub tests are opt-in.")

token = _env.get("GITHUB_TOKEN")
if not token:
    skip("GITHUB_TOKEN is not set; there is no credential to test with.")

repo_name = _env.get("GITHUB_TEST_REPO")
if not repo_name:
    skip("GITHUB_TEST_REPO is not set; nothing to read.")

os.environ.setdefault("ANTHROPIC_API_KEY", "stub-key-for-tests")

from pydantic import SecretStr  # noqa: E402

from backend.services.github_gateway import GitHubGateway  # noqa: E402
from backend.tools.github_rest import GitHubError  # noqa: E402

gateway = GitHubGateway(
    token=SecretStr(token),
    api_base_url=_env.get("GITHUB_API_BASE_URL", "https://api.github.com"),
)


async def main() -> None:
    print("=== 1. The credential actually works ===")
    status = await gateway.status()
    print("   configured          :", ok(status.configured))
    if not status.configured:
        print("   reason              :", status.reason)
    print("   account resolved    :", status.account, ok(bool(status.account)))
    print("   no token in status  :", ok(token not in repr(status)))

    print()
    print("=== 2. Repositories list ===")
    repos = await gateway.list_repositories()
    print("   returned some       :", ok(len(repos) > 0))
    print("   each has a full_name:", ok(all("/" in r.full_name for r in repos)))

    print()
    print(f"=== 3. {repo_name} is readable ===")
    try:
        repo = await gateway.get_repository(repo_name)
        print("   metadata read       :", ok(repo.full_name.lower() == repo_name.lower()))
        print("   default branch      :", repo.default_branch, ok(bool(repo.default_branch)))
        branches = await gateway.list_branches(repo_name)
        print("   branches listed     :", len(branches), ok(len(branches) > 0))
    except GitHubError as exc:
        print("   FAILED              :", exc)
        _results.append(False)

    print()
    print("   NOTE: nothing was written. The write path is proven against")
    print("         mocks in github_check (including that it never forces).")

    passed, total = sum(_results), len(_results)
    print()
    print(f"=== {passed}/{total} assertions passed ===")
    sys.exit(0 if passed == total else 1)


asyncio.run(main())
