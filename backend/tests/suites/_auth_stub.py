"""Test-only caller identity, so route suites can exercise protected endpoints.

WHY THIS EXISTS
    Every project route now requires a verified Supabase access token. Minting
    a genuine one needs that project's private signing key, which nobody has,
    so suites that test HITL, preview serving or reference uploads would all
    stop at 401 and test nothing.

    This overrides ONLY the identity dependency. Token verification has its own
    coverage; everything downstream of "who is calling" -- the scoped service,
    the repository, the policies -- stays production code.

WHY THE `_` PREFIX
    ``test_suites.py`` collects ``suites/*.py`` except names starting with an
    underscore. This is a helper, not a suite.
"""

from __future__ import annotations

from uuid import UUID

from backend.api.deps import get_caller
from backend.auth.jwt import CallerIdentity

# A fixed id. These suites are single-user by nature; cross-user isolation is
# isolation_check's job, and it uses real distinct users.
TEST_USER_ID = UUID("00000000-0000-4000-8000-00000000d37a")


def authenticate(app) -> None:
    """Make every protected route on ``app`` see one fixed caller."""
    app.dependency_overrides[get_caller] = lambda: CallerIdentity(
        user_id=TEST_USER_ID, email="suite@test.invalid"
    )
