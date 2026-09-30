"""Locates the Impeccable CLI and reports whether the quality gate can run.

WHY THIS FILE EXISTS
    ``CLAUDE.md`` 15 and ``docs/design.md`` 5.1 make Impeccable a **mandatory,
    blocking** quality gate with no substitute. That ruling is only worth
    anything if something actually checks, so this is the check.

A CORRECTION THIS FILE ENCODES
    The first version of this probe tested for a ``.impeccable/`` directory in
    the repository, because ``docs/design.md`` 5.2 said its absence meant
    Impeccable was not installed. That was wrong, and it mattered: ``.impeccable/``
    is **optional per-project detector config** (ignore rules, ignored files and
    values, design-system toggle), and a project can use the detector perfectly
    well without one. The probe would have reported the gate blocked forever on
    a machine where the tool works.

    Availability is therefore a property of the **CLI**, not of the repository.

WHAT COUNTS AS AVAILABLE
    A runnable executable that answers ``--version``. Not a path that exists,
    not a directory, not a launcher script — something that ran and replied.
    Anything less is a guess, and a gate satisfied by a guess is not a gate.

THE VERSION IS WHAT THE TOOL SAYS
    ``docs/design.md`` 5.3 requires the actual installed version. It is taken
    from the process output, never from a filename, a VERSION file or the
    skill's frontmatter — those three currently disagree with each other
    (4.3.1, 0.1.5, 4.0.0), which is exactly why only the running binary counts.

NO SHELL, EVER
    The Windows launcher is ``impeccable.cmd``, and ``CreateProcess`` cannot run
    a ``.cmd``. The tempting fix is ``shell=True``; ``docs/security.md`` 3.2
    prohibits it, and this module resolves the real ``impeccable.exe`` that
    ships beside the launcher instead — the same approach ``build_runner`` takes
    for npm.

LAYER
    Tools (``backend/tools/``). Filesystem and one short subprocess; no agent,
    no state, no graph.

Spec: CLAUDE.md 15; docs/design.md 5.1, 5.2, 5.3; docs/security.md 3.2.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.schemas.enums import AgentRole, Severity
from backend.schemas.state import EvaluationFinding

logger = logging.getLogger(__name__)

# An explicit override always wins. The launcher honours the same variable, so
# pointing both at one binary keeps the CLI and this probe in agreement.
ENV_BINARY = "IMPECCABLE_BIN"
ENV_SKILL_DIR = "IMPECCABLE_SKILL_DIR"

# Where the skill installs by default.
_DEFAULT_SKILL_DIR = Path.home() / ".claude" / "skills" / "impeccable"

# Answering --version must be near-instant. A longer budget here would turn a
# hung binary into a hung evaluation stage.
VERSION_TIMEOUT_SECONDS = 30


class DetectorError(RuntimeError):
    """The detector could not complete a scan.

    Distinct from "scanned and found nothing": exit 1 means a target could not
    be read, and treating that as a clean result is how a gate passes without
    having looked at anything.
    """


@dataclass(frozen=True)
class ImpeccableStatus:
    """What the probe found. Frozen: a probe result is a fact, not a setting."""

    available: bool
    version: str | None = None
    reason: str = ""
    executable: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.available


def _platform_binary_names() -> tuple[str, ...]:
    """Candidate binary paths under a skill directory, most specific first."""
    machine = platform.machine().lower()
    if os.name == "nt":
        arch = "arm64" if "arm" in machine else "x64"
        return (
            f"scripts/bin/windows-{arch}/impeccable.exe",
            "scripts/bin/windows-x64/impeccable.exe",
        )
    system = "darwin" if platform.system() == "Darwin" else "linux"
    arch = "arm64" if ("arm" in machine or "aarch64" in machine) else "x64"
    return (
        f"scripts/bin/{system}-{arch}/impeccable",
        f"scripts/bin/{system}-x64/impeccable",
    )


def find_impeccable() -> Path | None:
    """The Impeccable executable, or ``None``.

    Order: an explicit ``IMPECCABLE_BIN``; the binary shipped inside the skill
    directory; then ``PATH``.

    Launcher scripts are deliberately skipped on Windows. A ``.cmd`` cannot be
    executed without a shell, and the real executable sits beside it.
    """
    override = os.environ.get(ENV_BINARY)
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    skill_dir = Path(os.environ.get(ENV_SKILL_DIR) or _DEFAULT_SKILL_DIR)
    for relative in _platform_binary_names():
        candidate = skill_dir / relative
        if candidate.is_file():
            return candidate

    found = shutil.which("impeccable")
    if found and not found.lower().endswith((".cmd", ".bat")):
        return Path(found)
    return None


def probe_impeccable(repo_root: Path | None = None) -> ImpeccableStatus:
    """Report whether the Impeccable detector can be run, and at what version.

    Args:
        repo_root: accepted and unused. Availability is a property of the
            installed CLI, not of any repository — see the correction in this
            module's docstring. The parameter is kept so existing callers and
            the injected probe signature do not have to change in the same
            step as the fix.

    Returns:
        An :class:`ImpeccableStatus`. ``available`` is ``True`` only when a
        binary was found *and* answered ``--version``.
    """
    executable = find_impeccable()
    if executable is None:
        return ImpeccableStatus(
            available=False,
            reason="Impeccable is not installed: no CLI could be located.",
        )

    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("impeccable found but not runnable: %s", type(exc).__name__)
        return ImpeccableStatus(
            available=False,
            reason="Impeccable was found but could not be run.",
            executable=str(executable),
        )

    if completed.returncode != 0:
        return ImpeccableStatus(
            available=False,
            reason="Impeccable was found but did not report a version.",
            executable=str(executable),
        )

    version = (completed.stdout or "").strip() or None
    logger.info("impeccable available version=%s", version or "unknown")
    return ImpeccableStatus(
        available=True, version=version, executable=str(executable)
    )


# Exit codes, from `impeccable detect --help`:
#   0  scan completed, no primary findings (advisories may still be listed)
#   1  at least one requested target could not be scanned
#   2  scan completed with primary findings
EXIT_CLEAN = 0
EXIT_SCAN_FAILED = 1
EXIT_FINDINGS = 2

# A scan walks a built site. Generous, but bounded: a hung detector would
# otherwise hang the evaluation stage, and a stage that never returns is worse
# than one that blocks.
DETECT_TIMEOUT_SECONDS = 300

# Keys the mapper will accept for each field. Several are listed because the
# finding shape has NOT been observed against real output -- see the warning in
# :func:`run_detector`. Unknown shapes degrade to a usable finding rather than
# raising, because losing a real finding is worse than an imprecise label.
_ID_KEYS = ("id", "ruleId", "rule", "code")
_SUMMARY_KEYS = ("message", "summary", "title", "description", "text")
_PATH_KEYS = ("file", "path", "filePath", "location")
_SEVERITY_KEYS = ("severity", "level")

_SEVERITY_MAP: dict[str, Severity] = {
    "blocker": Severity.BLOCKER,
    "critical": Severity.BLOCKER,
    "error": Severity.MAJOR,
    "major": Severity.MAJOR,
    "warning": Severity.MINOR,
    "warn": Severity.MINOR,
    "minor": Severity.MINOR,
    "advisory": Severity.INFO,
    "info": Severity.INFO,
    "notice": Severity.INFO,
}


def _first(entry: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _to_finding(entry: dict, index: int) -> EvaluationFinding:
    """Map one detector entry onto the project's finding model.

    Deliberately forgiving about field names and strict about the result: every
    finding gets an id, a severity and a summary, because a finding the Fixer
    cannot act on is not worth surfacing.

    Advisory entries become ``INFO``. ``impeccable detect --help`` is explicit
    that advisories never count as failures and never change the exit code, so
    they must not be able to fail the gate either.
    """
    if entry.get("advisory") is True:
        severity = Severity.INFO
    else:
        raw = (_first(entry, _SEVERITY_KEYS) or "").lower()
        # An unrecognised severity becomes MAJOR, not INFO: a finding whose
        # importance is unknown should be looked at, not quietly discounted.
        severity = _SEVERITY_MAP.get(raw, Severity.MAJOR)

    rule = _first(entry, _ID_KEYS) or "finding"
    summary = _first(entry, _SUMMARY_KEYS) or f"Impeccable reported '{rule}'."
    location = _first(entry, _PATH_KEYS)

    return EvaluationFinding(
        finding_id=f"impeccable-{index}-{rule}"[:120],
        source=AgentRole.ANTI_SLOP_REVIEWER,
        category="anti-slop",
        severity=severity,
        summary=summary if location is None else f"{summary} ({location})",
    )


def run_detector(
    target: Path, *, timeout_seconds: int = DETECT_TIMEOUT_SECONDS
) -> list[EvaluationFinding]:
    """Scan ``target`` with ``impeccable detect --json`` and return its findings.

    Args:
        target: a file or directory to scan — for the Evaluation stage, the
            project's built ``dist/``.
        timeout_seconds: bound on the scan.

    Returns:
        The findings, possibly empty. Empty means "scanned, found nothing",
        which is a real result.

    Raises:
        DetectorError: the CLI is missing, the scan failed (exit 1), the output
            was not JSON, or it timed out. Every one of these must reach the
            caller, because the Evaluation node turns them into a BLOCKED gate
            rather than a pass (CLAUDE.md 15).

    NOTE ON FIELD MAPPING
        The exit codes and the ``--json`` stdout contract are documented by the
        CLI and were verified against it (an empty scan returns ``[]``, exit 0).
        The **field names inside a finding** were not: every target available
        here scans clean, so no real finding has been observed. :func:`_to_finding`
        therefore accepts several plausible key names and degrades rather than
        failing. The first time real findings appear, that mapping should be
        checked against them.
    """
    executable = find_impeccable()
    if executable is None:
        raise DetectorError("Impeccable is not available.")

    # Argument list, no shell, fixed flags. Nothing here comes from a model;
    # `target` is a path the builder resolved (docs/security.md 3.2).
    argv = [str(executable), "detect", "--json", str(target)]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=timeout_seconds, shell=False, check=False,
        )
    except subprocess.TimeoutExpired:
        raise DetectorError("The Impeccable scan timed out.") from None
    except OSError:
        raise DetectorError("The Impeccable scan could not be started.") from None

    if completed.returncode == EXIT_SCAN_FAILED:
        # "Could not read a target" is not "nothing is wrong".
        logger.warning("impeccable could not scan every target")
        raise DetectorError("Impeccable could not scan the built output.")

    if completed.returncode not in (EXIT_CLEAN, EXIT_FINDINGS):
        raise DetectorError(
            f"Impeccable exited unexpectedly ({completed.returncode})."
        )

    raw = (completed.stdout or "").strip()
    if not raw:
        return []

    try:
        document = json.loads(raw)
    except ValueError:
        # Output that is not JSON means the contract changed. Blocking is right:
        # silently returning [] would report a gate as clean on unparsed output.
        raise DetectorError("Impeccable returned output that could not be read.") from None

    entries = document if isinstance(document, list) else document.get("findings", [])
    findings = [
        _to_finding(entry, index)
        for index, entry in enumerate(entries)
        if isinstance(entry, dict)
    ]
    logger.info(
        "impeccable scan complete exit=%d findings=%d",
        completed.returncode,
        len(findings),
    )
    return findings
