"""Short-lived signed capability tokens for the static preview route.

WHY THIS FILE EXISTS
    ``/preview/{project_id}/...`` served generated files to ANYONE who knew a
    project id -- no credentials, no owner check. Verified, not assumed: a
    probe fetched user A's built ``index.html`` as user B and with no
    credentials at all, and got 200 with A's content in both cases.

    The typed API cannot simply be reused there. A browser loading a page in a
    tab or an iframe sends no ``Authorization`` header, and an iframe cannot be
    made to. So the capability has to travel in the URL, which means it must be
    unforgeable, short-lived, and bound to exactly one project.

WHY THE TOKEN SITS IN THE PATH, NOT IN A QUERY PARAMETER
    ``index.html`` is never one request. The browser then fetches
    ``./assets/index-abc.js``, the CSS, the fonts -- each a separate GET. A
    query parameter does not propagate to a relative URL, so the HTML would
    load and every sub-resource would 404: a blank preview.

    The builder's frozen ``vite.config.ts`` already sets ``base: "./"``
    ("Relative so the static output can be served from a per-project path").
    With the token as a path segment, every relative sub-resource inherits it
    for free:

        GET /preview/<token>/index.html
              -> html references ./assets/app.js
        GET /preview/<token>/assets/app.js      <- token carried automatically

    No cookie, no HTML rewriting. A cookie would otherwise need
    ``SameSite=None; Secure`` to survive a cross-origin iframe, which is
    painful over plain http on localhost and widens what the browser attaches
    elsewhere.

    A side benefit: the project id no longer appears in the URL at all.

WHAT THE TOKEN AUTHORIZES -- A SUBTREE, NOT ONE FILE
    Stated plainly because it is the one place this design is looser than
    "bound to the requested path": the token authorizes one project's ``dist/``
    subtree for its lifetime, not a single file. Binding to a single file is
    incompatible with a working HTML page, per the paragraph above.

    Containment within that subtree is enforced SEPARATELY and on every
    request by ``resolve_preview_file`` in ``backend/api/preview.py``, which
    already refuses traversal, absolute paths and unlisted extensions. The
    token decides WHICH project; that function decides WHICH file. Neither
    control substitutes for the other.

WHY PyJWT AND NOT HAND-ROLLED HMAC
    PyJWT is already a dependency -- ``backend/auth/jwt.py`` verifies Supabase
    access tokens with it. Reusing it means no new library and, more
    importantly, no hand-written expiry parsing or signature comparison, which
    is where this kind of code usually goes wrong.

    ``algorithms=["HS256"]`` is PINNED on decode. An unpinned ``jwt.decode`` is
    how algorithm-confusion attacks land: ``alg: none`` tokens, or a token
    re-signed with an asymmetric public key used as an HMAC secret. The
    Supabase verifier pins its algorithms for the same reason.

THIS IS NOT AN ACCESS-CONTROL DECISION ON ITS OWN
    A valid token proves only that this server issued it for that project and
    that it has not expired yet. The route MUST still confirm the project
    exists and is still owned by ``sub`` before serving a byte -- otherwise a
    token outlives a deletion or an ownership change for its whole TTL.

LAYER
    Auth (``backend/auth/``). Depends on settings only. It performs no I/O,
    touches no database and imports nothing from ``api/`` or ``services/``.

Spec: docs/api.md 2.5; docs/security.md 2, 3.4, 9; docs/database.md 1.3.5.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass

import jwt

logger = logging.getLogger(__name__)

# Symmetric: this server is both issuer and verifier, so there is no second
# party that needs a public key. Pinned on decode, never read from the token.
ALGORITHM = "HS256"

# The scope claim. A preview token must not be usable anywhere another
# HS256 token of ours might one day be accepted, so the purpose is signed in
# rather than inferred from where the token happened to arrive.
SCOPE = "preview"

# 5 minutes: long enough to load a page and its assets, short enough that a URL
# leaked through a screenshot, a shared link or a log line is dead on arrival.
# A longer review session re-fetches, which the API makes cheap.
DEFAULT_TTL_SECONDS = 300

# Small tolerance for clock skew between issue and verify. The same leeway the
# Supabase verifier allows.
LEEWAY_SECONDS = 10

_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "pid", "sub", "scope", "jti"]


class PreviewTokenError(Exception):
    """A token was absent, malformed, expired, or not ours.

    One exception for every failure on purpose. The route turns all of them
    into an identical 404, so nothing here should encourage a caller to tell a
    client which part failed -- that only helps someone probing.
    """


@dataclass(frozen=True)
class PreviewGrant:
    """What a verified token asserts. Claims, not permissions.

    ``project_id`` and ``owner_id`` are what the route re-checks against
    current state before serving anything.
    """

    project_id: str
    owner_id: str
    jti: str
    expires_at: int


def generate_dev_secret() -> str:
    """A process-local signing key for single-user development.

    Only ever used when no application database is configured. Tokens signed
    with it stop verifying when the process restarts, which is correct: a key
    nobody configured should not outlive the process that invented it, and in a
    multi-worker or multi-instance deployment each worker would otherwise
    reject the others' tokens. ``main.py`` refuses this path in multi-user mode.
    """
    return secrets.token_urlsafe(48)


def issue(
    *,
    project_id: str,
    owner_id: str,
    secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, int]:
    """Mint a token for one project's preview subtree.

    The CALLER is responsible for having verified that ``owner_id`` owns
    ``project_id``; this function signs what it is given and checks no
    authorization of its own. In practice that check is the owner-scoped
    service in ``backend/api/projects.py``, which cannot reach another user's
    project at all.

    Args:
        project_id: the project whose ``dist/`` the token authorizes.
        owner_id: the verified caller, recorded so the route can re-check it.
        secret: the signing key. Never logged, never returned.
        ttl_seconds: lifetime; defaults to five minutes.

    Returns:
        ``(token, expires_at_unix_seconds)``.
    """
    now = int(time.time())
    expires_at = now + ttl_seconds
    payload = {
        "pid": project_id,
        "sub": owner_id,
        "scope": SCOPE,
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        # Unique per issue. Gives the logs something to correlate on that is
        # not the token itself.
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, secret, algorithm=ALGORITHM)
    return token, expires_at


def verify(token: str, *, secret: str) -> PreviewGrant:
    """Decode and validate a preview token, or raise.

    Every claim that matters is required rather than optional: a token missing
    ``exp`` would otherwise verify and never expire, and one missing ``scope``
    could be a token minted for some other purpose.

    Raises:
        PreviewTokenError: on any failure whatsoever. The caller answers 404.
    """
    if not token:
        raise PreviewTokenError("No preview token was provided.")

    try:
        claims = jwt.decode(
            token,
            secret,
            # PINNED. Never `algorithms=jwt.algorithms.get_default_algorithms()`
            # and never read from the token header.
            algorithms=[ALGORITHM],
            leeway=LEEWAY_SECONDS,
            options={
                "require": _REQUIRED_CLAIMS,
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
            },
        )
    except jwt.ExpiredSignatureError:
        raise PreviewTokenError("The preview token has expired.") from None
    except jwt.MissingRequiredClaimError as exc:
        raise PreviewTokenError(f"The preview token is missing {exc.claim}.") from None
    except jwt.InvalidTokenError:
        # Covers a bad signature, a tampered payload, `alg: none`, and anything
        # that is not a JWT at all.
        raise PreviewTokenError("The preview token is not valid.") from None

    if claims.get("scope") != SCOPE:
        raise PreviewTokenError("The token was not issued for preview access.")

    project_id = claims.get("pid")
    owner_id = claims.get("sub")
    if not isinstance(project_id, str) or not project_id:
        raise PreviewTokenError("The preview token names no project.")
    if not isinstance(owner_id, str) or not owner_id:
        raise PreviewTokenError("The preview token names no owner.")

    return PreviewGrant(
        project_id=project_id,
        owner_id=owner_id,
        jti=str(claims.get("jti", "")),
        expires_at=int(claims["exp"]),
    )
