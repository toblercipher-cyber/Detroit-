"""The static preview route must serve a project only to its owner.

WHY THIS SUITE EXISTS
    ``/preview/{project_id}/...`` used to serve generated files to anyone who
    knew a project id. No credentials, no owner check. A probe fetched user A's
    built ``index.html`` as user B, and with no credentials at all, and got 200
    with A's content both times.

    That is a data breach rather than a bug report, so the property is pinned
    here the way ``isolation_check`` pins the typed API: by trying the attack
    over the real routes and requiring it to fail.

WHAT IT DRIVES
    The real FastAPI app, the real token module, a real PostgreSQL connection
    as the restricted ``detroit_app`` role, and real RLS. Two things are
    substituted, both for reasons that do not touch what is under test:

    - the model clients, because no Anthropic key is needed to prove
      authorization and the suite must run without one;
    - token verification, because minting a genuine Supabase access token needs
      that project's private signing key, which nobody has. Identity is faked;
      everything downstream of it -- the scoped service, the bound repository,
      the preview token, the policies -- is production code.

    Projects are created through the SERVICE rather than ``POST /projects`` on
    purpose: the HTTP route kicks off a background workflow that immediately
    fails on the absent provider key, which would make this suite slow and
    noisy while proving nothing about previews.

SKIPS RATHER THAN FAILS without ``DATABASE_URL_APP``, because there is then no
owner-scoped store and a red suite would say something untrue.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

PASS = "PASS"
FAIL = "FAIL"
_results: list[bool] = []


def ok(condition: bool) -> str:
    _results.append(bool(condition))
    return PASS if condition else FAIL


def skip(reason: str) -> None:
    print(f"SKIPPED: {reason}")
    sys.exit(0)


from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(".env")
if not _env.get("DATABASE_URL_APP"):
    skip("DATABASE_URL_APP is not set; there is no owner-scoped store to test.")

os.environ.setdefault("ANTHROPIC_API_KEY", "stub-key-for-tests")
# Set BEFORE settings are first built. In multi-user mode the app refuses to
# start without this, which is the behaviour under test in group 0; the suite
# supplies its own key so it never depends on a developer's .env holding one.
# An environment variable outranks .env in pydantic-settings, so this wins.
os.environ.setdefault(
    "PREVIEW_URL_SECRET", "suite-only-preview-signing-key-not-a-real-secret"
)
TEST_SECRET = os.environ["PREVIEW_URL_SECRET"]

import jwt  # noqa: E402
import psycopg  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api.deps import get_caller  # noqa: E402
from backend.auth import preview_token  # noqa: E402
from backend.auth.jwt import CallerIdentity  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.schemas.state import PreviewStatus  # noqa: E402


def _caller_from_test_header(request: Request) -> CallerIdentity:
    """Stand in for token verification. The header names the user.

    MUST live at module scope -- this file uses ``from __future__ import
    annotations``, so FastAPI resolves the ``Request`` annotation from module
    globals. Defined inside a function the name is invisible and the parameter
    is misread as a query argument.
    """
    raw = request.headers.get("X-Test-User")
    if not raw:
        from backend.api.deps import _unauthenticated

        raise _unauthenticated("An access token is required.")
    return CallerIdentity(user_id=uuid.UUID(raw))


USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
ADMIN_DSN = _env["DATABASE_URL"]
MARK = "@preview-auth-suite.invalid"

SECRET_MARKUP = "<h1>USER-A-PRIVATE-CONTENT</h1>"
ASSET_MARKUP = "console.log('user-a-private-asset')"


def seed_users() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn, conn.cursor() as cur:
        for user in (USER_A, USER_B):
            cur.execute(
                "INSERT INTO auth.users (id, instance_id, aud, role, email)"
                " VALUES (%s,'00000000-0000-0000-0000-000000000000',"
                "'authenticated','authenticated',%s)",
                (user, f"{user}{MARK}"),
            )


def cleanup() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn, conn.cursor() as cur:
        # Projects cascade from auth.users, so this removes those rows too.
        cur.execute("DELETE FROM auth.users WHERE email LIKE %s", (f"%{MARK}",))


def forge(payload: dict, secret: str = TEST_SECRET) -> str:
    """Sign an arbitrary payload, for the tampering cases."""
    return jwt.encode(payload, secret, algorithm="HS256")


def alg_none_token(project_id: str, owner_id: str) -> str:
    """An unsigned token claiming ``alg: none``.

    Hand-assembled because PyJWT will not produce one. This is the classic
    algorithm-confusion attack, and it is the reason ``verify`` pins
    ``algorithms=["HS256"]`` instead of trusting the header.
    """

    def seg(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    header = seg({"alg": "none", "typ": "JWT"})
    body = seg(
        {
            "pid": project_id,
            "sub": owner_id,
            "scope": "preview",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": uuid.uuid4().hex,
        }
    )
    return f"{header}.{body}."


def main() -> None:
    app = create_app()
    app.dependency_overrides[get_caller] = _caller_from_test_header

    seed_users()
    try:
        with TestClient(app) as client:
            a = {"X-Test-User": str(USER_A)}
            b = {"X-Test-User": str(USER_B)}

            service = app.state.project_service
            svc_a = service.for_owner(USER_A)

            # --- A project owned by A, with a built preview on disk ---
            state = svc_a.create(prompt="a coffee shop landing page")
            pid = state.project_id

            dist = svc_a.workspace_dir(pid) / "dist"
            (dist / "assets").mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text(SECRET_MARKUP, encoding="utf-8")
            (dist / "assets" / "app.js").write_text(ASSET_MARKUP, encoding="utf-8")

            state.preview_status = PreviewStatus(
                preview_url=f"/preview/{pid}/",
                built_at=datetime.now(timezone.utc),
                status="fresh",
            )
            svc_a._repository.save(state)

            print("=== 0. posture ===")
            print("   preview signing key configured:",
                  ok(bool(getattr(app.state, "preview_url_secret", ""))))
            print("   service is owner-scoped       :", ok(svc_a.is_owner_scoped))

            print()
            print("=== 1. the OLD unauthenticated URL shape is dead ===")
            r = client.get(f"/preview/{pid}/index.html")
            print("   /preview/{project_id}/index.html -> %s" % r.status_code,
                  ok(r.status_code == 404))
            print("   body does not contain A's content:",
                  ok(SECRET_MARKUP not in r.text))
            r = client.get(f"/preview/{pid}/")
            print("   /preview/{project_id}/           -> %s" % r.status_code,
                  ok(r.status_code == 404))

            print()
            print("=== 2. a token cannot be obtained without, or across, identity ===")
            r = client.get(f"/api/v1/projects/{pid}/preview")
            print("   anonymous  -> %s" % r.status_code, ok(r.status_code == 401))
            r = client.get(f"/api/v1/projects/{pid}/preview", headers=b)
            print("   user B     -> %s" % r.status_code, ok(r.status_code == 404))
            print("   B's code   : %s" % r.json()["error"]["code"],
                  ok(r.json()["error"]["code"] == "project_not_found"))

            r = client.get(f"/api/v1/projects/{pid}/preview", headers=a)
            print("   owner A    -> %s" % r.status_code, ok(r.status_code == 200))
            body = r.json()
            token = body["preview_url"].strip("/").split("/")[-1]
            print("   url is tokenised, not the id:",
                  ok(pid not in body["preview_url"] and len(token) > 40))
            print("   expires_at present          :", ok("expires_at" in body))

            print()
            print("=== 3. the owner's token serves the file ===")
            r = client.get(f"/preview/{token}/index.html")
            print("   index.html -> %s" % r.status_code, ok(r.status_code == 200))
            print("   correct content:", ok(SECRET_MARKUP in r.text))
            print("   content-type   :", ok("text/html" in r.headers["content-type"]))

            print()
            print("=== 4. relative sub-resources load under the same token ===")
            # The regression that would otherwise ship a blank preview: the
            # generated site uses base "./", so this is the request the browser
            # makes after parsing index.html.
            r = client.get(f"/preview/{token}/assets/app.js")
            print("   assets/app.js -> %s" % r.status_code, ok(r.status_code == 200))
            print("   correct content:", ok(ASSET_MARKUP in r.text))
            print("   js content-type:",
                  ok("javascript" in r.headers["content-type"]))

            print()
            print("=== 5. security headers survive, and framing is allowlisted ===")
            r = client.get(f"/preview/{token}/index.html")
            csp = r.headers.get("content-security-policy", "")
            print("   nosniff            :",
                  ok(r.headers.get("x-content-type-options") == "nosniff"))
            print("   no-referrer        :",
                  ok(r.headers.get("referrer-policy") == "no-referrer"))
            print("   no-store           :",
                  ok(r.headers.get("cache-control") == "no-store"))
            print("   script-src 'self'  :", ok("script-src 'self'" in csp))
            print("   connect-src 'none' :", ok("connect-src 'none'" in csp))
            print("   frame-ancestors set:", ok("frame-ancestors" in csp))
            print("   frame-ancestors is NOT a wildcard:",
                  ok("frame-ancestors *" not in csp))
            print("   names the configured origin:",
                  ok(any(o in csp for o in app.state.settings.allowed_origins)))

            print()
            print("=== 6. expired tokens are refused ===")
            now = int(time.time())
            expired = forge(
                {
                    "pid": pid, "sub": str(USER_A), "scope": "preview",
                    "iat": now - 3600, "nbf": now - 3600, "exp": now - 1800,
                    "jti": uuid.uuid4().hex,
                }
            )
            r = client.get(f"/preview/{expired}/index.html")
            print("   expired 30 min ago -> %s" % r.status_code,
                  ok(r.status_code == 404))
            print("   leaked nothing:", ok(SECRET_MARKUP not in r.text))

            print()
            print("=== 7. tampering is refused in every shape ===")
            cases: list[tuple[str, str]] = []

            # Flip a byte in the payload segment.
            head, payload, sig = token.split(".")
            flipped = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
            cases.append(("payload byte flipped", f"{head}.{flipped}.{sig}"))

            # A second project, used by group 9 for the unbuilt-preview case.
            other = svc_a.create(prompt="second project")

            # Signed with a key an attacker guessed. Everything else about the
            # token is well-formed, so only the signature stops it.
            cases.append((
                "re-signed with wrong key",
                forge(
                    {
                        "pid": pid, "sub": str(USER_A), "scope": "preview",
                        "iat": now, "nbf": now, "exp": now + 300,
                        "jti": uuid.uuid4().hex,
                    },
                    secret="an-attacker-guess-at-the-signing-key",
                ),
            ))
            cases.append(("alg:none", alg_none_token(pid, str(USER_A))))
            cases.append(("truncated", token[: len(token) // 2]))
            cases.append(("signature stripped", f"{head}.{payload}."))
            cases.append(("not a jwt at all", "just-some-string"))
            cases.append((
                "wrong scope",
                forge(
                    {
                        "pid": pid, "sub": str(USER_A), "scope": "something_else",
                        "iat": now, "nbf": now, "exp": now + 300,
                        "jti": uuid.uuid4().hex,
                    }
                ),
            ))
            cases.append((
                "missing exp",
                forge(
                    {
                        "pid": pid, "sub": str(USER_A), "scope": "preview",
                        "iat": now, "nbf": now, "jti": uuid.uuid4().hex,
                    }
                ),
            ))
            # Signed by us, but naming B as the owner of A's project. RLS and
            # the ownership re-check are what refuse this.
            cases.append((
                "owner swapped to B",
                forge(
                    {
                        "pid": pid, "sub": str(USER_B), "scope": "preview",
                        "iat": now, "nbf": now, "exp": now + 300,
                        "jti": uuid.uuid4().hex,
                    }
                ),
            ))

            for label, bad in cases:
                r = client.get(f"/preview/{bad}/index.html")
                clean = r.status_code == 404 and SECRET_MARKUP not in r.text
                print("   %-26s -> %s %s" % (label, r.status_code, ok(clean)))

            print()
            print("=== 8. path traversal and unlisted types, with a VALID token ===")
            # The token is genuine here, so only containment is being tested.
            traversals = [
                "../../../../etc/passwd",
                "..%2f..%2f.env",
                "../.env",
                "/etc/passwd",
                "C:/Windows/win.ini",
                "assets/../../.env",
                "danger.exe",
                "../workspace/package.json",
            ]
            for path in traversals:
                r = client.get(f"/preview/{token}/{path}")
                print("   %-28s -> %s" % (path, r.status_code),
                      ok(r.status_code == 404))

            print()
            print("=== 9. a token does not outlive its project ===")
            r = client.get(f"/api/v1/projects/{other.project_id}/preview", headers=a)
            # The second project has no build, so the typed route answers 409 —
            # which is itself worth asserting.
            print("   unbuilt project -> %s" % r.status_code, ok(r.status_code == 409))

            # Delete the FIRST project while its token is still unexpired.
            still_valid = token
            r = client.delete(f"/api/v1/projects/{pid}", headers=a)
            print("   owner deletes it -> %s" % r.status_code,
                  ok(r.status_code == 204))
            r = client.get(f"/preview/{still_valid}/index.html")
            print("   unexpired token after deletion -> %s" % r.status_code,
                  ok(r.status_code == 404))
            print("   leaked nothing:", ok(SECRET_MARKUP not in r.text))

    finally:
        cleanup()

    passed = sum(_results)
    total = len(_results)
    print()
    print(f"=== {passed}/{total} assertions passed ===")
    if passed != total:
        sys.exit(1)


main()
