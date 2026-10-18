"""User A must not reach User B's project, through any route.

WHY THIS SUITE EXISTS
    Ownership isolation is the one property where a silent failure is a data
    breach rather than a bug report. Every other suite checks that something
    works; this one checks that something is impossible.

WHAT IT DRIVES
    The real FastAPI app over its real routes, with a real PostgreSQL
    connection as the restricted ``detroit_app`` role and real RLS policies.
    Only two things are substituted:

    - the model clients, because no Anthropic key is required to prove
      authorization and the suite must run without one;
    - token verification, because minting a genuine Supabase token needs that
      project's private signing key, which nobody has. The verifier is
      replaced by one that maps a fixed header value to a user id. Everything
      downstream of identity -- the scoped service, the bound repository, the
      claims, the policies -- is the production path, unmodified.

    The JWT verifier's own correctness is covered separately; this suite is
    about what happens AFTER a caller is identified.

SKIPS RATHER THAN FAILS when DATABASE_URL_APP is absent, because there is then
no owner-scoped store to test and a red suite would say something untrue.
"""

from __future__ import annotations

import os
import sys
import uuid

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
os.environ.setdefault("PREVIEW_URL_SECRET", "test-only-preview-signing-key")  # signs preview URLs; main.py refuses to start without one

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api.deps import get_caller  # noqa: E402
from backend.auth.jwt import CallerIdentity  # noqa: E402
from backend.config.settings import get_settings  # noqa: E402
from fastapi import Request  # noqa: E402

from backend.main import create_app  # noqa: E402


def _caller_from_test_header(request: Request) -> CallerIdentity:
    """Stand in for token verification. The header names the user.

    MUST live at module scope. This file uses ``from __future__ import
    annotations``, so FastAPI resolves the ``Request`` annotation from module
    globals -- defined inside a function, the name is invisible to it and the
    parameter is misread as a query argument, which surfaces as a confusing
    422 instead of running at all.

    Only identity is faked. Everything downstream is production code.
    """
    raw = request.headers.get("X-Test-User")
    if not raw:
        return _reject()
    return CallerIdentity(user_id=uuid.UUID(raw))


def _reject() -> CallerIdentity:
    from backend.api.deps import _unauthenticated

    raise _unauthenticated("An access token is required.")

USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
ADMIN_DSN = _env["DATABASE_URL"]


def seed_users() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn, conn.cursor() as cur:
        for user in (USER_A, USER_B):
            cur.execute(
                "INSERT INTO auth.users (id, instance_id, aud, role, email)"
                " VALUES (%s,'00000000-0000-0000-0000-000000000000',"
                "'authenticated','authenticated',%s)",
                (user, f"{user}@isolation-suite.invalid"),
            )


def cleanup() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn, conn.cursor() as cur:
        # Projects cascade from auth.users, so this removes the rows too.
        cur.execute(
            "DELETE FROM auth.users WHERE email LIKE '%@isolation-suite.invalid'"
        )


def main() -> None:
    settings = get_settings()
    app = create_app()

    app.dependency_overrides[get_caller] = _caller_from_test_header

    seed_users()
    try:
        with TestClient(app) as client:
            a = {"X-Test-User": str(USER_A)}
            b = {"X-Test-User": str(USER_B)}

            print("=== 0. storage and auth posture ===")
            print("   app-role storage configured :", ok(settings.is_app_role_configured))
            print("   service is owner-scoped     :",
                  ok(app.state.project_service.for_owner(USER_A).is_owner_scoped))

            print()
            print("=== 1. unauthenticated requests are refused ===")
            for name, call in (
                ("GET  /projects", lambda: client.get("/api/v1/projects")),
                ("POST /projects", lambda: client.post(
                    "/api/v1/projects", json={"prompt": "x"})),
            ):
                r = call()
                body = r.json()
                print(f"   {name:16} -> {r.status_code}",
                      ok(r.status_code == 401),
                      "| code:", body.get("error", {}).get("code"),
                      ok(body.get("error", {}).get("code") == "unauthenticated"))

            print()
            print("=== 2. each user creates a project ===")
            ra = client.post("/api/v1/projects",
                             json={"prompt": "A's coffee shop page"}, headers=a)
            rb = client.post("/api/v1/projects",
                             json={"prompt": "B's pricing page"}, headers=b)
            print("   A created :", ra.status_code, ok(ra.status_code == 201))
            print("   B created :", rb.status_code, ok(rb.status_code == 201))
            pid_a = ra.json()["project_id"]
            pid_b = rb.json()["project_id"]
            print("   distinct ids:", ok(pid_a != pid_b))

            print()
            print("=== 3. listing is owner-scoped ===")
            la = client.get("/api/v1/projects", headers=a).json()["projects"]
            lb = client.get("/api/v1/projects", headers=b).json()["projects"]
            ids_a = {p["project_id"] for p in la}
            ids_b = {p["project_id"] for p in lb}
            print("   A sees only A's :", ok(ids_a == {pid_a}))
            print("   B sees only B's :", ok(ids_b == {pid_b}))

            print()
            print("=== 4. A cannot reach B's project on ANY route ===")
            probes = [
                ("GET    state",      lambda: client.get(f"/api/v1/projects/{pid_b}", headers=a)),
                ("GET    status",     lambda: client.get(f"/api/v1/projects/{pid_b}/status", headers=a)),
                ("GET    preview",    lambda: client.get(f"/api/v1/projects/{pid_b}/preview", headers=a)),
                ("GET    hitl",       lambda: client.get(f"/api/v1/projects/{pid_b}/hitl", headers=a)),
                ("GET    hitl hist",  lambda: client.get(f"/api/v1/projects/{pid_b}/hitl/history", headers=a)),
                ("DELETE project",    lambda: client.delete(f"/api/v1/projects/{pid_b}", headers=a)),
                ("POST   hitl resume", lambda: client.post(
                    f"/api/v1/projects/{pid_b}/hitl", headers=a,
                    json={"checkpoint_id": "ckpt_" + "0" * 32, "action": "approve"})),
                ("POST   ref image",  lambda: client.post(
                    f"/api/v1/projects/{pid_b}/reference-images", headers=a,
                    files={"file": ("x.png", b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 64, "image/png")})),
            ]
            for name, call in probes:
                r = call()
                code = r.json().get("error", {}).get("code") if r.content else None
                # 404 everywhere: another user's project must be indistinguishable
                # from one that does not exist.
                print(f"   {name:19} -> {r.status_code}", ok(r.status_code == 404),
                      "| code:", code, ok(code == "project_not_found"))

            print()
            print("=== 5. B's project is untouched by A's attempts ===")
            still = client.get(f"/api/v1/projects/{pid_b}", headers=b)
            print("   B can still read it :", still.status_code, ok(still.status_code == 200))
            lb2 = client.get("/api/v1/projects", headers=b).json()["projects"]
            print("   still in B's listing:", ok({p["project_id"] for p in lb2} == {pid_b}))

            print()
            print("=== 6. owners retain full access to their own ===")
            own = client.get(f"/api/v1/projects/{pid_a}", headers=a)
            print("   A reads own         :", own.status_code, ok(own.status_code == 200))
            st = client.get(f"/api/v1/projects/{pid_a}/status", headers=a)
            print("   A reads own status  :", st.status_code, ok(st.status_code == 200))
            dele = client.delete(f"/api/v1/projects/{pid_a}", headers=a)
            print("   A deletes own       :", dele.status_code, ok(dele.status_code == 204))

            print()
            print("=== 7. claims do not leak between pooled requests ===")
            # A deleted its only project. If a claim leaked from B's request,
            # A would now see B's.
            after = client.get("/api/v1/projects", headers=a).json()["projects"]
            print("   A's listing is empty:", ok(after == []))
            bfinal = client.get("/api/v1/projects", headers=b).json()["projects"]
            print("   B still has exactly 1:", ok(len(bfinal) == 1))
    finally:
        cleanup()

    print()
    total, passed = len(_results), sum(_results)
    print(f"=== {passed}/{total} assertions passed ===")
    if passed != total:
        print(FAIL, "isolation is NOT holding")
        sys.exit(1)


if __name__ == "__main__":
    main()
