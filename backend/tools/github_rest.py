"""Direct GitHub REST adapter: the path that does not need an MCP server.

WHY THIS FILE EXISTS
    ``docs/mcp.md`` 4 forbids assuming what an MCP server can do, and no GitHub
    MCP server is configured in this repository, so nothing can be discovered.
    That leaves two honest options: ship an integration that cannot work, or
    talk to GitHub directly.

    This is the second. ``backend/services/github_gateway.py`` prefers a
    discovered MCP tool when one exists and falls back here otherwise, so the
    feature works today without pretending a server is connected.

WHAT IT DOES AND DOES NOT DO
    Enough of the REST API for DETROIT's two operations, and nothing more:

        list repositories      GET  /user/repos
        read a repository      GET  /repos/{owner}/{repo}
        list branches          GET  /repos/{owner}/{repo}/branches
        read a tree            GET  /repos/{owner}/{repo}/git/trees/{sha}
        create a branch        POST /repos/{owner}/{repo}/git/refs
        commit a set of files  the Git Data API, below

    There is no delete, no force update, and no history rewrite. Those are
    ``destructive`` in ``docs/mcp.md`` 2.1 and would need their own explicit
    authorization; leaving them unimplemented means they cannot happen by
    accident.

    There is also no blob READ and no pull-request creation. Both existed and
    were removed: importing a repository and managing pull requests are
    outside this feature's scope, which is "push a generated project into the
    user's existing repository" and nothing else. Re-adding either means
    re-adding its approval path and its tests, deliberately.

WHY THE GIT DATA API FOR COMMITS, NOT THE CONTENTS API
    ``PUT /contents/{path}`` writes ONE file per call and per commit. A
    generated project is dozens of files, which would mean dozens of commits
    and a repository that cannot be reviewed. The Git Data API builds one tree
    and one commit:

        create blobs -> create a tree -> create a commit -> move the ref

    It is also the only way to make the write atomic from a reviewer's point of
    view: either the whole generated project landed or none of it did.

    The ref is moved WITHOUT force. A non-fast-forward update fails loudly
    rather than discarding whatever was on the branch, which is the behaviour
    ``skills.md`` 21 requires.

SECURITY
    - The token is a ``SecretStr``, read only when a header is built, and never
      logged, never returned, never placed in agent context
      (``docs/security.md`` 2, ``docs/mcp.md`` 6).
    - ``__repr__`` cannot print it.
    - Every error raised here carries a message safe to show a user: GitHub's
      own response body is inspected for a message but the request, the headers
      and the URL never reach the caller.
    - Repository names are validated against a strict pattern before they
      become part of a URL path. A name is caller-supplied, and this is what
      keeps it from being a path.

LAYER
    Tools (``backend/tools/``). One HTTP client and a credential. It knows
    nothing about projects, ownership or the workflow.

Spec: docs/mcp.md 2.1, 4, 6; docs/security.md 2, 3.4, 6; skills.md 21.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

# One request's budget. Generous enough for a tree of a few hundred files,
# bounded so a hung call cannot stall the stage that asked.
DEFAULT_TIMEOUT_SECONDS = 30.0

# GitHub's own rules: owner and repo are [A-Za-z0-9._-], owner cannot start
# with a hyphen. Anchored, because this value becomes a URL path segment and a
# caller supplies it. Anything else is refused before a request is built.
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# A ref may not contain a path traversal, a space, or the characters git
# itself forbids. Checked for the same reason as the repo name.
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

# A hostname: dot-separated labels, optionally with a port. Used only to tell
# a leading host apart from a leading owner when a URL has no scheme. It is
# deliberately NOT an allowlist of GitHub hosts — GitHub Enterprise runs on
# private names, and which server is contacted is decided by
# `github_api_base_url`, never by this string.
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+(?::\d{1,5})?$"
)

# Files are committed under the repository root. A path must stay relative and
# may not climb out, exactly as the preview route refuses traversal
# (docs/security.md 3.4).
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    """Base for adapter failures. Messages here are safe to display."""


class GitHubAuthError(GitHubError):
    """401/403 from GitHub: the token is missing, wrong, or lacks a scope."""


class GitHubNotFoundError(GitHubError):
    """404: no such repository, branch or path — or the token cannot see it.

    GitHub deliberately returns 404 rather than 403 for a private repository
    the token cannot read, so this does not distinguish them either.
    """


class GitHubConflictError(GitHubError):
    """409/422: the remote moved, or the ref already exists.

    Surfaced separately because it is the one failure a user can resolve, and
    the one that must never be resolved by forcing.
    """


class GitHubUnavailableError(GitHubError):
    """The request did not complete: network, timeout, or 5xx."""


@dataclass(frozen=True)
class RepoRef:
    """One repository, validated. Construct via :meth:`parse`."""

    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @classmethod
    def parse(cls, value: str) -> "RepoRef":
        """A repository URL **or** ``owner/repo`` -> a validated reference.

        WHY URLs ARE ACCEPTED
            The user's own words for "my repository" are the address bar:
            they copy ``https://github.com/me/site`` from the browser, or the
            clone string from the green button. Making them hand-extract
            ``me/site`` from that is a needless step that invites typos.

            Every form below reduces to the same two validated segments, so
            accepting URLs widens the INPUT, never what is trusted.

        FORMS ACCEPTED
            https://github.com/owner/repo          (and /repo.git, and a
                                                    trailing slash, and any
                                                    deeper path such as
                                                    /tree/main)
            http://github.com/owner/repo           (normalised; scheme is not
                                                    used for the API call)
            git@github.com:owner/repo.git          (SCP-style clone string)
            ssh://git@github.com/owner/repo.git
            github.com/owner/repo                  (no scheme)
            owner/repo                             (what it reduces to)

        WHAT IS STILL REFUSED
            The owner and repo segments are matched against GitHub's own
            naming rules AFTER extraction, exactly as before. A host is not
            validated against a list, because GitHub Enterprise lives on a
            private hostname — the credential and ``github_api_base_url``
            decide which server is actually contacted, never this string.

        Raises:
            GitHubError: the value is not a repository reference.
        """
        raw = (value or "").strip()
        if not raw:
            raise GitHubError("A repository URL or 'owner/name' is required.")

        text = raw

        # SCP-style clone strings have no scheme and use ':' as the separator.
        # Convert to a path form before anything else looks at it.
        if "@" in text and "://" not in text:
            _, _, after = text.partition("@")
            text = after.replace(":", "/", 1)
        elif "://" in text:
            text = text.split("://", 1)[1]
            # ssh://git@host/... keeps userinfo after the scheme is removed.
            # Strip it before the host, or "git@host" becomes the owner.
            if "@" in text.split("/", 1)[0]:
                text = text.partition("@")[2]

        # A traversal segment is never part of a repository reference, and the
        # host heuristic below would otherwise swallow it: "../etc/passwd" has
        # a head of ".." which contains a dot, so it would be mistaken for a
        # hostname and leave "etc/passwd". Refused outright instead.
        if any(part in (".", "..") for part in text.split("/")):
            raise GitHubError(f"{raw!r} is not a repository reference.")

        # Drop a host if one survived, plus any credentials in front of it.
        # A host must look like a hostname: dotted labels or bare "localhost".
        if "/" in text:
            head, _, rest = text.partition("/")
            if _HOSTNAME.match(head) or head == "localhost":
                text = rest

        text = text.split("?", 1)[0].split("#", 1)[0].strip("/")
        if text.endswith(".git"):
            text = text[: -len(".git")]

        parts = [p for p in text.split("/") if p]
        if len(parts) < 2:
            raise GitHubError(
                f"{raw!r} is not a repository. Use a GitHub URL or 'owner/name'."
            )

        # Deeper paths (/tree/main, /blob/...) are tolerated: the first two
        # segments are the repository, and the rest is navigation.
        owner, repo = parts[0], parts[1]
        if not _OWNER.match(owner) or not _REPO.match(repo):
            raise GitHubError(f"'{owner}/{repo}' is not a valid repository name.")
        return cls(owner=owner, repo=repo)


@dataclass(frozen=True)
class RemoteRepo:
    """A repository as GitHub describes it."""

    full_name: str
    default_branch: str
    private: bool
    permissions_push: bool
    html_url: str
    updated_at: str | None = None


def validate_repo_path(path: str) -> str:
    """Refuse a path that could escape the repository root.

    The same containment rule the preview route applies to ``dist/``
    (``docs/security.md`` 3.4), applied to the other direction of travel: these
    paths are written into a commit and read back onto a developer's disk.
    """
    value = (path or "").strip().replace("\\", "/")
    # An ABSOLUTE path is refused, not normalised. Stripping the leading slash
    # would silently turn "/etc/passwd" into a valid relative path, and a
    # security check that quietly rewrites its input teaches the caller
    # nothing. Repository paths are relative to the root by definition.
    if value.startswith("/"):
        raise GitHubError(f"Repository paths must be relative: {path!r}")
    if not value or not _SAFE_PATH.match(value):
        raise GitHubError(f"Unsafe repository path: {path!r}")
    parts = value.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise GitHubError(f"Unsafe repository path: {path!r}")
    return value


class GitHubRestClient:
    """A thin, bounded client for the subset of GitHub this product needs."""

    def __init__(
        self,
        token: SecretStr,
        *,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if token is None or not token.get_secret_value().strip():
            raise GitHubError("A GitHub token is required.")
        self._token = token
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def __repr__(self) -> str:
        """Never render the token, including inside a traceback."""
        return f"GitHubRestClient(base_url={self._base!r}, token='set')"

    def _headers(self) -> dict[str, str]:
        """Built at the point of use so the secret is not held in a dict."""
        return {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "DETROIT-builder",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """One request, with every failure mapped to a safe, typed error."""
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=json, params=params
                )
        except httpx.TimeoutException:
            logger.warning("github request timed out method=%s", method)
            raise GitHubUnavailableError("GitHub did not respond in time.") from None
        except httpx.HTTPError as exc:
            # Never the exception text: it embeds the URL and can embed headers.
            logger.warning("github request failed type=%s", type(exc).__name__)
            raise GitHubUnavailableError("GitHub could not be reached.") from None

        if response.status_code in (401, 403):
            # Rate limiting also arrives as 403; separate it so the message is
            # actionable rather than blaming the credential.
            if response.headers.get("x-ratelimit-remaining") == "0":
                logger.warning("github rate limit reached")
                raise GitHubUnavailableError(
                    "GitHub's rate limit has been reached. Try again later."
                )
            logger.warning("github auth rejected status=%s", response.status_code)
            raise GitHubAuthError(
                "GitHub rejected the credential, or it lacks the required scope."
            )
        if response.status_code == 404:
            raise GitHubNotFoundError("No such repository, branch or file.")
        if response.status_code in (409, 422):
            raise GitHubConflictError(_safe_message(response))
        if response.status_code >= 500:
            logger.warning("github server error status=%s", response.status_code)
            raise GitHubUnavailableError("GitHub returned a server error.")
        if response.status_code >= 400:
            raise GitHubError(_safe_message(response))

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise GitHubUnavailableError(
                "GitHub returned a response that was not JSON."
            ) from None

    # --- Reads ----------------------------------------------------------

    async def list_repositories(self, *, limit: int = 100) -> list[RemoteRepo]:
        """Repositories the token can see, most recently pushed first.

        ``affiliation`` excludes repositories the user merely stars or watches.
        Only those with push permission are useful here, but all are returned
        so the UI can explain why one cannot be selected.
        """
        data = await self._request(
            "GET",
            "/user/repos",
            params={
                "per_page": min(max(limit, 1), 100),
                "sort": "pushed",
                "affiliation": "owner,collaborator,organization_member",
            },
        )
        return [_repo_from(item) for item in (data or []) if isinstance(item, dict)]

    async def get_repository(self, ref: RepoRef) -> RemoteRepo:
        data = await self._request("GET", f"/repos/{ref.owner}/{ref.repo}")
        return _repo_from(data or {})

    async def list_branches(self, ref: RepoRef, *, limit: int = 100) -> list[str]:
        data = await self._request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/branches",
            params={"per_page": min(max(limit, 1), 100)},
        )
        return [b["name"] for b in (data or []) if isinstance(b, dict) and "name" in b]

    async def get_branch_sha(self, ref: RepoRef, branch: str) -> str:
        """The commit a branch points at."""
        if not _REF.match(branch or ""):
            raise GitHubError(f"'{branch}' is not a valid branch name.")
        data = await self._request(
            "GET", f"/repos/{ref.owner}/{ref.repo}/commits/{branch}"
        )
        sha = (data or {}).get("sha")
        if not isinstance(sha, str):
            raise GitHubUnavailableError("GitHub did not return a commit for that branch.")
        return sha

    async def list_tree(
        self, ref: RepoRef, branch: str, *, subdir: str | None = None
    ) -> list[str]:
        """Every file path on a branch, optionally under one subdirectory.

        Recursive in one request. GitHub truncates very large trees and says so;
        a truncated answer is raised rather than silently treated as complete,
        because a partial file list would make a pull look like a deletion.
        """
        sha = await self.get_branch_sha(ref, branch)
        data = await self._request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/git/trees/{sha}",
            params={"recursive": "1"},
        )
        if (data or {}).get("truncated"):
            raise GitHubError(
                "That repository's file list is too large to read in one request."
            )
        paths = [
            entry["path"]
            for entry in (data or {}).get("tree", [])
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and isinstance(entry.get("path"), str)
        ]
        if subdir:
            prefix = validate_repo_path(subdir).rstrip("/") + "/"
            paths = [p for p in paths if p.startswith(prefix)]
        return sorted(paths)

    # --- Writes (approval-gated by the caller) --------------------------

    async def create_branch(self, ref: RepoRef, new_branch: str, from_sha: str) -> None:
        """Create a ref. Fails if it already exists — never moves an existing one."""
        if not _REF.match(new_branch or ""):
            raise GitHubError(f"'{new_branch}' is not a valid branch name.")
        await self._request(
            "POST",
            f"/repos/{ref.owner}/{ref.repo}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": from_sha},
        )

    async def commit_files(
        self,
        ref: RepoRef,
        *,
        branch: str,
        files: dict[str, str],
        message: str,
        parent_sha: str,
    ) -> str:
        """Commit a set of files as ONE commit, and move the branch to it.

        The Git Data API sequence, which is what makes this atomic:
        blobs -> tree -> commit -> ref update.

        The tree is created with ``base_tree=parent`` so files NOT in ``files``
        are preserved. This is an additive update: it never deletes a path the
        generated project happens not to contain.

        The ref update is sent WITHOUT ``force``. If the branch moved since
        ``parent_sha`` was read, GitHub refuses and a
        :class:`GitHubConflictError` surfaces — which is the entire point. A
        forced update here would silently destroy someone's commits.

        Returns:
            The new commit sha.
        """
        if not files:
            raise GitHubError("There is nothing to commit.")
        if not _REF.match(branch or ""):
            raise GitHubError(f"'{branch}' is not a valid branch name.")

        tree_entries: list[dict[str, Any]] = []
        for path, text in sorted(files.items()):
            safe = validate_repo_path(path)
            blob = await self._request(
                "POST",
                f"/repos/{ref.owner}/{ref.repo}/git/blobs",
                json={"content": text, "encoding": "utf-8"},
            )
            tree_entries.append(
                {
                    "path": safe,
                    "mode": "100644",  # a regular file; never a symlink or submodule
                    "type": "blob",
                    "sha": (blob or {}).get("sha"),
                }
            )

        tree = await self._request(
            "POST",
            f"/repos/{ref.owner}/{ref.repo}/git/trees",
            json={"base_tree": parent_sha, "tree": tree_entries},
        )
        commit = await self._request(
            "POST",
            f"/repos/{ref.owner}/{ref.repo}/git/commits",
            json={
                "message": message,
                "tree": (tree or {}).get("sha"),
                "parents": [parent_sha],
            },
        )
        new_sha = (commit or {}).get("sha")
        if not isinstance(new_sha, str):
            raise GitHubUnavailableError("GitHub did not return the new commit.")

        await self._request(
            "PATCH",
            f"/repos/{ref.owner}/{ref.repo}/git/refs/heads/{branch}",
            json={"sha": new_sha, "force": False},
        )
        logger.info(
            "github commit pushed repo=%s branch=%s files=%d",
            ref.full_name,
            branch,
            len(files),
        )
        return new_sha


def _repo_from(item: dict[str, Any]) -> RemoteRepo:
    permissions = item.get("permissions") or {}
    return RemoteRepo(
        full_name=str(item.get("full_name") or ""),
        default_branch=str(item.get("default_branch") or "main"),
        private=bool(item.get("private")),
        permissions_push=bool(permissions.get("push")),
        html_url=str(item.get("html_url") or ""),
        updated_at=item.get("pushed_at") or item.get("updated_at"),
    )


def _safe_message(response: httpx.Response) -> str:
    """GitHub's own error message, or a generic one.

    Only the ``message`` field is taken. The rest of an error body can echo the
    request, and this string is shown to a user (``docs/security.md`` 6).
    """
    try:
        body = response.json()
    except ValueError:
        return "GitHub rejected the request."
    message = body.get("message") if isinstance(body, dict) else None
    return str(message) if isinstance(message, str) and message else (
        "GitHub rejected the request."
    )
