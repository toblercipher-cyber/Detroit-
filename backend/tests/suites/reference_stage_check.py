"""Reference Analysis: conditional skip, the node, its gate, the upload route.

The central claim under test is docs/workflow.md 1: the stage AND its gate exist
only when a reference image was supplied. No network; the model is stubbed.
"""
import os, sys, asyncio, base64, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from backend.api import projects as routes
from backend.config.settings import get_settings
from backend.context.builder import ContextBuilder
from backend.graph.workflow import (build_workflow, WorkflowDependencies,
                                    NODE_REFERENCE_ANALYSIS, NODE_REFERENCE_GATE,
                                    route_after_design_gate)
from backend.memory.artifacts import JsonArtifactRepository
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.services.projects import ProjectService
from backend.schemas.enums import HitlAction, RunStatus, WorkflowStage
from backend.schemas.api import HitlDecisionRequest
from backend.schemas.state import (ProjectState, NormalizedRequirements, Plan,
                                   DesignSpec, ReferenceAnalysis)

PNG = b"\x89PNG\r\n\x1a\n" + b"PIXELDATA" * 12
JPEG = b"\xff\xd8\xff\xe0" + b"jpegbytes" * 12
SVG = b"<svg><script>alert(1)</script></svg>"
N = {"ref": 0}
SEEN = {"prompt": None}


class FakeChat(Runnable):
    def __init__(self, **kw): pass
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        def make():
            if model is NormalizedRequirements:
                return NormalizedRequirements(goal="g", input_density="sparse")
            if model is Plan:
                return Plan(objective="a coffee shop landing page")
            if model is DesignSpec:
                return DesignSpec(font_families=["Inter"], visual_tone="warm")
            N["ref"] += 1
            # Real fields only: ReferenceAnalysis is extra="forbid" and has no
            # headline field, so a made-up one fails schema validation.
            return ReferenceAnalysis(composition="reading v%d" % N["ref"])
        outer = self
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                if model is ReferenceAnalysis:
                    SEEN["prompt"] = i.to_messages() if hasattr(i, "to_messages") else i
                return make()
        return _S()


def build(tmp=None):
    N["ref"] = 0
    SEEN["prompt"] = None
    repo = JsonArtifactRepository(Path(tmp or tempfile.mkdtemp(prefix="refstage_")))
    g = build_workflow(WorkflowDependencies(
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(FakeChat()),
        planner=PlannerAgent(FakeChat()),
        design_director=DesignDirectorAgent(FakeChat()),
        reference_analyzer=ReferenceAnalyzerAgent(FakeChat()),
        repository=repo), checkpointer=InMemorySaver())
    svc = ProjectService(repository=repo, workflow=g, settings=get_settings())
    return svc


def approve_to_design(svc, pid):
    """Run to the plan gate, approve, land on the design gate."""
    st = asyncio.run(svc.run_workflow(pid))
    st = svc.get(pid)
    asyncio.run(svc.submit_hitl_decision(pid, HitlDecisionRequest(
        checkpoint_id=st.pending_checkpoint.checkpoint_id, action=HitlAction.APPROVE)))
    return svc.get(pid)


print("=== 1. Topology includes the reference stage and its gate ===")
svc = build()
nodes = sorted(n for n in svc._workflow.get_graph().nodes if not n.startswith("__"))
print("   nodes:", nodes)
print("   7 nodes        :", ok(len(nodes) == 7))
print("   reference node :", ok(NODE_REFERENCE_ANALYSIS in nodes))
print("   reference gate :", ok(NODE_REFERENCE_GATE in nodes))

print()
print("=== 2. Router: skip only when there is no image ===")
s_none = ProjectState(user_request="x")
s_none.hitl_history.append.__self__  # no-op; keep state explicit below
from backend.schemas.state import HitlRecord
s_none.hitl_history.append(HitlRecord(checkpoint_id="ckpt_" + "0" * 32,
                                      stage=WorkflowStage.DESIGN,
                                      action=HitlAction.APPROVE))
print("   approved + no image -> skip_reference:",
      ok(route_after_design_gate(s_none) == "skip_reference"))
s_img = s_none.model_copy(deep=True)
from backend.schemas.state import ReferenceImageRef
s_img.reference_images.append(ReferenceImageRef(
    image_id="img_" + "ab" * 16, content_type="image/png", size_bytes=1, sha256="x"))
print("   approved + image    -> approved     :",
      ok(route_after_design_gate(s_img) == "approved"))
s_rev = s_img.model_copy(deep=True)
s_rev.hitl_history[-1] = HitlRecord(checkpoint_id="ckpt_" + "0" * 32,
                                    stage=WorkflowStage.DESIGN,
                                    action=HitlAction.REJECT)
print("   rejected + image    -> revise       :",
      ok(route_after_design_gate(s_rev) == "revise"))

print()
print("=== 3. NO image: design approval skips the stage entirely ===")
svc = build()
pid = svc.create(prompt="coffee shop").project_id
st = approve_to_design(svc, pid)
print("   at the design gate:", st.pending_checkpoint.stage.value,
      ok(st.pending_checkpoint.stage is WorkflowStage.DESIGN))
acc = asyncio.run(svc.submit_hitl_decision(pid, HitlDecisionRequest(
    checkpoint_id=st.pending_checkpoint.checkpoint_id, action=HitlAction.APPROVE)))
after = svc.get(pid)
print("   run ended, no new gate:", ok(after.pending_checkpoint is None))
print("   reference agent NOT called:", N["ref"], ok(N["ref"] == 0))
print("   reference_analysis stays None:", ok(after.reference_analysis is None))
print("   no reference gate in history:",
      ok(all(h.stage is not WorkflowStage.REFERENCE_ANALYSIS for h in after.hitl_history)))

print()
print("=== 4. WITH an image: the stage runs and its gate appears ===")
svc = build()
pid = svc.create(prompt="coffee shop").project_id
ref = svc.attach_reference_image(pid, PNG, "image/png")
st = approve_to_design(svc, pid)
asyncio.run(svc.submit_hitl_decision(pid, HitlDecisionRequest(
    checkpoint_id=st.pending_checkpoint.checkpoint_id, action=HitlAction.APPROVE)))
after = svc.get(pid)
print("   reference agent called:", N["ref"], ok(N["ref"] == 1))
print("   paused at a new gate  :", ok(after.pending_checkpoint is not None))
print("   gate stage            :", after.pending_checkpoint.stage.value,
      ok(after.pending_checkpoint.stage is WorkflowStage.REFERENCE_ANALYSIS))
print("   result_ref            :", after.pending_checkpoint.result_ref,
      ok(after.pending_checkpoint.result_ref == "reference_analysis"))
print("   analysis stored       :", ok(after.reference_analysis is not None))
print("   status awaiting_hitl  :", ok(after.run_status is RunStatus.AWAITING_HITL))

print()
print("=== 5. The actual PIXELS reached the agent, not the checksum ===")
msgs = SEEN["prompt"]
blocks = msgs[1].content if msgs else []
kinds = [b.get("type") for b in blocks] if isinstance(blocks, list) else ["text"]
b64 = base64.b64encode(PNG).decode("ascii")
joined = str(blocks)
print("   content block types :", kinds)
print("   an image block sent :", ok("image" in kinds))
print("   base64 is the REAL file:", ok(b64 in joined))
print("   sha256 NOT sent instead:", ok(ref.sha256 not in joined))
text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text") \
    if isinstance(blocks, list) else str(blocks)
print("   untrusted-content guard present:",
      ok("data" in text.lower() and "instruction" in text.lower()))

print()
print("=== 6. Reference gate: custom re-runs reference only ===")
cp = after.pending_checkpoint.checkpoint_id
asyncio.run(svc.submit_hitl_decision(pid, HitlDecisionRequest(
    checkpoint_id=cp, action=HitlAction.CUSTOM, instruction="focus on the nav")))
s6 = svc.get(pid)
print("   reference re-ran    :", N["ref"], ok(N["ref"] == 2))
print("   still at reference  :", s6.pending_checkpoint.stage.value,
      ok(s6.pending_checkpoint.stage is WorkflowStage.REFERENCE_ANALYSIS))
print("   analysis replaced   :", s6.reference_analysis.composition,
      ok(s6.reference_analysis.composition == "reading v2"))
print("   instruction reached the prompt:",
      ok("focus on the nav" in str(SEEN["prompt"])))

print()
print("=== 7. Reference approval ends the run (architecture not stubbed) ===")
asyncio.run(svc.submit_hitl_decision(pid, HitlDecisionRequest(
    checkpoint_id=s6.pending_checkpoint.checkpoint_id, action=HitlAction.APPROVE)))
s7 = svc.get(pid)
print("   no pending gate :", ok(s7.pending_checkpoint is None))
print("   three approvals :", len(s7.approved_decisions), ok(len(s7.approved_decisions) == 3))
print("   stages approved :", [d.stage.value for d in s7.approved_decisions],
      ok([d.stage.value for d in s7.approved_decisions]
         == ["planning", "design", "reference_analysis"]))

print()
print("=== 8. Storage naming: {image_id}.{ext} from the VERIFIED type ===")
d = svc._repository.reference_dir(pid)
names = sorted(p.name for p in d.iterdir())
print("   on disk        :", names, ok(names == [ref.image_id + ".png"]))
r2 = svc.attach_reference_image(pid, JPEG, "image/jpeg")
print("   jpeg -> .jpg   :", ok((d / (r2.image_id + ".jpg")).is_file()))
print("   loadable by id alone:", ok(svc._repository.load_reference_image(pid, r2.image_id)
                                     == JPEG))

print()
print("=== 9. Upload route over HTTP ===")
from backend.main import create_app
client = TestClient(create_app(), raise_server_exceptions=False)
client.__enter__()
svc2 = build()
client.app.state.project_service = svc2
P = "/api/v1/projects"
pid2 = svc2.create(prompt="x").project_id

r = client.post("%s/%s/reference-images" % (P, pid2),
                files={"file": ("photo.png", PNG, "image/png")})
print("   201 created    :", r.status_code, ok(r.status_code == 201))
body = r.json()
print("   server id      :", body["image_id"][:4], ok(body["image_id"].startswith("img_")))
print("   client filename not echoed:", ok("photo" not in r.text))
print("   sha256 returned:", ok(len(body["sha256"]) == 64))

r = client.post("%s/%s/reference-images" % (P, pid2),
                files={"file": ("x.png", SVG, "image/png")})
print("   415 on SVG-as-PNG:", r.status_code, ok(r.status_code == 415))
print("   code           :", r.json()["error"]["code"],
      ok(r.json()["error"]["code"] == "unsupported_media_type"))
print("   payload not echoed:", ok("script" not in r.text))

limit = get_settings().max_upload_bytes
big = b"\x89PNG\r\n\x1a\n" + b"x" * limit
r = client.post("%s/%s/reference-images" % (P, pid2),
                files={"file": ("big.png", big, "image/png")})
print("   413 oversized  :", r.status_code, ok(r.status_code == 413))
print("   code           :", r.json()["error"]["code"],
      ok(r.json()["error"]["code"] == "upload_too_large"))

r = client.post("%s/%s/reference-images" % (P, "proj_" + "0" * 32),
                files={"file": ("a.png", PNG, "image/png")})
print("   404 unknown project:", r.status_code, ok(r.status_code == 404))

print()
print("=== 10. Upload makes the stage reachable end to end over HTTP ===")
st = approve_to_design(svc2, pid2)
asyncio.run(svc2.submit_hitl_decision(pid2, HitlDecisionRequest(
    checkpoint_id=st.pending_checkpoint.checkpoint_id, action=HitlAction.APPROVE)))
g = client.get("%s/%s/hitl" % (P, pid2))
print("   gate served    :", g.status_code, ok(g.status_code == 200))
print("   it is the reference gate:", g.json()["stage"],
      ok(g.json()["stage"] == "reference_analysis"))
