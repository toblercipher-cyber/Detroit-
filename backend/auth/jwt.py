"""Supabase Auth token verification.

WHY THIS FILE EXISTS
    This is the only place the backend decides who someone is. Everything
    downstream — ownership, RLS claims, 404-vs-200 — depends on the `sub` this
    module returns, so it is deliberately small, strict, and has no other job.

LAYER
    Auth (``backend/auth/``). Depends on config and the standard library plus
    PyJWT. It imports nothing from api, services, graph or memory, so it can be
    tested without any of them.

WHAT IT DOES NOT DO
    - **No sessions.** Supabase Auth issues and refreshes tokens; this verifies
      them. There is no second authentication system (CLAUDE.md 9A).
    - **No authorization.** It answers "which user is this", never "may they".
      Ownership is the service's decision.
    - **No HTTP.** Turning a failure into a 401 envelope is the API layer's job.

---------------------------------------------------------------------------
WHY ASYMMETRIC VERIFICATION, AND NOT A SHARED SECRET

This project's Supabase instance signs access tokens with **ES256** and
publishes a JWKS — confirmed against the live project, which serves one EC
signing key. There is therefore no symmetric secret to compare against, and
``SUPABASE_JWT_SECRET`` must not be introduced: adding one would mean holding
a credential that can MINT tokens, where today the backend only holds a public
key that can CHECK them. That asymmetry is the security win, and it is worth
protecting.

WHAT IS ACTUALLY CHECKED

Verification is only as good as its narrowest check, so all of these are on:

    signature   against the JWKS key named by the token's `kid`
    exp         expiry, with a small leeway for clock skew
    iat/nbf     not-yet-valid tokens rejected
    iss         must be this project's `/auth/v1` issuer
    aud         must be "authenticated"
    sub         must be present and a valid UUID

A token that merely *decodes* is not a verified token. `jwt.decode` is never
called with `verify_signature: False` anywhere in this module.

KEY ROTATION

Supabase rotates signing keys. The JWKS is cached, and a cache that never
expires would reject every token signed by a new key until the process
restarts. So: a TTL, plus a forced refresh the first time an unknown `kid`
appears. The forced refresh is rate-limited, because an attacker who can send
arbitrary `kid` values must not be able to turn that into a request amplifier
against Supabase.

Spec: docs/api.md 1.1; docs/database.md 1.3.5; docs/security.md 2, 6.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from uuid import UUID

import jwt
from jwt import PyJWKClient

from backend.config.settings import Settings

logger = logging.getLogger(__name__)

# Supabase mints access tokens with this audience.
EXPECTED_AUDIENCE = "authenticated"

# Tolerance for clock skew between this machine and Supabase. Small on purpose:
# it widens the window in which an expired token still works.
LEEWAY_SECONDS = 10

# How long a fetched JWKS is trusted before it is re-read.
JWKS_CACHE_SECONDS = 600

# Floor between forced refreshes triggered by an unknown `kid`, so a stream of
# junk tokens cannot be turned into a flood of requests to Supabase.
JWKS_MIN_REFRESH_SECONDS = 30

# Only asymmetric algorithms. Listing them explicitly is what prevents the
# classic confusion attack: a token whose header says `alg: HS256` would
# otherwise be checked with the public key AS a shared secret, and a public key
# is not secret. `alg: none` is likewise impossible to reach from here.
ALLOWED_ALGORITHMS = ("ES256", "RS256")


class AuthError(Exception):
    """Token verification failed.

    The message is safe to show a caller: it says what was wrong in general
    terms and never includes the token, a key, or a claim value. Anything more
    specific would help an attacker tune their next attempt.
    """


@dataclass(frozen=True)
class CallerIdentity:
    """A verified caller. The only thing downstream code may treat as identity.

    Frozen because nothing should be able to edit who the caller is after
    verification — the whole point is that this value came from a signature.
    """

    user_id: UUID
    email: str | None = None


class SupabaseTokenVerifier:
    """Verifies Supabase access tokens against the project's JWKS.

    One instance per process, built at startup and resolved as a dependency.
    The JWKS client holds a cache, so a per-request instance would fetch keys
    on every call.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url:
            raise AuthError("SUPABASE_URL is not configured.")
        base = settings.supabase_url.rstrip("/")
        self._issuer = f"{base}/auth/v1"
        self._jwks_url = f"{self._issuer}/.well-known/jwks.json"
        self._client = PyJWKClient(
            self._jwks_url,
            cache_keys=True,
            lifespan=JWKS_CACHE_SECONDS,
        )
        self._lock = threading.Lock()
        self._last_forced_refresh = 0.0

    def _signing_key(self, token: str):
        """Resolve the signing key, refreshing once for an unknown ``kid``.

        ``PyJWKClient`` caches, so a key rotated in since the last fetch is
        simply absent and lookup raises. One forced refresh turns that from a
        hard outage into a brief miss — rate-limited so repeated junk tokens
        cannot amplify into traffic against Supabase.
        """
        try:
            return self._client.get_signing_key_from_jwt(token)
        except Exception:
            now = time.monotonic()
            with self._lock:
                too_soon = now - self._last_forced_refresh < JWKS_MIN_REFRESH_SECONDS
                if not too_soon:
                    self._last_forced_refresh = now
            if too_soon:
                raise AuthError("Token signing key is not recognised.") from None
            logger.info("unknown JWT kid; refreshing JWKS once")
            try:
                self._client.fetch_data()
                return self._client.get_signing_key_from_jwt(token)
            except Exception:
                raise AuthError("Token signing key is not recognised.") from None

    def verify(self, token: str) -> CallerIdentity:
        """Verify a bearer token and return who it belongs to.

        Raises:
            AuthError: for every failure mode. The caller turns it into a 401;
                it never distinguishes "bad signature" from "expired" to the
                client beyond what is already obvious.
        """
        if not token or not token.strip():
            raise AuthError("No access token was provided.")

        key = self._signing_key(token)
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=EXPECTED_AUDIENCE,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={
                    "require": ["exp", "sub", "aud", "iss"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("The access token has expired.") from None
        except jwt.InvalidAudienceError:
            raise AuthError("The access token was issued for another audience.") from None
        except jwt.InvalidIssuerError:
            raise AuthError("The access token was issued by another project.") from None
        except jwt.InvalidTokenError:
            # Covers bad signature, malformed token, disallowed alg, missing
            # required claim. Collapsed on purpose: a caller does not need to
            # know which, and telling them helps only an attacker.
            raise AuthError("The access token is not valid.") from None

        subject = claims.get("sub")
        try:
            user_id = UUID(str(subject))
        except (TypeError, ValueError):
            raise AuthError("The access token has no usable subject.") from None

        email = claims.get("email")
        return CallerIdentity(
            user_id=user_id, email=email if isinstance(email, str) else None
        )
