"""GitHub PUSH routes over real HTTP: ownership, approval, and what never leaks.

Push is the only sync route that exists; pull/import was removed as out of
scope, so there is nothing here that exercises it.

WHY THIS SUITE EXISTS SEPARATELY FROM ``github_check``
    That one proves the gateway's logic. This one proves the things that are
    properties of the ROUTE: that a second user cannot reach the first user's
    repository selection, that a push cannot happen without spending an
    approval, and that no response carries the credential.

    Those can only be checked by driving the real FastAPI app through the real
    dependency chain, with a real owner-scoped PostgreSQL repository and real
    RLS. Only two things are substituted: the model clients (no provider key
    needed to prove authorization) and token verification (minting a genuine
    Supabase token needs a private key nobody has). Everything downstream of
    identity is production code.

SKIPS without DATABASE_URL_APP: there is then no owner-scoped store, and a
green suite would be claiming something untrue.
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
from pydantic import SecretStr  # noqa: E402

from backend.api.deps import get_caller  # noqa: E402
from backend.auth.jwt import CallerIdentity  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.services.external_actions import ExternalActionService  # noqa: E402
from backend.services.github_gateway import GitHubGateway  # noqa: E402

# The shared helper, NOT the sibling suite: importing a suite would run it.
from backend.tests.suites._github_mock import (  # noqa: E402
    TOKEN,
    MockGitHub,
    gateway_with,
)


def _caller_from_test_header(request: Request) -> CallerIdentity:
    raw = request.headers.get("X-Test-User")
    if not raw:
        from backend.api.deps import _unauthenticated

        raise _unauthenticated("An access token is required.")
    return CallerIdentity(user_id=uuid.UUID(raw))


USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
ADMIN_DSN = _env["DATABASE_URL"]
MARK = "@github-routes-suite.invalid"


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
    mock = MockGitHub()
    app = create_app()
    app.dependency_overrides[get_caller] = _caller_from_test_header

    seed()
    try:
        with TestClient(app) as client:
            # A configured gateway and an enabled approval gate, so the routes
            # exercise the real path rather than short-circuiting at 501.
            app.state.github_gateway = gateway_with(mock)
            app.state.external_actions = ExternalActionService(
                capability_check=lambda action: True
            )

            a = {"X-Test-User": str(USER_A)}
            b = {"X-Test-User": str(USER_B)}
            svc_a = app.state.project_service.for_owner(USER_A)
            state = svc_a.create(prompt="a coffee shop landing page")
            pid = state.project_id

            # Give the project something to push.
            ws = svc_a.workspace_dir(pid) / "src"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "App.tsx").write_text("export const A = 1;\n", encoding="utf-8")

            P = f"/api/v1/projects/{pid}"

            print("=== 1. Every GitHub route requires authentication ===")
            for method, path in [
                ("GET", "/api/v1/github/status"),
                ("GET", "/api/v1/github/repositories"),
                ("GET", f"{P}/github"),
                ("PUT", f"{P}/github"),
                ("DELETE", f"{P}/github"),
                ("POST", f"{P}/github/push/preview"),
                ("POST", f"{P}/github/push"),
            ]:
                r = client.request(method, path)
                print(f"   {method:6} {path.replace(pid, '{id}'):42} -> {r.status_code}",
                      ok(r.status_code == 401))

            print()
            print("=== 2. Status and repositories carry NO credential ===")
            r = client.get("/api/v1/github/status", headers=a)
            body = r.text
            secret = TOKEN.get_secret_value()
            print("   status 200            :", ok(r.status_code == 200))
            print("   no token in body      :", ok(secret not in body))
            print("   configured is a bool  :", ok(isinstance(r.json()["configured"], bool)))
            r = client.get("/api/v1/github/repositories", headers=a)
            print("   repos listed          :", ok(r.status_code == 200 and r.json()["repositories"]))
            print("   no token in body      :", ok(secret not in r.text))

            print()
            print("=== 3. Selecting a repository is owner-scoped ===")
            r = client.put(f"{P}/github", json={"repository": "octo/site"}, headers=a)
            print("   A connects            :", r.status_code, ok(r.status_code == 200))
            print("   stored repository     :", ok(r.json()["repository"] == "octo/site"))
            r = client.get(f"{P}/github", headers=a)
            print("   A reads it back       :", ok(r.json()["connected"] is True))
            # B must not see or change A's project at all.
            print("   B GET   -> 404        :", ok(client.get(f"{P}/github", headers=b).status_code == 404))
            print("   B PUT   -> 404        :", ok(client.put(f"{P}/github", json={"repository": "octo/site"}, headers=b).status_code == 404))
            print("   B DELETE-> 404        :", ok(client.delete(f"{P}/github", headers=b).status_code == 404))
            print("   B preview push -> 404 :", ok(client.post(f"{P}/github/push/preview", headers=b).status_code == 404))

            print()
            print("=== 3a. A pasted URL connects the same repository ===")
            # The primary user journey: paste the address bar, not owner/name.
            client.delete(f"{P}/github", headers=a)
            r = client.put(f"{P}/github",
                           json={"repository": "https://github.com/octo/site"},
                           headers=a)
            print("   URL accepted          :", r.status_code, ok(r.status_code == 200))
            print("   normalised to owner/n :", ok(r.json()["repository"] == "octo/site"))
            print("   target branch shown   :", r.json()["branch"], ok(bool(r.json()["branch"])))
            print("   default branch shown  :", ok(r.json()["default_branch"] == "main"))
            r = client.put(f"{P}/github",
                           json={"repository": "git@github.com:octo/site.git"},
                           headers=a)
            print("   clone string accepted :", ok(r.status_code == 200
                                                   and r.json()["repository"] == "octo/site"))
            r = client.put(f"{P}/github", json={"repository": "not a repo"}, headers=a)
            print("   junk refused (409)    :", r.status_code, ok(r.status_code == 409))

            print()
            print("=== 3b. Write permission is verified before connecting ===")
            mock.can_push = False
            r = client.put(f"{P}/github",
                           json={"repository": "https://github.com/octo/site"},
                           headers=a)
            print("   read-only repo refused:", r.status_code, ok(r.status_code == 409))
            print("   message explains why  :", ok("push" in r.json()["error"]["message"]))
            mock.can_push = True
            client.put(f"{P}/github", json={"repository": "octo/site"}, headers=a)

            print()
            print("=== 4. Selection survives a reload (it is persisted) ===")
            reloaded = app.state.project_service.for_owner(USER_A).get(pid)
            print("   state.github persisted:", ok(reloaded.github is not None
                                                   and reloaded.github.repository == "octo/site"))

            print()
            print("=== 5. A push cannot happen without an approval ===")
            r = client.post(f"{P}/github/push",
                            json={"plan_id": "made-up", "approval_token": "made-up"},
                            headers=a)
            print("   invented approval -> 403:", r.status_code, ok(r.status_code == 403))
            print("   code                    :", ok(r.json()["error"]["code"] == "approval_required"))
            print("   nothing was committed   :", ok(mock.committed is None))

            print()
            print("=== 6. Preview mutates nothing and issues one approval ===")
            r = client.post(f"{P}/github/push/preview", headers=a)
            print("   preview 200             :", r.status_code, ok(r.status_code == 200))
            plan = r.json()
            print("   describes the change    :", ok(plan["added"] + plan["updated"] > 0))
            print("   carries a token         :", ok(bool(plan["approval_token"])))
            print("   still nothing committed :", ok(mock.committed is None))

            print()
            print("=== 7. The approval works exactly once ===")
            payload = {"plan_id": plan["plan_id"], "approval_token": plan["approval_token"]}
            r = client.post(f"{P}/github/push", json=payload, headers=a)
            print("   first push 200          :", r.status_code, ok(r.status_code == 200))
            print("   files were committed    :", ok(mock.committed is not None))
            r2 = client.post(f"{P}/github/push", json=payload, headers=a)
            print("   replay -> 410           :", r2.status_code, ok(r2.status_code == 410))
            print("   code                    :", ok(r2.json()["error"]["code"] == "approval_expired"))

            print()
            print("=== 8. An approval is bound to its project ===")
            other = svc_a.create(prompt="second project")
            r = client.post(f"/api/v1/projects/{other.project_id}/github/push",
                            json=payload, headers=a)
            # Not connected AND not its approval: either way it must not push.
            print("   other project refused   :", r.status_code, ok(r.status_code in (403, 409)))

            print()
            print("=== 9. The action was recorded in history ===")
            after = app.state.project_service.for_owner(USER_A).get(pid)
            kinds = [a_.action_type for a_ in after.mcp_action_history]
            print("   mcp_action_history      :", kinds, ok("github_push" in kinds))
            print("   last_commit_sha stored  :", ok(after.github.last_commit_sha == "commit-new"))

            print()
            print("=== 10. Disconnect is local only ===")
            before_requests = len(mock.requests)
            r = client.delete(f"{P}/github", headers=a)
            print("   204                     :", ok(r.status_code == 204))
            print("   no GitHub call made     :", ok(len(mock.requests) == before_requests))
            print("   link cleared            :", ok(client.get(f"{P}/github", headers=a).json()["connected"] is False))

            print()
            print("=== 11. Push on a disconnected project is refused ===")
            r = client.post(f"{P}/github/push/preview", headers=a)
            print("   409 not 500             :", r.status_code, ok(r.status_code == 409))

            client.delete(f"{P}", headers=a)
            client.delete(f"/api/v1/projects/{other.project_id}", headers=a)
    finally:
        cleanup()

    passed, total = sum(_results), len(_results)
    print()
    print(f"=== {passed}/{total} assertions passed ===")
    sys.exit(0 if passed == total else 1)


main()
