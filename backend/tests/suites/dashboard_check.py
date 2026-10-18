"""The dashboard's data: one authenticated request, one user's projects.

WHY THIS SUITE EXISTS
    Every figure on `/dashboard` is derived from `GET /api/v1/projects`. So the
    things worth asserting are that the payload carries what the page needs
    WITHOUT a per-project fan-out, that the numbers come from persisted state,
    and that one user's totals cannot include another's rows.

    The last one is the only property here where a silent failure is a breach,
    and it is checked against a real owner-scoped PostgreSQL connection with
    real RLS rather than against a filter in the page.

SKIPS without DATABASE_URL_APP: there is then no owner-scoped store.
"""

from __future__ import annotations

import os
import sys
import uuid

PASS = "PASS"
_results: list[bool] = []


def ok(condition: bool) -> str:
    _results.append(bool(condition))
    return PASS if condition else "**FAIL**"


def skip(reason: str) -> None:
    print(f"SKIPPED: {reason}")
    sys.exit(0)


sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True

from dotenv import dotenv_values  # noqa: E402

_env = dotenv_values(".env")
if not _env.get("DATABASE_URL_APP"):
    skip("DATABASE_URL_APP is not set; there is no owner-scoped store to test.")
os.environ.setdefault("ANTHROPIC_API_KEY", "stub-key-for-tests")
os.environ.setdefault("PREVIEW_URL_SECRET", "test-only-preview-signing-key")

import psycopg  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api.deps import get_caller  # noqa: E402
from backend.auth.jwt import CallerIdentity  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.schemas.enums import RunStatus  # noqa: E402


def _caller_from_test_header(request: Request) -> CallerIdentity:
    raw = request.headers.get("X-Test-User")
    if not raw:
        from backend.api.deps import _unauthenticated

        raise _unauthenticated("An access token is required.")
    return CallerIdentity(user_id=uuid.UUID(raw))


USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
ADMIN_DSN = _env["DATABASE_URL"]
MARK = "@dashboard-suite.invalid"


def seed() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c, c.cursor() as cur:
        for u in (USER_A, USER_B):
            cur.execute(
                "INSERT INTO auth.users (id, instance_id, aud, role, email)"
                " VALUES (%s,'00000000-0000-0000-0000-000000000000',"
                "'authenticated','authenticated',%s)",
                (u, f"{u}{MARK}"),
            )


def cleanup() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DELETE FROM auth.users WHERE email LIKE %s", (f"%{MARK}",))


def main() -> None:
    app = create_app()
    app.dependency_overrides[get_caller] = _caller_from_test_header
    seed()
    try:
        with TestClient(app) as client:
            a = {"X-Test-User": str(USER_A)}
            b = {"X-Test-User": str(USER_B)}
            svc_a = app.state.project_service.for_owner(USER_A)
            svc_b = app.state.project_service.for_owner(USER_B)

            # A has three projects in three different states; B has one.
            p1 = svc_a.create(prompt="coffee shop")
            p2 = svc_a.create(prompt="bakery")
            p3 = svc_a.create(prompt="gym")
            p2.run_status = RunStatus.COMPLETED
            svc_a.save(p2)
            p3.run_status = RunStatus.AWAITING_HITL
            p3.iteration_count = 2
            svc_a.save(p3)
            b1 = svc_b.create(prompt="user B's private project")

            print("=== 1. One request carries everything the page needs ===")
            r = client.get("/api/v1/projects", headers=a)
            print("   200                 :", ok(r.status_code == 200))
            projects = r.json()["projects"]
            first = projects[0]
            for field in ("project_id", "title", "stage", "status",
                          "updated_at", "created_at", "iteration_count"):
                print(f"   carries {field:16}:", ok(field in first))
            print("   no per-project fetch needed:",
                  ok(all("iteration_count" in p for p in projects)))

            print()
            print("=== 2. The numbers come from persisted state ===")
            by_id = {p["project_id"]: p for p in projects}
            print("   A sees exactly 3     :", len(projects), ok(len(projects) == 3))
            print("   completed reflected  :",
                  ok(by_id[p2.project_id]["status"] == "completed"))
            print("   awaiting reflected   :",
                  ok(by_id[p3.project_id]["status"] == "awaiting_hitl"))
            print("   iteration_count real :",
                  ok(by_id[p3.project_id]["iteration_count"] == 2))
            print("   untouched stays idle :",
                  ok(by_id[p1.project_id]["iteration_count"] == 0))

            print()
            print("=== 3. ISOLATION: B's project is invisible to A ===")
            ids_a = {p["project_id"] for p in projects}
            print("   B's id absent for A  :", ok(b1.project_id not in ids_a))
            rb = client.get("/api/v1/projects", headers=b)
            ids_b = {p["project_id"] for p in rb.json()["projects"]}
            print("   B sees exactly 1     :", ok(len(ids_b) == 1))
            print("   A's ids absent for B :", ok(not (ids_a & ids_b)))
            print("   A's title not in B's payload:",
                  ok("coffee shop" not in rb.text))

            print()
            print("=== 4. Identity comes from the token, never the request ===")
            # A request that tries to name another owner changes nothing: the
            # service is bound to the verified caller before any query runs.
            spoof = client.get(
                f"/api/v1/projects?owner_id={USER_B}", headers=a
            )
            spoofed_ids = {p["project_id"] for p in spoof.json()["projects"]}
            print("   query param ignored  :", ok(spoofed_ids == ids_a))
            print("   anonymous -> 401     :",
                  ok(client.get("/api/v1/projects").status_code == 401))

            print()
            print("=== 5. A new project appears immediately ===")
            svc_a.create(prompt="fourth")
            again = client.get("/api/v1/projects", headers=a).json()["projects"]
            print("   now 4 for A          :", ok(len(again) == 4))
            print("   still 1 for B        :",
                  ok(len(client.get("/api/v1/projects", headers=b).json()["projects"]) == 1))
    finally:
        cleanup()

    passed, total = sum(_results), len(_results)
    print()
    print(f"=== {passed}/{total} assertions passed ===")
    sys.exit(0 if passed == total else 1)


main()
