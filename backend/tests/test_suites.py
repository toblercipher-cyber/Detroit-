"""Pytest driver for the verification suites in ``backend/tests/suites``.

WHY THIS FILE EXISTS
    The suites are standalone scripts: each one sets up real objects, drives a real
    code path with stub models, and prints ``PASS`` or ``**FAIL**`` per assertion.
    That shape is deliberate — a suite doubles as readable evidence of what a module
    actually does, and it can be run on its own while developing that module.

    Pytest cannot collect scripts, so this file turns each one into a test case.

WHY NOT JUST NAME THE SUITES ``test_*.py``
    Because pytest would then IMPORT them at collection time. They have no test
    functions, so a suite that printed ``**FAIL**`` would contribute nothing to
    collect, and the run would exit GREEN with the failure on screen and nobody
    reading it.

    That is not hypothetical: an earlier harness on this project counted ``**FAIL**``
    strings in output and therefore reported "0 failures" for scripts that had
    crashed before printing anything. Four suites were silently broken for several
    sessions. This driver checks BOTH signals so neither failure mode can hide:

        - a non-zero exit code   -> the script crashed
        - a ``**FAIL**`` marker  -> an assertion inside it failed

    A suite that crashes on line 1 fails here. A suite that runs to completion with
    one bad assertion also fails here.

RUNNING
    From the repository root::

        .venv/Scripts/python.exe -m pytest backend/tests -q

    One test case per suite, named after the suite, so a failure names the module.

NO NETWORK
    Every suite stubs the chat model. None makes a provider call and none requires
    ``.env``; the dummy key below only lets the composition root build.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SUITES_DIR = Path(__file__).parent / "suites"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Generous: the slowest suites build a full LangGraph workflow and drive several
# resume cycles. Low enough that a hang fails the run instead of stalling CI.
TIMEOUT_SECONDS = 180

FAIL_MARKER = "**FAIL**"


def _suite_paths() -> list[Path]:
    return sorted(p for p in SUITES_DIR.glob("*.py") if not p.name.startswith("_"))


def _suite_ids() -> list[str]:
    return [p.stem for p in _suite_paths()]


def test_suites_are_discovered() -> None:
    """Guard against the driver silently finding nothing.

    Without this, deleting or renaming the suites directory would produce a green
    run with zero tests — the same false-green this driver exists to prevent.
    """
    assert SUITES_DIR.is_dir(), f"missing suites directory: {SUITES_DIR}"
    found = _suite_paths()
    assert len(found) >= 37, f"expected at least 37 suites, found {len(found)}"


@pytest.mark.parametrize("suite", _suite_paths(), ids=_suite_ids())
def test_suite(suite: Path) -> None:
    """Run one suite and require a clean exit AND no failed assertion."""
    env = {
        **os.environ,
        # Suites import ``backend.*`` and are run from the repository root.
        "PYTHONPATH": str(REPO_ROOT),
        # Never write .pyc into the tree during a test run.
        "PYTHONDONTWRITEBYTECODE": "1",
        # The composition root reads this at import. No call is ever made with it.
        "ANTHROPIC_API_KEY": os.environ.get(
            "ANTHROPIC_API_KEY", "test-only-not-a-real-key"
        ),
        # Signs preview URLs. The backend REFUSES TO START without it whenever
        # DATABASE_URL_APP is set (backend/main.py `_preview_secret`), so any
        # suite that builds the real app needs one. Supplied here so that is
        # true for every suite at once rather than remembered per file; suites
        # also setdefault their own, for when they are run directly.
        "PREVIEW_URL_SECRET": os.environ.get(
            "PREVIEW_URL_SECRET", "test-only-preview-signing-key"
        ),
    }

    try:
        result = subprocess.run(
            [sys.executable, str(suite)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{suite.name} exceeded {TIMEOUT_SECONDS}s")

    output = result.stdout + result.stderr
    failures = [
        line.strip() for line in output.splitlines() if FAIL_MARKER in line
    ]

    if result.returncode != 0:
        pytest.fail(
            f"{suite.name} exited {result.returncode}\n"
            f"--- output (tail) ---\n" + "\n".join(output.splitlines()[-30:])
        )

    if failures:
        pytest.fail(
            f"{suite.name}: {len(failures)} failed assertion(s)\n"
            + "\n".join(failures)
        )
