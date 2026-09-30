"""Probes whether Impeccable is installed, and reports the truth either way.

WHY THIS FILE EXISTS
    ``CLAUDE.md`` 15 and ``docs/design.md`` 5.1 make Impeccable a **mandatory,
    blocking** quality gate with no substitute. That ruling is only worth
    anything if something actually checks, so this is the check.

    It answers one question — is Impeccable present, and at what version — and
    it answers it from the filesystem every time. Nothing here infers, caches a
    previous answer, or falls back to a default. A gate that can be satisfied by
    an assumption is not a gate.

THE FAILURE MODE THIS GUARDS AGAINST
    The tempting bug is to treat "tool missing" as "nothing to report" and let a
    clean-looking evaluation through. ``docs/design.md`` 5.1 names that exactly:
    reporting an uncompleted check as completed is a correctness failure, not a
    shortcut. So the unavailable answer is a first-class result carrying a
    reason, not an exception to be swallowed and not an empty finding list.

VERSION IS READ, NEVER ASSUMED
    ``docs/design.md`` 5.3 requires ``impeccable_version`` to be the actual
    installed version. When the directory exists but no version can be read,
    this returns ``None`` rather than a guess. A wrong version in an audit trail
    is worse than an absent one.

WHAT THIS FILE DELIBERATELY DOES NOT DO
    It does not run the detector. Impeccable is not installed in this
    repository, so its command-line interface, output shape and exit codes have
    not been observed. Writing an invocation against an imagined interface would
    be the same class of mistake this module exists to prevent — code that looks
    complete and is not. The runner is added once the tool is present and its
    real interface can be read.

LAYER
    Tools (``backend/tools/``). Filesystem only; no agent, no state, no graph.

Spec: CLAUDE.md 15; docs/design.md 5.1, 5.2, 5.3; docs/workflow.md 1.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Impeccable installs into the repository root (see .gitignore, which already
# excludes .impeccable/mocks and .impeccable/config.local.json).
IMPECCABLE_DIR = ".impeccable"

# Where a version might legitimately be recorded. Tried in order; the first
# readable one wins. Kept as a small list rather than one hard-coded path
# because the installed layout has not been observed yet — and an unreadable
# version yields None, never a placeholder.
_VERSION_FILES: tuple[str, ...] = (
    "config.json",
    "impeccable.json",
    "version.json",
)
_VERSION_KEYS: tuple[str, ...] = ("version", "impeccableVersion", "toolVersion")


@dataclass(frozen=True)
class ImpeccableStatus:
    """What the probe found. Frozen: a probe result is a fact, not a setting."""

    available: bool
    version: str | None = None
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.available


def _read_version(root: Path) -> str | None:
    """The recorded version, or ``None`` when none can be read.

    Every failure path returns ``None``: a missing file, malformed JSON, an
    unexpected shape, or an unreadable file all mean "not known". None of them
    mean "assume something".
    """
    for name in _VERSION_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("impeccable %s is present but unreadable", name)
            continue
        if not isinstance(document, dict):
            continue
        for key in _VERSION_KEYS:
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def probe_impeccable(repo_root: Path) -> ImpeccableStatus:
    """Report whether Impeccable is installed under ``repo_root``.

    Args:
        repo_root: the repository root to look in. Passed rather than derived,
            so a test can point it anywhere and the answer still comes from a
            real directory.

    Returns:
        An :class:`ImpeccableStatus`. ``available`` is ``False`` with a reason
        when the tool is absent; it is never ``True`` on inference.
    """
    directory = repo_root / IMPECCABLE_DIR

    if not directory.is_dir():
        return ImpeccableStatus(
            available=False,
            reason="Impeccable is not installed: no .impeccable directory.",
        )

    version = _read_version(directory)
    logger.info("impeccable detected version=%s", version or "unknown")
    return ImpeccableStatus(available=True, version=version)
