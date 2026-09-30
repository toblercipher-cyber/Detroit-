"""Shared test configuration.

WHY THIS FILE EXISTS
    Two things every test run needs before anything imports ``backend``:

    1. The repository root on ``sys.path``. The project has no ``__init__.py``
       files and is imported as a namespace package from the root, so pytest
       invoked from anywhere else would fail on ``import backend``.
    2. A placeholder ``ANTHROPIC_API_KEY``. ``backend.config.settings`` fails fast
       when it is absent, by design. Tests never call the provider — the value only
       lets the composition root be constructed.

    It deliberately does NOT provide fixtures. The suites build their own objects
    explicitly, which is what makes each one readable on its own.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Placeholder only. No test performs a provider call, and none reads .env.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
