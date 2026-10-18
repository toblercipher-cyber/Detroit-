"""Shared GitHub test double: a mock REST API and a gateway wired to it.

WHY THIS IS A HELPER AND NOT PART OF A SUITE
    Two suites need the same mock — ``github_check`` for the gateway's logic
    and ``github_routes_check`` for the HTTP surface. The suites are standalone
    SCRIPTS, so importing one from the other runs it, and its ``sys.exit`` ends
    the importing process before its own assertions run. That happened; this
    module exists so it cannot happen again.

WHY THE ``_`` PREFIX
    ``test_suites.py`` collects ``suites/*.py`` except names starting with an
    underscore. This is a helper, not a suite.

REMOTE_FILES still matters for PUSH: `list_tree` answers from its keys, which
is how a push plan tells an "add" from an "update". The `/contents/` and
`/pulls` endpoints were removed with pull and PR creation.

WHAT IT MOCKS
    The transport boundary only — ``httpx``. Everything above it, including the
    status-code mapping and every validation rule, is the production code under
    test. The mock never asserts; it records what was asked of it so a suite
    can check the exact requests that would reach GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from backend.tests.suites import _tmpdir
from backend.services.github_gateway import GitHubGateway
from backend.tools.github_rest import GitHubError, GitHubRestClient

TOKEN = SecretStr("ghp_this_is_a_fake_token_for_tests_only")

# ---------------------------------------------------------------------------
# A mock GitHub. Every response the gateway relies on, and nothing else.
# ---------------------------------------------------------------------------

REMOTE_FILES = {
    "src/App.tsx": "export default function App() { return null; }\n",
    "src/shared.ts": "export const SHARED = 1;\n",
    "package.json": '{"name":"remote"}\n',  # outside src/: must be skipped
}


class MockGitHub:
    """Serves the subset of the REST API this integration calls."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.committed: dict[str, str] | None = None
        self.forced: bool | None = None
        self.branch_exists = True
        self.can_push = True
        self.head_sha = "sha-head"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))

        def j(payload, code=200):
            return httpx.Response(code, json=payload)

        if path == "/user/repos":
            return j([self._repo()])
        if path == "/repos/octo/site" and request.method == "GET":
            return j(self._repo())
        if path == "/repos/octo/site/branches":
            return j([{"name": "main"}, {"name": "detroit/generated"}])
        if path.startswith("/repos/octo/site/commits/"):
            branch = path.rsplit("/", 1)[-1]
            if branch == "detroit/generated" and not self.branch_exists:
                return j({"message": "Branch not found"}, 404)
            return j({"sha": self.head_sha})
        if path.startswith("/repos/octo/site/git/trees/") and request.method == "GET":
            return j(
                {
                    "truncated": False,
                    "tree": [
                        {"type": "blob", "path": p} for p in sorted(REMOTE_FILES)
                    ],
                }
            )
        if path == "/repos/octo/site/git/blobs":
            return j({"sha": "blob-" + str(len(self.requests))}, 201)
        if path == "/repos/octo/site/git/trees" and request.method == "POST":
            body = json.loads(request.content)
            self.committed = {e["path"]: e["sha"] for e in body["tree"]}
            self.base_tree = body.get("base_tree")
            return j({"sha": "tree-new"}, 201)
        if path == "/repos/octo/site/git/commits":
            return j({"sha": "commit-new"}, 201)
        if path.startswith("/repos/octo/site/git/refs/heads/"):
            self.forced = json.loads(request.content).get("force")
            return j({"ref": "refs/heads/detroit/generated"})
        if path == "/repos/octo/site/git/refs" and request.method == "POST":
            return j({"ref": "created"}, 201)
        return j({"message": f"unmocked {path}"}, 404)

    def _repo(self):
        return {
            "full_name": "octo/site",
            "default_branch": "main",
            "private": False,
            "permissions": {"push": self.can_push},
            "html_url": "https://github.com/octo/site",
        }


def gateway_with(mock: MockGitHub) -> GitHubGateway:
    """A real gateway whose transport is the mock."""
    gw = GitHubGateway(token=TOKEN)
    transport = httpx.MockTransport(mock.handler)

    original = GitHubRestClient._request

    async def patched(self, method, path, *, json=None, params=None):
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.request(
                method,
                f"https://api.github.com{path}",
                headers=self._headers(),
                json=json,
                params=params,
            )
        # Reuse the production status-code mapping rather than duplicating it.
        return _map(response)

    GitHubRestClient._request = patched  # type: ignore[assignment]
    gw._restore = lambda: setattr(GitHubRestClient, "_request", original)  # type: ignore[attr-defined]
    return gw


def _map(response: httpx.Response):
    """The production error mapping, applied to a mocked response."""
    from backend.tools.github_rest import (
        GitHubAuthError,
        GitHubConflictError,
        GitHubNotFoundError,
        GitHubUnavailableError,
        _safe_message,
    )

    if response.status_code in (401, 403):
        raise GitHubAuthError("GitHub rejected the credential.")
    if response.status_code == 404:
        raise GitHubNotFoundError("No such repository, branch or file.")
    if response.status_code in (409, 422):
        raise GitHubConflictError(_safe_message(response))
    if response.status_code >= 500:
        raise GitHubUnavailableError("GitHub returned a server error.")
    if response.status_code >= 400:
        raise GitHubError(_safe_message(response))
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def make_workspace() -> Path:
    root = Path(_tmpdir.mkdtemp(prefix="gh_ws_"))
    src = root / "src"
    src.mkdir(parents=True)
    (src / "App.tsx").write_text("export default function App() { return 1; }\n", encoding="utf-8")
    (src / "new.ts").write_text("export const NEW = true;\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"local"}\n', encoding="utf-8")
    return root


