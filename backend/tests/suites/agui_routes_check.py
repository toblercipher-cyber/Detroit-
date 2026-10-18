"""The AG-UI stream over real HTTP: who may attach, and what they get first.

The stream carries a project's live state, so it needs exactly the ownership
story every other project route has. This drives the real app with a real
owner-scoped PostgreSQL repository and real RLS; only identity is stubbed,
because minting a genuine Supabase token needs a private key nobody has.

SKIPS without DATABASE_URL_APP: there is then no owner-scoped store, and a
green suite would be claiming something untrue.
"""

from __future__ import annotations

import json
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
from backend.services import agui_translator as agui  # noqa: E402
from backend.services.agui_stream import broker  # noqa: E402


def _caller_from_test_header(request: Request) -> CallerIdentity:
    raw = request.headers.get("X-Test-User")
    if not raw:
        from backend.api.deps import _unauthenticated

        raise _unauthenticated("An access token is required.")
    return CallerIdentity(user_id=uuid.UUID(raw))


USER_A = uuid.uuid4()
USER_B = uuid.uuid4()
ADMIN_DSN = _env["DATABASE_URL"]
MARK = "@agui-routes-suite.invalid"


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


def events_from(body: str) -> list[dict]:
    """Parse an SSE body into the JSON payloads it carried."""
    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


def main() -> None:
    app = create_app()
    app.dependency_overrides[get_caller] = _caller_from_test_header
    seed()
    try:
        with TestClient(app) as client:
            a = {"X-Test-User": str(USER_A)}
            b = {"X-Test-User": str(USER_B)}
            svc_a = app.state.project_service.for_owner(USER_A)
            state = svc_a.create(prompt="a coffee shop landing page")
            pid = state.project_id
            url = f"/api/v1/projects/{pid}/agui"

            print("=== 1. The stream is authenticated and owner-scoped ===")
            print("   anonymous -> 401 :", ok(client.get(url).status_code == 401))
            print("   user B    -> 404 :", ok(client.get(url, headers=b).status_code == 404))
            bad = f"/api/v1/projects/proj_{'0' * 32}/agui"
            print("   unknown   -> 404 :", ok(client.get(bad, headers=a).status_code == 404))

            print()
            print("=== 2. The owner gets a protocol stream, state first ===")
            # The project is idle (nothing started it), so the stream sends the
            # snapshot and closes rather than hanging.
            state.run_status = RunStatus.COMPLETED
            svc_a.save(state)
            r = client.get(url, headers=a)
            print("   200                :", r.status_code, ok(r.status_code == 200))
            print("   SSE content type   :",
                  ok("text/event-stream" in r.headers.get("content-type", "")))
            print("   not buffered       :",
                  ok(r.headers.get("x-accel-buffering") == "no"))
            payloads = events_from(r.text)
            print("   first is a snapshot:",
                  ok(bool(payloads) and payloads[0]["type"] == "STATE_SNAPSHOT"))
            print("   snapshot names it  :",
                  ok(payloads[0]["snapshot"]["projectId"] == pid))
            print("   settled run closes :",
                  ok(payloads[-1]["type"] == "RUN_FINISHED"))

            print()
            print("=== 3. A parked run tells a reconnecting tab what to answer ===")
            fresh = svc_a.create(prompt="second project")
            fresh.run_status = RunStatus.AWAITING_HITL
            from backend.schemas.state import HitlCheckpoint
            from backend.schemas.enums import WorkflowStage

            fresh.pending_checkpoint = HitlCheckpoint(
                stage=WorkflowStage.PLANNING,
                title="Review the plan",
                summary="A plan is ready.",
                result_ref="plan",
            )
            svc_a.save(fresh)
            # The stream for a LIVE run stays open by design, which a plain
            # request would wait on forever. Publishing the run's end first
            # exercises the same catch-up path and lets it close — which is
            # also exactly what a tab attaching to an already-finished run
            # must see.
            broker.publish(fresh.project_id, agui.run_finished(fresh.project_id, "r"))
            r2 = client.get(f"/api/v1/projects/{fresh.project_id}/agui", headers=a)
            parked = events_from(r2.text)
            kinds = [p["type"] for p in parked]
            names = [p.get("name") for p in parked]
            print("   snapshot then hitl :",
                  ok(kinds[0] == "STATE_SNAPSHOT" and "detroit.hitl" in names))
            print("   awaiting id present:",
                  ok(parked[0]["snapshot"]["awaitingCheckpointId"]
                     == fresh.pending_checkpoint.checkpoint_id))
            print("   closes once ended  :", ok(kinds[-1] == "RUN_FINISHED"))

            print()
            print("=== 4. Replay lets a late tab catch up ===")
            broker.publish(pid, agui.activity("planning"))
            replayed = [e.type.value for e in broker.replay(pid)]
            print("   buffered for replay:", ok("CUSTOM" in replayed))
            broker.clear(pid)
            print("   clear empties it   :", ok(broker.replay(pid) == []))

            print()
            print("=== 5. No credential appears in the stream ===")
            body = json.dumps(parked)
            print("   no bearer in body  :", ok("Bearer" not in body))
            print("   no token key       :", ok("access_token" not in body))

            svc_a.delete(pid)
            svc_a.delete(fresh.project_id)
    finally:
        cleanup()

    passed, total = sum(_results), len(_results)
    print()
    print(f"=== {passed}/{total} assertions passed ===")
    sys.exit(0 if passed == total else 1)


main()
