"""Temporary directories for the suites that clean themselves up.

WHY THIS EXISTS
    The suites created scratch directories with ``tempfile.mkdtemp`` and never
    removed them. Several then ran a real ``npm install`` inside, so each one
    cost roughly 71 MB. Across 43 call sites in 23 suites that accumulated
    **1,934 directories and 22.9 GB** between 2026-09-29 and 2026-10-09 —
    enough to fill a 120 GB disk, which it did. A disk-full write then
    truncated a suite file to zero bytes, and because an empty script exits 0,
    the runner reported it as passing while it tested nothing.

    So this is not housekeeping. An unbounded temp leak eventually corrupts
    files and produces green runs that prove nothing.

WHY A HELPER RATHER THAN ``TemporaryDirectory`` EVERYWHERE
    These suites are standalone scripts, not pytest functions. The directory is
    used from the point of creation to the end of the script, often across
    module-level statements, so wrapping each one in a ``with`` block would mean
    re-indenting and restructuring 23 files. That is a large diff over working
    tests for no behavioural gain, and the risk is exactly the kind of silent
    breakage this module exists to stop.

    Instead: same call signature, same return type, cleanup registered once.
    Each call site changes by one word.

WHEN CLEANUP RUNS
    At interpreter exit, via ``atexit`` — which covers a clean finish, a failed
    assertion, ``sys.exit`` and an unhandled exception, because CPython runs
    atexit handlers in all of those. It does NOT cover ``os._exit``, ``SIGKILL``
    or a hard crash; nothing in-process can.

    ``ignore_errors=True`` because a locked file on Windows must not turn
    cleanup into a second failure that masks the real one.

WHY THE ``_`` PREFIX
    ``test_suites.py`` collects ``suites/*.py`` except names starting with an
    underscore. This is a helper, not a suite.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile

# Every directory handed out, so cleanup does not depend on a caller
# remembering to register one.
_CREATED: list[str] = []


def _cleanup() -> None:
    """Remove every directory this module created."""
    while _CREATED:
        shutil.rmtree(_CREATED.pop(), ignore_errors=True)


atexit.register(_cleanup)


def mkdtemp(prefix: str | None = None, suffix: str | None = None) -> str:
    """A scratch directory that is removed when this process exits.

    Drop-in for ``tempfile.mkdtemp``: same arguments, same ``str`` return, so a
    call site changes only by which module it calls.
    """
    path = tempfile.mkdtemp(prefix=prefix, suffix=suffix)
    _CREATED.append(path)
    return path


def cleanup_now() -> None:
    """Remove the directories immediately instead of waiting for exit.

    For a suite that creates many in a loop and would otherwise hold them all
    until the end.
    """
    _cleanup()
