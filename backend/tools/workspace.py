"""Workspace file access: the boundary that decides what a model may write.

WHY THIS FILE EXISTS
    Everything up to now has been reasoning. This is the first place the system
    *acts* on the filesystem on a model's behalf, and it is the only place that
    should. ``docs/architecture.md`` 5.2 calls the template constraint "the primary
    isolation mechanism", and a constraint stated in a prompt is a request; a
    constraint enforced here is a guarantee.

    The rules it enforces, verbatim from that section:

      - writes are confined to ``src/`` inside the project workspace
      - ``package.json``, ``vite.config.ts``, ``index.html`` and ``tsconfig*.json``
        are builder-owned and never model-writable
      - nothing outside the workspace is reachable at all

    The second rule is what keeps a build safe. Building a project executes its own
    config and plugins, so freezing the config and the dependency set removes the
    model's ability to introduce build-time code. Let a model edit
    ``vite.config.ts`` and every other protection here becomes decoration.

LAYER
    Tools (``backend/tools/``). Depends on the standard library and LangChain only.
    It imports nothing from agents, graph, context or services — a tool that reached
    back into the workflow could be steered by the thing it is meant to contain.

TWO CHECKS, NOT ONE
    Every path is rejected on shape first (absolute, drive-lettered, traversing) and
    then resolved and confirmed to sit inside the workspace. The second check is the
    real guarantee; the first exists so the common case fails with a message that
    says what was wrong. Neither is trusted alone, because a containment guarantee
    should not rest on one predicate.

WHAT IT DELIBERATELY DOES NOT DO
    It does not run anything. Executing the build is a separate, separately
    constrained concern (``docs/architecture.md`` 5.3) and belongs in its own tool, so
    that "may write a file" and "may run a command" are never the same permission.

Spec: docs/architecture.md 5.1-5.2; docs/security.md 3.1-3.4; docs/agents.md 4.6, 4.11.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# The only writable subtree. docs/architecture.md 5.2 grants "workspace/src/ plus a
# small allowlist of asset paths" but never enumerates the asset paths, so none are
# invented here: assets belong under src/assets/, which this already covers. Widening
# it is a decision, not an implementation detail.
WRITABLE_PREFIXES: tuple[str, ...] = ("src/",)

# Builder-owned template files. Redundant while only src/ is writable — kept because
# the cost of the check is nothing and the cost of forgetting it, if the writable set
# ever widens, is a model that can edit the build.
PROTECTED_NAMES: frozenset[str] = frozenset(
    {"package.json", "vite.config.ts", "index.html"}
)
PROTECTED_PREFIX = "tsconfig"
PROTECTED_SUFFIX = ".json"


class WorkspaceAccessError(RuntimeError):
    """A path was refused. Messages name the offending path but never the root.

    Callers surface this as a failed stage, not as a retry: a model asking for a path
    it is not allowed is not a transient condition.
    """


class WriteFileArgs(BaseModel):
    """Arguments for the write tool, as the model sees them."""

    path: str = Field(
        description="Workspace-relative path under src/, using forward slashes."
    )
    content: str = Field(description="Full new contents of the file.")


class ReadFileArgs(BaseModel):
    """Arguments for the read tool, as the model sees them."""

    path: str = Field(description="Workspace-relative path, using forward slashes.")


class WorkspaceFiles:
    """Read and write access to one project workspace, and nothing else.

    Constructed per project. The root is supplied by the caller and never derived
    from anything a model produced.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.expanduser().resolve()

    # --- Containment -----------------------------------------------------

    def _reject_shape(self, path: str) -> None:
        """Fail fast on paths that cannot be workspace-relative."""
        if not path or not path.strip():
            raise WorkspaceAccessError("A path is required.")
        if path.startswith(("/", "\\")):
            raise WorkspaceAccessError(f"Path must be relative: {path!r}")
        if len(path) > 1 and path[1] == ":":
            raise WorkspaceAccessError(f"Path must not name a drive: {path!r}")
        parts = PurePosixPath(path.replace("\\", "/")).parts
        if ".." in parts:
            raise WorkspaceAccessError(f"Path must not traverse upwards: {path!r}")

    def _resolve(self, path: str) -> Path:
        """Resolve a relative path and confirm it lands inside the workspace."""
        self._reject_shape(path)
        candidate = (self._root / path.replace("\\", "/")).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            logger.warning("refused a path resolving outside the workspace")
            raise WorkspaceAccessError(f"Path is outside the workspace: {path!r}")
        return candidate

    def _reject_unwritable(self, path: str) -> None:
        """Apply the template constraint to a write target."""
        normalised = path.replace("\\", "/")
        if not normalised.startswith(WRITABLE_PREFIXES):
            raise WorkspaceAccessError(
                f"Writes are confined to {', '.join(WRITABLE_PREFIXES)} — "
                f"refused: {path!r}"
            )
        name = PurePosixPath(normalised).name
        is_tsconfig = name.startswith(PROTECTED_PREFIX) and name.endswith(
            PROTECTED_SUFFIX
        )
        if name in PROTECTED_NAMES or is_tsconfig:
            raise WorkspaceAccessError(
                f"{name} is a builder-owned template file and is not writable."
            )

    # --- Operations ------------------------------------------------------

    def read_file(self, path: str) -> str:
        """Read a text file from anywhere inside the workspace.

        Reading is allowed across the whole workspace while writing is not: seeing
        the template's config is useful context, and being able to change it is the
        risk.
        """
        target = self._resolve(path)
        if not target.is_file():
            raise WorkspaceAccessError(f"No such file in the workspace: {path!r}")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise WorkspaceAccessError(f"Not a text file: {path!r}") from None
        except OSError as exc:
            raise WorkspaceAccessError(
                f"Could not read {path!r} (reason: {exc.strerror})."
            ) from None

    def write_file(self, path: str, content: str) -> str:
        """Write a text file, subject to the template constraint.

        Returns the workspace-relative path written, so a caller can record it
        without re-deriving it from whatever the model said.
        """
        self._reject_unwritable(path)
        target = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceAccessError(
                f"Could not write {path!r} (reason: {exc.strerror})."
            ) from None
        relative = target.relative_to(self._root).as_posix()
        logger.info("workspace write path=%s bytes=%d", relative, len(content))
        return relative

    def exists(self, path: str) -> bool:
        """True when the path exists inside the workspace."""
        try:
            return self._resolve(path).exists()
        except WorkspaceAccessError:
            return False


def build_workspace_tools(workspace: WorkspaceFiles) -> list[StructuredTool]:
    """Expose a workspace as LangChain tools bound to that one project.

    The workspace is captured in the closure, so a model supplies only a path — it
    never names, and cannot influence, the root it is writing under.

    Enforcement lives in :class:`WorkspaceFiles`, not here. These wrappers exist so a
    tool-calling agent can reach the same guarded operations a node can call
    directly; if the wiring changes, the guarantees do not move.
    """

    def _write(path: str, content: str) -> str:
        written = workspace.write_file(path, content)
        return f"Wrote {written}"

    def _read(path: str) -> str:
        return workspace.read_file(path)

    return [
        StructuredTool.from_function(
            func=_write,
            name="write_workspace_file",
            description=(
                "Write a file in the project workspace. Only paths under src/ are "
                "permitted; build configuration files cannot be written."
            ),
            args_schema=WriteFileArgs,
        ),
        StructuredTool.from_function(
            func=_read,
            name="read_workspace_file",
            description="Read a text file from the project workspace.",
            args_schema=ReadFileArgs,
        ),
    ]
