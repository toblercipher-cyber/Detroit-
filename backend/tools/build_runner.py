"""Runs the allowlisted build toolchain against a generated project workspace.

WHY THIS FILE EXISTS
    This is where the builder executes code a model wrote. ``docs/security.md`` 3.2
    fixes the rules and this module is their single enforcement point: if a
    subprocess is ever spawned for a generated project, it is spawned here.

    Nothing about a command is derived from model output. The three commands are
    literals in this file; the only value that varies per run is the workspace
    directory, which the caller resolves.

THE SEQUENCE (docs/architecture.md 5.3)
    1. npm install --ignore-scripts      dependencies, lifecycle scripts off
    2. npx tsc --noEmit                  type check
    3. npx vite build                    static output to workspace/dist/

    Each step must succeed before the next runs. A type error is a build failure,
    not a warning to carry forward: shipping a preview of code that does not
    compile would make the Evaluation stage review something the user could never
    actually get.

EXIT CODES, NOT OUTPUT
    Success is decided by the process exit code and nothing else. Parsing stdout
    for the word "error" is how a failing build gets reported green -- and it is a
    mistake that has already been made once on this project, where a shell pipeline
    captured ``tail``'s status instead of the compiler's.

OUTPUT IS SANITIZED BEFORE IT LEAVES
    Toolchain output carries absolute paths, usernames and sometimes environment
    detail. ``docs/architecture.md`` 5.5: raw build output is logged server-side and
    never returned. The caller gets a short, redacted excerpt.

LAYER
    Tools (``backend/tools/``). No agent, no state model, no graph. It takes a
    directory and returns a result.

Spec: docs/architecture.md 5.3, 5.5; docs/security.md 3.2, 3.3.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class ToolchainUnavailableError(RuntimeError):
    """Node or npm could not be located.

    A configuration problem, not a problem with generated code. Kept distinct so
    the workflow never reports "your site failed to build" when the truth is that
    the machine has no Node installed.
    """


@lru_cache(maxsize=None)
def _resolve_launcher(name: str) -> tuple[str, ...]:
    """The argv prefix that runs ``npm`` or ``npx`` without a shell.

    On Windows both are ``.CMD`` shims, and ``CreateProcess`` cannot execute a
    ``.cmd``. The usual workaround is ``shell=True``, which ``docs/security.md``
    3.2 prohibits — and rightly: it reintroduces string parsing on a command line
    that includes a filesystem path.

    Instead the real entry point is used. npm ships ``npm-cli.js`` / ``npx-cli.js``
    beside ``node``, so ``node npm-cli.js install …`` is an ordinary argument list
    executing a real binary. On POSIX the resolved ``npm`` is already executable,
    so it is used directly.

    Cached because it is the same answer for the life of the process and resolving
    it touches the filesystem.
    """
    resolved = shutil.which(name)
    if resolved and not resolved.lower().endswith((".cmd", ".bat")):
        return (resolved,)

    node = shutil.which("node")
    if node is None:
        raise ToolchainUnavailableError("Node.js is not available on this machine.")

    cli = Path(node).parent / "node_modules" / "npm" / "bin" / f"{name}-cli.js"
    if cli.is_file():
        return (node, str(cli))

    if resolved is None:
        raise ToolchainUnavailableError(f"'{name}' is not available on this machine.")
    raise ToolchainUnavailableError(
        f"'{name}' was found only as a shell shim, which cannot be run safely."
    )

# The complete set of commands that may run against a generated workspace
# (docs/security.md 3.2). Argument lists, never strings: a list cannot be
# reinterpreted by a shell, so no quoting mistake can turn a path into an operator.
#
# Order is the build order and each step gates the next.
BUILD_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("install", ("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund")),
    ("typecheck", ("npx", "tsc", "--noEmit")),
    ("build", ("npx", "vite", "build")),
)

# Kept from the parent environment. Everything else is dropped, so no builder
# secret -- ANTHROPIC_API_KEY above all -- is inherited by a process running
# model-written code (docs/security.md 3.2).
#
# PATH is required to find node. SYSTEMROOT, COMSPEC and PATHEXT are required for
# subprocess creation to work at all on Windows; without them npm fails in ways
# that look like a build error rather than a configuration one.
_ENV_PASSTHROUGH: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
)

# npm writes its cache under the user profile by default; these keep it predictable
# and keep the subprocess from reaching for anything interactive.
_ENV_OVERRIDES: dict[str, str] = {
    "CI": "1",
    "NO_COLOR": "1",
    "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    "NPM_CONFIG_FUND": "false",
    "NPM_CONFIG_AUDIT": "false",
}

# Enough to diagnose a failure, not enough to be a data channel. Build output can
# be megabytes; only the tail matters because that is where the error is.
MAX_OUTPUT_CHARS = 4_000

_HOME_RE = re.compile(r"[A-Za-z]:[\\/](?:Users|home)[\\/][^\\/\s]+", re.IGNORECASE)
_POSIX_HOME_RE = re.compile(r"/(?:home|Users)/[^/\s]+")


def sanitize_output(text: str, workspace_dir: Path) -> str:
    """Redact filesystem detail and truncate to the tail.

    The workspace path becomes ``<workspace>`` and any home directory becomes
    ``<home>``. Both leak the machine's account name, and a build log is shown to
    a user who has no business seeing the server's layout (docs/security.md 6).

    The tail is kept rather than the head: compilers print progress first and the
    reason for failure last.
    """
    if not text:
        return ""

    cleaned = text.replace(str(workspace_dir), "<workspace>")
    cleaned = cleaned.replace(str(workspace_dir).replace("\\", "/"), "<workspace>")
    cleaned = _HOME_RE.sub("<home>", cleaned)
    cleaned = _POSIX_HOME_RE.sub("<home>", cleaned)

    if len(cleaned) > MAX_OUTPUT_CHARS:
        cleaned = "…truncated…\n" + cleaned[-MAX_OUTPUT_CHARS:]
    return cleaned.strip()


@dataclass(frozen=True)
class StepResult:
    """What one command did."""

    step: str
    exit_code: int
    timed_out: bool
    output: str  # already sanitized

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class BuildOutcome:
    """The result of running the sequence.

    ``failed_step`` names where it stopped, which is what the Fixer and the user
    both need: "typecheck" and "build" call for different responses.
    """

    ok: bool
    steps: tuple[StepResult, ...] = ()
    failed_step: str | None = None
    message: str = ""
    dist_dir: Path | None = None


def _subprocess_env() -> dict[str, str]:
    """A minimal environment: passthrough allowlist plus fixed overrides."""
    env = {
        name: os.environ[name] for name in _ENV_PASSTHROUGH if name in os.environ
    }
    env.update(_ENV_OVERRIDES)
    return env


class BuildRunner:
    """Executes the allowlisted toolchain in one project workspace."""

    def __init__(self, timeout_seconds: int) -> None:
        """
        Args:
            timeout_seconds: per step, not for the whole sequence. ``npm install``
                is legitimately slow while ``tsc`` is not, and a single budget for
                both would either strangle the first or excuse a hang in the second.
        """
        self._timeout = timeout_seconds

    def run(self, workspace_dir: Path) -> BuildOutcome:
        """Run install → typecheck → build, stopping at the first failure.

        Args:
            workspace_dir: the project workspace. Must already be scaffolded; this
                method does not create or repair it.

        Returns:
            A :class:`BuildOutcome` whose output is sanitized and safe to persist.
            Failures are returned, not raised: a build that does not compile is an
            ordinary result the workflow acts on (CLAUDE.md 26).
        """
        if not (workspace_dir / "package.json").is_file():
            return BuildOutcome(
                ok=False,
                failed_step="scaffold",
                message="The project workspace has not been set up.",
            )

        results: list[StepResult] = []
        for name, command in BUILD_STEPS:
            result = self._run_step(name, command, workspace_dir)
            results.append(result)
            if not result.ok:
                return BuildOutcome(
                    ok=False,
                    steps=tuple(results),
                    failed_step=name,
                    message=(
                        f"The {name} step timed out."
                        if result.timed_out
                        else f"The {name} step failed."
                    ),
                )

        dist = workspace_dir / "dist"
        if not dist.is_dir():
            # vite exited 0 but produced nothing. Treated as a failure rather than
            # trusted, because the next stage would serve an empty preview.
            return BuildOutcome(
                ok=False,
                steps=tuple(results),
                failed_step="build",
                message="The build reported success but produced no output.",
            )

        return BuildOutcome(ok=True, steps=tuple(results), dist_dir=dist)

    def _run_step(
        self, name: str, command: tuple[str, ...], workspace_dir: Path
    ) -> StepResult:
        """Run one allowlisted command. Never a shell, never model input."""
        logger.info("build step start step=%s dir=%s", name, workspace_dir.name)

        # The first element names the launcher; the rest are fixed literal flags.
        # Resolution never consults model output.
        try:
            argv = list(_resolve_launcher(command[0])) + list(command[1:])
        except ToolchainUnavailableError as exc:
            logger.error("toolchain unavailable step=%s: %s", name, exc)
            return StepResult(
                step=name, exit_code=-1, timed_out=False, output=str(exc)
            )

        try:
            completed = subprocess.run(
                argv,
                cwd=str(workspace_dir),
                env=_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                # Explicit, though it is the default: shell=True would let a path
                # be reinterpreted as a command (docs/security.md 3.2).
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("build step timed out step=%s limit=%ss", name, self._timeout)
            partial = (exc.stdout or "") + (exc.stderr or "")
            if isinstance(partial, bytes):  # text=True normally prevents this
                partial = partial.decode("utf-8", "replace")
            return StepResult(
                step=name,
                exit_code=-1,
                timed_out=True,
                output=sanitize_output(partial, workspace_dir),
            )
        except OSError as exc:
            # The toolchain is missing or not executable. A configuration problem,
            # not a problem with the generated code, and worth saying so.
            logger.error("build step could not start step=%s: %s", name, exc.strerror)
            return StepResult(
                step=name,
                exit_code=-1,
                timed_out=False,
                output=f"Could not start the {name} step.",
            )

        # Exit code decides. Output is for humans only.
        output = sanitize_output(
            (completed.stdout or "") + (completed.stderr or ""), workspace_dir
        )
        logger.info(
            "build step done step=%s exit=%d", name, completed.returncode
        )
        return StepResult(
            step=name,
            exit_code=completed.returncode,
            timed_out=False,
            output=output,
        )
