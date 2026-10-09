"""HITL routes end to end over real HTTP (TestClient). No network, no .env."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver

from backend.api import projects as routes
from backend.schemas.state import NormalizedRequirements, Plan, DesignSpec
from backend.services.projects import ProjectService
from backend.memory.artifacts import JsonArtifactRepository
from backend.graph.workflow import build_workflow, WorkflowDependencies
from backend.context.builder import ContextBuilder
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.config.settings import get_settings

# --- WorkflowDependencies gained reference_analysis collaborators ---
# Self-contained: it must not depend on whatever stub shape this suite uses.
import tempfile as _tf
from pathlib import Path as _P
from langchain_core.runnables import Runnable as _R
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent as _RAA
from backend.agents.frontend_architect import FrontendArchitectAgent as _FAA
from backend.memory.artifacts import JsonArtifactRepository as _JAR
from backend.schemas.state import ReferenceAnalysis as _RA


class _RefStub(_R):
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        # Return the model that was ASKED for. Every field on these outputs is
        # optional, so an empty instance is valid -- and returning a fixed type
        # would hand one node another node's output.
        class _S(_R):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                return model()
        return _S()

    def bind_tools(self, tools):
        # The Builder is a tool-calling agent. This stub writes nothing and
        # returns immediately, which is enough for the topology tests here --
        # build_runner_check covers the real compile path.
        from langchain_core.messages import AIMessage
        class _T(_R):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                return AIMessage(content="no changes needed")
        return _T()


def _ref_deps():
    """Every collaborator WorkflowDependencies requires beyond the core three."""
    from backend.agents.builder import BuilderAgent as _BA
    from backend.agents.fixer import FixerAgent as _FX
    from backend.agents.visual_ux_critic import VisualUxCriticAgent as _VC
    from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent as _AS
    from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent as _AR
    from backend.agents.code_reviewer import CodeReviewerAgent as _CR
    from backend.tools.build_runner import BuildRunner as _BR
    from backend.tools.impeccable import ImpeccableStatus as _IS
    return dict(reference_analyzer=_RAA(_RefStub()),
                frontend_architect=_FAA(_RefStub()),
                build_builder=lambda ws: _BA(_RefStub(), ws),
                build_runner=_BR(timeout_seconds=600),
                visual_ux_critic=_VC(_RefStub()),
                anti_slop_reviewer=_AS(_RefStub()),
                accessibility_reviewer=_AR(_RefStub()),
                code_reviewer=_CR(_RefStub()),
                build_fixer=lambda ws: _FX(_RefStub(), ws),
                max_auto_iterations=3,
                # Blocked, which is the real state of this repository.
                impeccable_probe=lambda: _IS(False, reason="not installed"),
                repository=_JAR(_P(_tf.mkdtemp(prefix="wfdeps_"))))


N = {"d": 0}


class FakeChat(Runnable):
    def __init__(self, **kw): pass
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        def make():
            if model is NormalizedRequirements:
                return NormalizedRequirements(goal="g", input_density="sparse")
            if model is Plan:
                return Plan(objective="a coffee shop landing page")
            N["d"] += 1
            return DesignSpec(font_families=["Inter"], visual_tone="tone v%d" % N["d"])
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k): return make()
        return _S()


def make_client():
    """The REAL app (so main.py's error envelope applies), with a stub service.

    create_app's lifespan builds a live service first; it is swapped out after
    startup so no model is ever invoked.
    """
    N["d"] = 0
    g = build_workflow(WorkflowDependencies(**_ref_deps(), 
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(FakeChat()),
        planner=PlannerAgent(FakeChat()),
        design_director=DesignDirectorAgent(FakeChat())), checkpointer=InMemorySaver())
    svc = ProjectService(repository=JsonArtifactRepository(Path(tempfile.mkdtemp())),
                         workflow=g, settings=get_settings())
    from backend.main import create_app
    client = TestClient(create_app(), raise_server_exceptions=False)
    # Routes now require a verified caller; stub identity only.
    from backend.tests.suites._auth_stub import authenticate
    authenticate(client.app)
    client.__enter__()                       # run lifespan
    client.app.state.project_service = svc   # then take over
    return client, svc


client, svc = make_client()
P = "/api/v1/projects"

print("=== 1. All three routes are registered on the router ===")
registered = {(r.path, m) for r in routes.router.routes
              if hasattr(r, "methods") for m in r.methods if m != "HEAD"}
for path, method in [(P + "/{project_id}/hitl", "GET"),
                     (P + "/{project_id}/hitl", "POST"),
                     (P + "/{project_id}/hitl/history", "GET")]:
    print("   %-6s %-32s %s" % (method, path.split("projects")[1],
                                ok((path, method) in registered)))

print()
print("=== 2. POST creates a project; background task not auto-run ===")
r = client.post(P, json={"prompt": "build a coffee shop landing page"})
print("   201            :", r.status_code, ok(r.status_code == 201))
pid = r.json()["project_id"]

print()
print("=== 3. GET /hitl returns 204 when no gate is pending ===")
# Created through the service, NOT through POST: TestClient runs BackgroundTasks
# synchronously, so a project created over HTTP has already run and paused.
idle = svc.create(prompt="not started").project_id
r = client.get("%s/%s/hitl" % (P, idle))
print("   204 no content :", r.status_code, ok(r.status_code == 204))
print("   empty body     :", ok(r.content == b""))

print()
print("=== 4. Once paused, GET /hitl returns the gate ===")
import asyncio
st = svc.get(pid)
if st.pending_checkpoint is None:            # not already run by the background task
    st = asyncio.run(svc.run_workflow(pid))
r = client.get("%s/%s/hitl" % (P, pid))
print("   200            :", r.status_code, ok(r.status_code == 200))
body = r.json()
print("   stage          :", body["stage"], ok(body["stage"] == "planning"))
print("   result_ref     :", body["result_ref"], ok(body["result_ref"] == "plan"))
cp1 = body["checkpoint_id"]
print("   id matches state:", ok(cp1 == st.pending_checkpoint.checkpoint_id))

print()
print("=== 5. POST /hitl approve -> 202, advances to the design gate ===")
r = client.post("%s/%s/hitl" % (P, pid), json={"checkpoint_id": cp1, "action": "approve"})
print("   202 accepted   :", r.status_code, ok(r.status_code == 202))
b = r.json()
print("   accepted/resumed:", ok(b["accepted"] and b["resumed"]))
print("   stage answered :", b["stage"], ok(b["stage"] == "planning"))
print("   status         :", b["status"], ok(b["status"] == "awaiting_hitl"))
r = client.get("%s/%s/hitl" % (P, pid))
cp2 = r.json()["checkpoint_id"]
print("   next gate design:", r.json()["stage"], ok(r.json()["stage"] == "design"))

print()
print("=== 6. 410 on a stale checkpoint_id ===")
r = client.post("%s/%s/hitl" % (P, pid), json={"checkpoint_id": cp1, "action": "approve"})
print("   410 gone       :", r.status_code, ok(r.status_code == 410))
err = r.json()["error"]
print("   code           :", err["code"], ok(err["code"] == "checkpoint_already_resolved"))
print("   envelope shape :", ok(set(err) >= {"code", "message", "request_id"}))
print("   no stack trace :", ok("Traceback" not in r.text and "backend\\\\" not in r.text))

print()
print("=== 7. 422 on a malformed decision (model validator, before the service) ===")
for label, payload in [
        ("custom without instruction", {"checkpoint_id": cp2, "action": "custom"}),
        ("approve with instruction", {"checkpoint_id": cp2, "action": "approve",
                                      "instruction": "x"}),
        ("select_option without id", {"checkpoint_id": cp2, "action": "select_option"}),
        ("unknown action", {"checkpoint_id": cp2, "action": "banana"})]:
    rr = client.post("%s/%s/hitl" % (P, pid), json=payload)
    print("   %-28s %s %s" % (label, rr.status_code, ok(rr.status_code == 422)))
print("   submitted value not echoed:",
      ok("banana" not in client.post("%s/%s/hitl" % (P, pid),
                                     json={"checkpoint_id": cp2, "action": "banana"}).text))

print()
print("=== 8. custom via HTTP re-runs design only ===")
before = N["d"]
r = client.post("%s/%s/hitl" % (P, pid),
                json={"checkpoint_id": cp2, "action": "custom", "instruction": "warmer"})
print("   202            :", r.status_code, ok(r.status_code == 202))
print("   design re-ran  :", N["d"], ok(N["d"] == before + 1))
print("   still design   :", client.get("%s/%s/hitl" % (P, pid)).json()["stage"] == "design",
      ok(client.get("%s/%s/hitl" % (P, pid)).json()["stage"] == "design"))

print()
print("=== 9. GET /hitl/history is the audit trail, ids join the gates ===")
r = client.get("%s/%s/hitl/history" % (P, pid))
print("   200            :", r.status_code, ok(r.status_code == 200))
h = r.json()["history"]
print("   two records    :", len(h), ok(len(h) == 2))
print("   actions        :", [x["action"] for x in h], ok([x["action"] for x in h] == ["approve", "custom"]))
print("   1st cites gate1:", ok(h[0]["checkpoint_id"] == cp1))
print("   2nd cites gate2:", ok(h[1]["checkpoint_id"] == cp2))

print()
print("=== 10. 404 for unknown and traversal-shaped ids ===")
for label, bad in [("unknown id", "proj_" + "0" * 32), ("traversal", "..%2f..%2fetc")]:
    for path, method in [("/hitl", "get"), ("/hitl/history", "get")]:
        rr = getattr(client, method)("%s/%s%s" % (P, bad, path))
        print("   %-10s %-14s %s %s" % (label, path, rr.status_code, ok(rr.status_code == 404)))
rr = client.post("%s/%s/hitl" % (P, "proj_" + "0" * 32),
                 json={"checkpoint_id": "x", "action": "approve"})
print("   unknown id POST /hitl      :", rr.status_code, ok(rr.status_code == 404))

print()
print("=== 11. 409 when nothing is pending ===")
pid2 = svc.create(prompt="never started").project_id   # no gate ever raised
rr = client.post("%s/%s/hitl" % (P, pid2),
                 json={"checkpoint_id": "ckpt_" + "0" * 32, "action": "approve"})
print("   409 conflict   :", rr.status_code, ok(rr.status_code == 409))
code = rr.json()["error"]["code"]
print("   code per docs/api.md 412:", code, ok(code == "no_pending_checkpoint"))
print("   distinct from the other 409 cause:", ok(code != "invalid_stage_for_action"))
