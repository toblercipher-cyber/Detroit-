"""GitHub synchronisation for a DETROIT project: status, repositories, push, pull.

WHY THIS FILE EXISTS
    Something has to turn "send my generated site to GitHub" into a reviewable
    set of file changes, and back again. This owns that, and only that: it
    knows about repositories and workspaces, never about HTTP routes, agents or
    the workflow graph.

---------------------------------------------------------------------------
THE DESIGN DECISION THIS FILE SETTLES: MCP OR REST?

    ``docs/mcp.md`` 4 forbids hard-coding MCP tool names, and it is right to:
    names differ between servers and versions. But a DETERMINISTIC push needs
    to know which tool creates a branch and which one commits a tree. Those two
    requirements cannot both be satisfied by code:

        - hard-coding "create_branch" violates 4
        - discovering tools gives names and schemas, not MEANING

    Resolving it by guessing from names would be exactly the assumption 4
    exists to prevent. So the split is:

    **Push and pull use the REST adapter** (``backend/tools/github_rest.py``).
    They are deterministic, user-facing, and must behave identically every
    time. ``docs/mcp.md`` 2.1 lists the capability classes GitHub integration
    needs; the adapter implements exactly those and nothing destructive.

    **MCP is for the GitHub Operator agent** (``backend/agents/github_operator.py``).
    A model CAN read a discovered tool's description and schema and choose
    appropriately — that is the one consumer for which discovery-without-
    hard-coded-names actually works. ``McpClient.call_tool`` enforces
    discovery, role and approval for it.

    This is the answer to "inspect the server's capabilities first; if it
    cannot support genuine import/export, implement a secure GitHub API
    adapter rather than displaying a fake success." Discovery still runs and is
    reported in the status; it simply is not what drives push and pull.

---------------------------------------------------------------------------
WHAT PUSH ACTUALLY SYNCHRONISES

    **workspace ``src/`` -> a branch.**
        Only ``src/`` travels. The rest of a generated workspace is the frozen
        builder template (``package.json``, ``vite.config.ts``, the tsconfigs)
        plus ``node_modules/`` and ``dist/``. The template is identical for
        every project and the other two are build output; committing any of
        them would make every DETROIT repository mostly noise.

        The commit is ADDITIVE. The tree is built on the branch's existing
        tree, so a file the workspace does not contain is left alone rather
        than deleted. DETROIT does not know why a file is in someone's
        repository, so it does not remove it.

        The ref is moved without force. If the branch advanced since the plan
        was made, the push fails with a conflict instead of discarding commits.

    **Unsupported files.** Non-UTF-8 blobs and anything over the per-file
    ceiling are skipped and REPORTED, never silently dropped: a push that
    quietly omitted half a project would look like it had worked.

    **Pull/import was removed.** It existed, worked and was tested; importing
    a repository is outside this feature's scope, which is "push a generated
    project into the user's existing repository". Re-adding it means re-adding
    its conflict handling and its approval path deliberately, not inheriting
    them by accident.

---------------------------------------------------------------------------
APPROVAL

    Nothing here executes a write without a caller that already holds a spent
    approval token. ``plan_push`` MUTATES NOTHING — it reads the remote and the
    workspace and describes what would change. The route layer pairs it with
    ``ExternalActionService`` (``docs/mcp.md`` 5), and ``execute_push`` is
    reachable only after that gate.

LAYER
    Services (``backend/services/``). Depends on tools and schemas; imported by
    the API and the agent. It opens no HTTP route and holds no request state.

Spec: docs/mcp.md 2.1, 4, 5, 6; docs/security.md 2, 3.1, 3.4, 6; skills.md 21.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from backend.tools.github_rest import (
    GitHubError,
    GitHubRestClient,
    RemoteRepo,
    RepoRef,
    validate_repo_path,
)

logger = logging.getLogger(__name__)

# A generated project can be large but not unbounded. A push that would carry
# more than this is refused rather than truncated, because a partial push looks
# like a complete one in the resulting commit.
MAX_SYNC_FILES = 400

# Per-file ceiling. Generated source is text; anything this size is not source.
MAX_FILE_BYTES = 512_000

# Push emits these three and no others. "unchanged" and "conflict" existed
# for the removed pull path and are gone with it.
ChangeKind = Literal["add", "update", "skip"]
SyncDirection = Literal["push"]


class GitHubUnconfiguredError(RuntimeError):
    """No route to GitHub is configured. Maps to 501 capability_unavailable."""


@dataclass(frozen=True)
class GitHubStatus:
    """What the integration can actually do right now.

    Deliberately contains NO credential and nothing derived from one — not a
    prefix, not a length, not a masked form. It answers "is this usable", which
    is all a browser needs (``docs/security.md`` 2).
    """

    configured: bool
    mode: Literal["rest", "mcp", "none"]
    reason: str = ""
    mcp_server_configured: bool = False
    mcp_tools_discovered: int | None = None
    account: str | None = None


@dataclass(frozen=True)
class FileChange:
    """One file's fate in a sync, with a reason when it is not applied."""

    path: str
    kind: ChangeKind
    reason: str = ""
    size_bytes: int = 0


@dataclass(frozen=True)
class SyncPlan:
    """What a push or pull WOULD do. Produced without mutating anything."""

    direction: SyncDirection
    repository: str
    branch: str
    base_branch: str
    changes: list[FileChange] = field(default_factory=list)
    # The commit the plan was computed against. Execution sends this as the
    # parent, so a branch that moved in between is caught as a conflict rather
    # than silently overwritten.
    head_sha: str | None = None

    def _count(self, kind: ChangeKind) -> int:
        return sum(1 for c in self.changes if c.kind == kind)

    @property
    def added(self) -> int:
        return self._count("add")

    @property
    def updated(self) -> int:
        return self._count("update")

    @property
    def skipped(self) -> int:
        return self._count("skip")

    @property
    def has_effect(self) -> bool:
        """Whether applying this would change anything at all."""
        return bool(self.added or self.updated)


@dataclass(frozen=True)
class SyncResult:
    """The outcome of an executed sync."""

    direction: SyncDirection
    repository: str
    branch: str
    files_written: int
    commit_sha: str | None = None
    detail: str = ""


class GitHubGateway:
    """Reads and writes one GitHub repository on behalf of one project."""

    def __init__(
        self,
        *,
        token: SecretStr | None,
        api_base_url: str = "https://api.github.com",
        mcp_server_url: str | None = None,
    ) -> None:
        """
        Args:
            token: the GitHub credential, or None. Held, never exposed.
            api_base_url: overridable for GitHub Enterprise.
            mcp_server_url: recorded for status reporting. Discovery for the
                GitHub Operator happens through ``McpClient``, not here.
        """
        self._token = token
        self._api_base = api_base_url
        self._mcp_server_url = mcp_server_url

    def __repr__(self) -> str:
        return f"GitHubGateway(configured={self.is_configured}, token='redacted')"

    @property
    def is_configured(self) -> bool:
        return self._token is not None

    def _client(self) -> GitHubRestClient:
        if self._token is None:
            raise GitHubUnconfiguredError(
                "GitHub is not configured on this server. Set GITHUB_TOKEN."
            )
        return GitHubRestClient(self._token, base_url=self._api_base)

    # --- Status and repositories ----------------------------------------

    async def probe_mcp(self) -> tuple[int | None, str]:
        """Ask a configured MCP server what it ACTUALLY advertises.

        WHY THIS RUNS AT ALL
            ``docs/mcp.md`` 4 forbids assuming a server's capabilities, and the
            inverse is just as wrong: assuming it has none. If an operator
            configured a GitHub MCP server, the honest thing is to connect and
            count what it offers, then say so.

        WHY THE ANSWER DOES NOT CHANGE WHICH PATH PUSHES
            Discovery returns names and JSON schemas. It does not return
            meaning, and no amount of string matching turns ``push_files`` into
            a guarantee that it commits a tree the way this product needs —
            that guess is precisely what 4 forbids. So the count is REPORTED,
            and push still goes through the REST adapter.

            A future version could let the GitHub Operator agent drive a
            discovered tool for this, because a model can read a description.
            That path exists (``McpClient.call_tool``) and is not wired to
            push, deliberately.

        Returns:
            ``(tool_count_or_None, human_readable_note)``.
        """
        if not self._mcp_server_url:
            return None, ""
        try:
            from backend.tools.mcp_client import McpClient

            discovery = await McpClient(
                self._mcp_server_url, self._token
            ).discover()
        except Exception as exc:
            # Type only: the message can carry the URL or the auth header.
            logger.warning("mcp probe failed type=%s", type(exc).__name__)
            return None, "The configured MCP server could not be reached."

        writable = sum(
            1 for t in discovery.tools.values() if t.capability != "read"
        )
        return (
            len(discovery.tools),
            f"MCP server reachable: {len(discovery.tools)} tool(s), "
            f"{writable} of them write-capable. Push uses the REST adapter "
            "regardless, because a tool's name does not prove what it does.",
        )

    async def status(self) -> GitHubStatus:
        """Whether GitHub is usable, verified by actual calls.

        A token that is merely PRESENT proves nothing — it can be revoked,
        expired or wrong-scoped. So this spends one request to find out, and
        reports what it learns. Saying "connected" because a variable is set is
        exactly the false claim this integration must not make
        (``docs/mcp.md`` 1).

        When an MCP server is configured it is probed too, so the status
        reflects what that server really advertises rather than its mere
        presence.
        """
        mcp_count, mcp_note = await self.probe_mcp()

        if self._token is None:
            return GitHubStatus(
                configured=False,
                mode="none",
                # Names no environment variable: this string is shown to any
                # signed-in user, and server configuration is the operator's
                # business (docs/security.md 6).
                reason="GitHub is not configured on this server.",
                mcp_server_configured=bool(self._mcp_server_url),
                mcp_tools_discovered=mcp_count,
            )
        try:
            repos = await self._client().list_repositories(limit=1)
        except GitHubError as exc:
            return GitHubStatus(
                configured=False,
                mode="none",
                reason=str(exc),
                mcp_server_configured=bool(self._mcp_server_url),
                mcp_tools_discovered=mcp_count,
            )
        account = repos[0].full_name.split("/")[0] if repos else None
        return GitHubStatus(
            configured=True,
            mode="rest",
            reason=mcp_note,
            mcp_server_configured=bool(self._mcp_server_url),
            mcp_tools_discovered=mcp_count,
            account=account,
        )

    async def list_repositories(self) -> list[RemoteRepo]:
        """Repositories the credential can see."""
        return await self._client().list_repositories()

    async def get_repository(self, full_name: str) -> RemoteRepo:
        return await self._client().get_repository(RepoRef.parse(full_name))

    async def list_branches(self, full_name: str) -> list[str]:
        return await self._client().list_branches(RepoRef.parse(full_name))

    # --- Push -----------------------------------------------------------

    def _collect_workspace_files(self, workspace: Path) -> tuple[dict[str, str], list[FileChange]]:
        """Read ``src/`` out of a workspace, with per-file refusals recorded.

        Returns the readable files and the changes that could not be included,
        so the plan can explain a skip rather than silently shrinking.
        """
        src = workspace / "src"
        files: dict[str, str] = {}
        skipped: list[FileChange] = []
        if not src.is_dir():
            return files, skipped

        for path in sorted(p for p in src.rglob("*") if p.is_file()):
            rel = path.relative_to(workspace).as_posix()
            try:
                size = path.stat().st_size
            except OSError:
                skipped.append(FileChange(rel, "skip", "could not be read"))
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(
                    FileChange(rel, "skip", f"larger than {MAX_FILE_BYTES // 1000} kB", size)
                )
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                skipped.append(FileChange(rel, "skip", "not a UTF-8 text file", size))
                continue
            try:
                validate_repo_path(rel)
            except GitHubError:
                skipped.append(FileChange(rel, "skip", "unsafe path"))
                continue
            files[rel] = text
        return files, skipped

    async def plan_push(
        self, workspace: Path, *, repository: str, branch: str, base_branch: str | None = None
    ) -> SyncPlan:
        """Describe what pushing this workspace would change. Mutates nothing.

        Reads the remote branch (or its base, when the branch does not exist
        yet) and compares file by file, so the user sees adds and updates
        separately rather than a file count.
        """
        client = self._client()
        ref = RepoRef.parse(repository)
        repo = await client.get_repository(ref)
        base = base_branch or repo.default_branch

        local, changes = self._collect_workspace_files(workspace)
        if not local:
            raise GitHubError(
                "This project has no generated source to push yet. Build it first."
            )
        if len(local) > MAX_SYNC_FILES:
            raise GitHubError(
                f"This project has {len(local)} files, more than the {MAX_SYNC_FILES} "
                "this integration will push in one commit."
            )

        # Compare against the target branch when it exists, otherwise its base:
        # a new branch starts as a copy of the base, so that is what the files
        # would really be landing on top of.
        try:
            remote_paths = set(await client.list_tree(ref, branch))
            head_sha: str | None = await client.get_branch_sha(ref, branch)
        except GitHubError:
            remote_paths = set(await client.list_tree(ref, base))
            head_sha = await client.get_branch_sha(ref, base)

        for path, text in sorted(local.items()):
            size = len(text.encode("utf-8"))
            if path in remote_paths:
                changes.append(FileChange(path, "update", "exists on the branch", size))
            else:
                changes.append(FileChange(path, "add", "", size))

        logger.info(
            "github push planned repo=%s branch=%s add=%d update=%d skip=%d",
            ref.full_name,
            branch,
            sum(1 for c in changes if c.kind == "add"),
            sum(1 for c in changes if c.kind == "update"),
            sum(1 for c in changes if c.kind == "skip"),
        )
        return SyncPlan(
            direction="push",
            repository=ref.full_name,
            branch=branch,
            base_branch=base,
            changes=changes,
            head_sha=head_sha,
        )

    async def execute_push(
        self,
        workspace: Path,
        *,
        repository: str,
        branch: str,
        base_branch: str | None = None,
        commit_message: str,
        expected_head_sha: str | None = None,
    ) -> SyncResult:
        """Create the branch if needed, commit ``src/`` to it, optionally open a PR.

        CALLERS MUST HAVE SPENT AN APPROVAL TOKEN FIRST. This performs the
        write; the gate that authorises it lives in
        ``backend/services/external_actions.py`` and in the route
        (``docs/mcp.md`` 5).

        ``expected_head_sha`` is the sha the plan was computed against. The
        commit is parented on it, so GitHub refuses a non-fast-forward update
        if the branch moved — a conflict, never a silent overwrite.
        """
        client = self._client()
        ref = RepoRef.parse(repository)
        repo = await client.get_repository(ref)
        base = base_branch or repo.default_branch

        if not repo.permissions_push:
            raise GitHubError(
                f"The configured credential cannot push to {ref.full_name}."
            )

        local, _ = self._collect_workspace_files(workspace)
        if not local:
            raise GitHubError("There is nothing to push.")

        # Create the branch only when it is genuinely absent. An existing
        # branch is committed onto, never reset.
        try:
            parent = await client.get_branch_sha(ref, branch)
        except GitHubError:
            base_sha = await client.get_branch_sha(ref, base)
            await client.create_branch(ref, branch, base_sha)
            parent = base_sha

        if expected_head_sha and parent != expected_head_sha:
            raise GitHubError(
                "The branch has moved since this plan was prepared. Review the "
                "changes again before pushing."
            )

        commit_sha = await client.commit_files(
            ref,
            branch=branch,
            files=local,
            message=commit_message,
            parent_sha=parent,
        )

        return SyncResult(
            direction="push",
            repository=ref.full_name,
            branch=branch,
            files_written=len(local),
            commit_sha=commit_sha,
            detail=f"Committed {len(local)} file(s) to {ref.full_name}@{branch}.",
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
