"""The mandatory Impeccable gate: blocks honestly, never passes on a substitute."""
import os, sys, json, tempfile, asyncio
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from langchain_core.runnables import Runnable
from backend.tools.impeccable import probe_impeccable, ImpeccableStatus, IMPECCABLE_DIR
from backend.graph.workflow import evaluation_node, WorkflowDependencies, NODE_EVALUATION
from backend.context.builder import ContextBuilder
from backend.memory.artifacts import JsonArtifactRepository
from backend.tools.build_runner import BuildRunner
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.agents.builder import BuilderAgent
from backend.agents.visual_ux_critic import VisualUxCriticAgent
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent
from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
from backend.agents.code_reviewer import CodeReviewerAgent
from backend.schemas.enums import RunStatus, WorkflowStage, Severity, AgentRole
from backend.schemas.state import (ProjectState, ReviewFindings, EvaluationFinding,
                                   PreviewStatus, FileRef, EvaluationReport)
from backend.context.builder import RenderedPreviewPayload
import base64

# PNG magic bytes, built without escapes so no shell layer can mangle them.
SHOT = base64.b64encode(bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b'p' * 400).decode()
DOM = '<main><h1>Coffee</h1><button>Order</button></main>'


def fake_capture(dist_dir, url):
    """Stands in for the headless browser that does not exist yet."""
    return RenderedPreviewPayload(url=url, dom_html=DOM,
                                  screenshot_base64=SHOT,
                                  screenshot_media_type='image/png')

CALLED = {"reviewers": 0}


def finding(fid, sev, source=AgentRole.VISUAL_UX_CRITIC):
    return EvaluationFinding(finding_id=fid, source=source, category="visual",
                             severity=sev, summary="s")


class Stub(Runnable):
    """Returns ReviewFindings with a configurable severity."""
    def __init__(self, severity=Severity.MINOR, boom=False):
        self.severity, self.boom = severity, boom
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        outer = self
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                CALLED["reviewers"] += 1
                if outer.boom:
                    raise RuntimeError("reviewer exploded")
                return ReviewFindings(findings=[finding("f", outer.severity)])
        return _S()


def make_deps(probe, detector=None, severity=Severity.MINOR, boom=False,
              repo=None, capture=fake_capture):
    repo = repo or JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="ev_")))
    st = Stub(severity, boom)
    return WorkflowDependencies(
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(st),
        planner=PlannerAgent(st), design_director=DesignDirectorAgent(st),
        reference_analyzer=ReferenceAnalyzerAgent(st),
        frontend_architect=FrontendArchitectAgent(st),
        build_builder=lambda ws: BuilderAgent(st, ws),
        build_runner=BuildRunner(timeout_seconds=60),
        visual_ux_critic=VisualUxCriticAgent(st),
        anti_slop_reviewer=AntiSlopReviewerAgent(st),
        accessibility_reviewer=AccessibilityReviewerAgent(st),
        code_reviewer=CodeReviewerAgent(st),
        impeccable_probe=probe, repository=repo,
        impeccable_detector=detector, preview_capture=capture), repo


def built_state(repo):
    st = ProjectState(user_request="x")
    st.preview_status = PreviewStatus(preview_url="/preview/p/", status="fresh")
    st.generated_files = [FileRef(path="src/App.tsx", size_bytes=1, sha256="a")]
    repo.save(st)
    return st


print("=== 1. Probe on the REAL repository ===")
root = Path(os.getcwd())
live = probe_impeccable(root)
print("   .impeccable/ exists:", (root / IMPECCABLE_DIR).is_dir())
print("   available reflects the filesystem:",
      ok(live.available == (root / IMPECCABLE_DIR).is_dir()))
if not live.available:
    print("   reason given       :", repr(live.reason), ok(bool(live.reason)))
    print("   version is None    :", ok(live.version is None))

print()
print("=== 2. Probe never invents availability or a version ===")
empty = Path(tempfile.mkdtemp(prefix="noimp_"))
s = probe_impeccable(empty)
print("   absent -> unavailable:", ok(not s.available and s.version is None))

present = Path(tempfile.mkdtemp(prefix="imp_"))
(present / IMPECCABLE_DIR).mkdir()
s2 = probe_impeccable(present)
print("   dir present -> available:", ok(s2.available))
print("   no version file -> None :", ok(s2.version is None), "(not guessed)")

(present / IMPECCABLE_DIR / "config.json").write_text('{"version":"1.4.2"}', encoding="utf-8")
print("   version READ from config:", probe_impeccable(present).version,
      ok(probe_impeccable(present).version == "1.4.2"))

(present / IMPECCABLE_DIR / "config.json").write_text("{not json", encoding="utf-8")
s3 = probe_impeccable(present)
print("   malformed config -> still available, version None:",
      ok(s3.available and s3.version is None))

print()
print("=== 3. THE RULING: unavailable -> blocked, never passed ===")
deps, repo = make_deps(lambda: ImpeccableStatus(False, reason="not installed"))
st = built_state(repo)
CALLED["reviewers"] = 0
out = asyncio.run(evaluation_node(st, deps))
rep = out["evaluation_results"]
print("   run_status BLOCKED  :", out["run_status"].value,
      ok(out["run_status"] is RunStatus.BLOCKED))
print("   not FAILED          :", ok(out["run_status"] is not RunStatus.FAILED),
      "(a missing tool is not broken code)")
print("   gate_status blocked :", rep.gate_status, ok(rep.gate_status == "blocked"))
print("   impeccable_available:", ok(rep.impeccable_available is False))
print("   error quality_gate_blocked:", out["last_error"].code,
      ok(out["last_error"].code == "quality_gate_blocked"))
print("   NO reviewer ran     :", CALLED["reviewers"],
      ok(CALLED["reviewers"] == 0), "(no agent-only substitute)")
print("   no findings claimed :", ok(rep.findings == []))

print()
print("=== 4. Installed but detector unwired -> still blocked ===")
deps, repo = make_deps(lambda: ImpeccableStatus(True, version="1.4.2"), detector=None)
st = built_state(repo)
CALLED["reviewers"] = 0
out = asyncio.run(evaluation_node(st, deps))
rep = out["evaluation_results"]
print("   still BLOCKED       :", ok(out["run_status"] is RunStatus.BLOCKED))
print("   gate blocked        :", ok(rep.gate_status == "blocked"))
print("   version still recorded:", rep.impeccable_version, ok(rep.impeccable_version == "1.4.2"))
print("   NO reviewer ran     :", ok(CALLED["reviewers"] == 0))

print()
print("=== 5. The model CANNOT express a passing blocked gate ===")
try:
    EvaluationReport(iteration=0, impeccable_available=False, gate_status="passed")
    print("   invariant NOT enforced **FAIL**")
except Exception as e:
    print("   unavailable + passed rejected:", ok(True), "(%s)" % type(e).__name__)

print()
print("=== 6. Available + detector -> all four reviewers run IN PARALLEL ===")
det = lambda ws: [finding("imp1", Severity.MINOR, AgentRole.ANTI_SLOP_REVIEWER)]
deps, repo = make_deps(lambda: ImpeccableStatus(True, version="1.4.2"), detector=det)
st = built_state(repo)
CALLED["reviewers"] = 0
out = asyncio.run(evaluation_node(st, deps))
rep = out["evaluation_results"]
print("   four reviewers ran  :", CALLED["reviewers"], ok(CALLED["reviewers"] == 4))
print("   gate passed         :", rep.gate_status, ok(rep.gate_status == "passed"))
print("   run_status RUNNING  :", ok(out["run_status"] is RunStatus.RUNNING))
print("   available True      :", ok(rep.impeccable_available is True))
print("   version recorded    :", ok(rep.impeccable_version == "1.4.2"))
print("   detector finding kept:", ok(any(f.finding_id == "imp1" for f in rep.findings)))
print("   reviewer findings merged:", len(rep.findings), ok(len(rep.findings) == 5),
      "(1 detector + 4 reviewers)")

print()
print("=== 7. A BLOCKER finding fails the gate ===")
deps, repo = make_deps(lambda: ImpeccableStatus(True, version="1.4.2"),
                       detector=det, severity=Severity.BLOCKER)
st = built_state(repo)
out = asyncio.run(evaluation_node(st, deps))
print("   gate failed         :", out["evaluation_results"].gate_status,
      ok(out["evaluation_results"].gate_status == "failed"))
print("   not blocked         :", ok(out["evaluation_results"].gate_status != "blocked"))

print()
print("=== 8. A crashing reviewer blocks; it is never silently dropped ===")
deps, repo = make_deps(lambda: ImpeccableStatus(True, version="1.4.2"),
                       detector=det, boom=True)
st = built_state(repo)
out = asyncio.run(evaluation_node(st, deps))
print("   BLOCKED not passed  :", out["evaluation_results"].gate_status,
      ok(out["evaluation_results"].gate_status == "blocked"))
print("   run_status BLOCKED  :", ok(out["run_status"] is RunStatus.BLOCKED))

print()
print("=== 9. A crashing DETECTOR blocks too ===")
def boom_det(ws): raise RuntimeError("detector exploded")
deps, repo = make_deps(lambda: ImpeccableStatus(True, version="1.4.2"), detector=boom_det)
st = built_state(repo)
out = asyncio.run(evaluation_node(st, deps))
print("   blocked             :", ok(out["evaluation_results"].gate_status == "blocked"))
print("   message sanitized   :", repr(out["last_error"].message),
      ok("exploded" not in out["last_error"].message))

print()
print("=== 10. Graph includes evaluation and terminates there ===")
from backend.graph.workflow import build_workflow, NODE_PREVIEW
deps, repo = make_deps(lambda: ImpeccableStatus(False, reason="x"))
g = build_workflow(deps)
nodes = sorted(n for n in g.get_graph().nodes if not n.startswith("__"))
edges = {(e.source, e.target) for e in g.get_graph().edges}
print("   11 nodes            :", len(nodes), ok(len(nodes) == 11))
print("   evaluation present  :", ok(NODE_EVALUATION in nodes))
print("   preview -> evaluation:", ok((NODE_PREVIEW, NODE_EVALUATION) in edges))
print("   evaluation -> END   :", ok((NODE_EVALUATION, "__end__") in edges))
print("   targeted_fix NOT claimed:", ok("targeted_fix" not in nodes))

print()
print("=== 11. No preview capture -> blocked, reviewers never run ===")
det2 = lambda ws: [finding("imp1", Severity.MINOR, AgentRole.ANTI_SLOP_REVIEWER)]
deps, repo = make_deps(lambda: ImpeccableStatus(True, version="1.4.2"),
                       detector=det2, capture=None)
st = built_state(repo)
CALLED["reviewers"] = 0
out = asyncio.run(evaluation_node(st, deps))
print("   blocked            :", out["evaluation_results"].gate_status,
      ok(out["evaluation_results"].gate_status == "blocked"))
print("   run_status BLOCKED :", ok(out["run_status"] is RunStatus.BLOCKED))
print("   NO reviewer ran    :", CALLED["reviewers"], ok(CALLED["reviewers"] == 0))
print("   reason names the preview:", repr(out["last_error"].message),
      ok("preview" in out["last_error"].message.lower()))

print()
print("=== 12. A URL alone can never be passed off as a rendered preview ===")
try:
    RenderedPreviewPayload(url="/preview/p/")
    print("   URL-only payload ACCEPTED **FAIL**")
except Exception as e:
    print("   URL-only payload rejected by the model:", ok(True))
