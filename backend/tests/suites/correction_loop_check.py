"""Targeted Fix, Re-evaluation, the max-3 budget, and Final Validation.

The assertion that matters most here is that the loop STOPS. A correction loop
that cannot exhaust its budget is the runaway docs/workflow.md 4 exists to
prevent, and it is cheap to get wrong in a way no single-pass test notices.
"""
import os, sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from backend.graph.workflow import (
    targeted_fix_node, re_evaluation_node, final_validation_node,
    final_validation_gate_node, route_after_evaluation, route_after_preview,
    build_workflow, WorkflowDependencies,
    NODE_TARGETED_FIX, NODE_RE_EVALUATION, NODE_FINAL_VALIDATION,
    NODE_FINAL_GATE, NODE_BUILD, NODE_PREVIEW, NODE_EVALUATION,
)
from backend.context.builder import ContextBuilder, RenderedPreviewPayload
from backend.memory.artifacts import JsonArtifactRepository
from backend.tools.build_runner import BuildRunner
from backend.tools.impeccable import ImpeccableStatus
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.agents.builder import BuilderAgent
from backend.agents.fixer import FixerAgent
from backend.agents.visual_ux_critic import VisualUxCriticAgent
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent
from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
from backend.agents.code_reviewer import CodeReviewerAgent
from backend.schemas.enums import RunStatus, WorkflowStage, Severity, AgentRole, HitlAction
from backend.schemas.state import (ProjectState, EvaluationReport, EvaluationFinding,
                                   ReviewFindings, PreviewStatus, FileRef)

SHOT = __import__("base64").b64encode(
    bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"p" * 300).decode()


def finding(fid="f1", sev=Severity.MAJOR):
    return EvaluationFinding(finding_id=fid, source=AgentRole.ANTI_SLOP_REVIEWER,
                             category="anti-slop", severity=sev, summary="too many gradients")


def report(status, findings=None, iteration=0):
    return EvaluationReport(iteration=iteration, impeccable_available=True,
                            impeccable_version="4.0.0", gate_status=status,
                            findings=findings if findings is not None else [])


print("=== 1. route_after_evaluation: all four verdicts ===")
def st_with(status, iters, findings=None):
    s = ProjectState(user_request="x")
    s.evaluation_results = report(status, findings or [finding()], iters)
    s.iteration_count = iters
    return s

cases = [
    ("passed, 0 iters", "passed", 0, "validate"),
    ("passed, 3 iters", "passed", 3, "validate"),
    ("blocked",         "blocked", 0, "stop"),
    ("failed, 0 iters", "failed", 0, "fix"),
    ("failed, 1 iter",  "failed", 1, "fix"),
    ("failed, 2 iters", "failed", 2, "fix"),
    ("failed, 3 iters", "failed", 3, "exhausted"),
    ("failed, 4 iters", "failed", 4, "exhausted"),
]
for label, status, iters, want in cases:
    got = route_after_evaluation(st_with(status, iters), 3)
    print("   %-16s -> %-10s %s" % (label, got, ok(got == want)))
no_report = ProjectState(user_request="x")
print("   no report at all -> stop:", ok(route_after_evaluation(no_report, 3) == "stop"))
print("   blocked NEVER routes to validate:",
      ok(route_after_evaluation(st_with("blocked", 0), 3) != "validate"))

print()
print("=== 2. The budget is configuration, not a literal ===")
print("   limit 1: failed@1 -> exhausted:",
      ok(route_after_evaluation(st_with("failed", 1), 1) == "exhausted"))
print("   limit 5: failed@4 -> fix       :",
      ok(route_after_evaluation(st_with("failed", 4), 5) == "fix"))

print()
print("=== 3. route_after_preview distinguishes first pass from re-check ===")
s0 = ProjectState(user_request="x")
print("   0 iterations -> evaluate :", ok(route_after_preview(s0) == "evaluate"))
s0.iteration_count = 1
print("   1 iteration  -> re_evaluate:", ok(route_after_preview(s0) == "re_evaluate"))


class FixStub(Runnable):
    """A Fixer model that writes one file, or fails on demand."""
    def __init__(self, fail=False): self.fail, self.calls = fail, 0
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def bind_tools(self, tools):
        outer = self
        class _T(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                outer.calls += 1
                if outer.fail:
                    raise RuntimeError("fixer exploded")
                if outer.calls == 1:
                    return AIMessage(content="", tool_calls=[{
                        "name": "write_workspace_file",
                        "args": {"path": "src/App.tsx",
                                 "content": "export default () => <p>fixed</p>;\n"},
                        "id": "c1", "type": "tool_call"}])
                return AIMessage(content="done")
        return _T()
    def with_structured_output(self, model):
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k): return model()
        return _S()


def make_deps(fix_stub, repo, max_iters=3):
    rs = FixStub()
    return WorkflowDependencies(
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(rs), planner=PlannerAgent(rs),
        design_director=DesignDirectorAgent(rs), reference_analyzer=ReferenceAnalyzerAgent(rs),
        frontend_architect=FrontendArchitectAgent(rs),
        build_builder=lambda w: BuilderAgent(rs, w),
        build_fixer=lambda w: FixerAgent(fix_stub, w),
        build_runner=BuildRunner(timeout_seconds=60),
        visual_ux_critic=VisualUxCriticAgent(rs), anti_slop_reviewer=AntiSlopReviewerAgent(rs),
        accessibility_reviewer=AccessibilityReviewerAgent(rs),
        code_reviewer=CodeReviewerAgent(rs),
        impeccable_probe=lambda: ImpeccableStatus(True, version="4.0.0"),
        max_auto_iterations=max_iters, repository=repo)


def fixable_state(repo):
    s = ProjectState(user_request="coffee shop")
    s.evaluation_results = report("failed", [finding()])
    ws = repo.workspace_dir(s.project_id)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "App.tsx").write_text("export default () => <p>old</p>;\n", encoding="utf-8")
    s.generated_files = [FileRef(path="src/App.tsx", size_bytes=1, sha256="a")]
    repo.save(s)
    return s


print()
print("=== 4. Targeted Fix: writes, counts, re-reads from disk ===")
repo = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="cl_")))
st = fixable_state(repo)
out = asyncio.run(targeted_fix_node(st, make_deps(FixStub(), repo)))
print("   stage TARGETED_FIX :", ok(out["current_stage"] is WorkflowStage.TARGETED_FIX))
print("   iteration 0 -> 1   :", out["iteration_count"], ok(out["iteration_count"] == 1))
ws = repo.workspace_dir(st.project_id)
print("   file really changed:",
      ok("fixed" in (ws / "src" / "App.tsx").read_text(encoding="utf-8")))
print("   manifest re-read from disk:",
      ok(any(f.path == "src/App.tsx" for f in out["generated_files"])))

print()
print("=== 5. A FAILED fix still consumes an attempt ===")
repo2 = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="cl2_")))
st2 = fixable_state(repo2)
st2.iteration_count = 1
out2 = asyncio.run(targeted_fix_node(st2, make_deps(FixStub(fail=True), repo2)))
print("   run_status FAILED  :", ok(out2["run_status"] is RunStatus.FAILED))
print("   counter STILL went up:", out2["iteration_count"], ok(out2["iteration_count"] == 2))
print("   -> a failing fixer cannot loop forever")

print()
print("=== 6. Fix with no findings refuses rather than inventing work ===")
repo3 = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="cl3_")))
st3 = fixable_state(repo3)
st3.evaluation_results = report("failed", [])
out3 = asyncio.run(targeted_fix_node(st3, make_deps(FixStub(), repo3)))
print("   refuses            :", ok(out3["run_status"] is RunStatus.FAILED))
print("   no iteration spent :", ok("iteration_count" not in out3))

print()
print("=== 7. Re-evaluation is the SAME gate under a different stage ===")
repo4 = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="cl4_")))
st4 = fixable_state(repo4)
st4.preview_status = PreviewStatus(preview_url="/preview/p/", status="fresh")
deps4 = make_deps(FixStub(), repo4)
# No detector wired -> must BLOCK, exactly as the first pass would.
out4 = asyncio.run(re_evaluation_node(st4, deps4))
print("   blocks like evaluation:",
      ok(out4["evaluation_results"].gate_status == "blocked"))
print("   stage is RE_EVALUATION:", out4["current_stage"].value,
      ok(out4["current_stage"] is WorkflowStage.RE_EVALUATION))
print("   NOT mislabelled as evaluation:",
      ok(out4["current_stage"] is not WorkflowStage.EVALUATION))

print()
print("=== 8. Final Validation refuses anything the gate did not pass ===")
s5 = ProjectState(user_request="x")
for label, rep in [("no report", None), ("blocked", report("blocked")),
                   ("failed", report("failed", [finding()]))]:
    s5.evaluation_results = rep
    o = final_validation_node(s5, deps4)
    print("   %-10s refused:" % label, ok(o["run_status"] is RunStatus.FAILED))
s5.evaluation_results = report("passed")
o = final_validation_node(s5, deps4)
print("   passed accepted  :", ok(o["current_stage"] is WorkflowStage.FINAL_VALIDATION))
print("   run_status RUNNING:", ok(o["run_status"] is RunStatus.RUNNING))

print()
print("=== 9. The final summary is built from FACTS, not a model's account ===")
s6 = ProjectState(user_request="x")
s6.evaluation_results = report("passed", [finding("a", Severity.INFO)])
s6.iteration_count = 2
s6.generated_files = [FileRef(path="src/a.tsx", size_bytes=1, sha256="x"),
                      FileRef(path="src/b.tsx", size_bytes=1, sha256="y")]
from langgraph.types import interrupt as _real_interrupt
import backend.graph.workflow as wf
seen = {}
wf.interrupt = lambda payload: (seen.update(payload), {"checkpoint_id": payload["checkpoint_id"],
                                                       "action": "approve"})[1]
try:
    final_validation_gate_node(s6, deps4)
finally:
    wf.interrupt = _real_interrupt
print("   summary:", repr(seen.get("summary")))
print("   counts the real files   :", ok("2 file(s)" in seen["summary"]))
print("   counts the real retries :", ok("2 automatic correction(s)" in seen["summary"]))
print("   reports the real verdict:", ok("passed" in seen["summary"]))
print("   counts the real findings:", ok("1 finding(s)" in seen["summary"]))
print("   stage/result_ref        :", seen["stage"], seen["result_ref"],
      ok(seen["stage"] == "final_validation" and seen["result_ref"] == "generated_files"))

print()
print("=== 10. Topology: the loop closes, and it terminates ===")
g = build_workflow(make_deps(FixStub(), repo))
nodes = sorted(n for n in g.get_graph().nodes if not n.startswith("__"))
edges = {(e.source, e.target) for e in g.get_graph().edges}
print("   15 nodes:", len(nodes), ok(len(nodes) == 15))
for a, b in [(NODE_TARGETED_FIX, NODE_BUILD), (NODE_BUILD, NODE_PREVIEW),
             (NODE_PREVIEW, NODE_EVALUATION), (NODE_PREVIEW, NODE_RE_EVALUATION),
             (NODE_EVALUATION, NODE_TARGETED_FIX), (NODE_RE_EVALUATION, NODE_TARGETED_FIX),
             (NODE_EVALUATION, NODE_FINAL_VALIDATION),
             (NODE_RE_EVALUATION, NODE_FINAL_VALIDATION),
             (NODE_FINAL_VALIDATION, NODE_FINAL_GATE)]:
    print("   %-24s -> %-22s %s" % (a, b, ok((a, b) in edges)))
print("   the loop really is a cycle:",
      ok((NODE_TARGETED_FIX, NODE_BUILD) in edges and (NODE_PREVIEW, NODE_RE_EVALUATION) in edges
         and (NODE_RE_EVALUATION, NODE_TARGETED_FIX) in edges))
print("   evaluation can reach END  :", ok((NODE_EVALUATION, "__end__") in edges))
print("   final gate can reach END  :", ok((NODE_FINAL_GATE, "__end__") in edges))
print("   external_action NOT claimed:", ok("external_action" not in nodes))

print()
print("=== 11. 11 of 12 stages now have nodes ===")
impl = set(nodes)
missing = [s.value for s in WorkflowStage
           if s.value not in impl and s is not WorkflowStage.EXTERNAL_ACTION]
print("   every stage but external_action:", missing or "none missing", ok(not missing))
