"""Functional checks of previously-unverified logic. No network, no API calls."""
import os, sys, tempfile, shutil
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import ValidationError
from backend.schemas.state import (ProjectState, EvaluationReport, EvaluationFinding,
                                   NormalizedRequirements, Plan, ApprovedDecision, ComponentNode, ComponentTree)
from backend.schemas.enums import AgentRole, Severity, WorkflowStage, HitlAction
from backend.schemas.api import ProjectStateResponse, HitlDecisionRequest
from backend.context.builder import ContextBuilder, ContextPermissionError
from backend.memory.artifacts import JsonArtifactRepository
from backend.tests.suites import _tmpdir

ok = lambda b: "PASS" if b else "**FAIL**"

print("=== A. ProjectState ===")
s = ProjectState(user_request="Build a coffee shop landing page")
print("  ids formatted        :", ok(s.project_id.startswith("proj_") and len(s.project_id) == 37
                                     and s.session_id.startswith("sess_")))
s.node_scratch["secret_scratch"] = "must-not-persist"
dumped = s.model_dump_json()
print("  node_scratch excluded:", ok("must-not-persist" not in dumped and "node_scratch" not in dumped))

print()
print("=== B. Mandatory Impeccable gate invariant ===")
try:
    EvaluationReport(iteration=0, impeccable_available=False, gate_status="passed")
    print("  blocked gate reported as passed:", ok(False))
except ValidationError:
    print("  unavailable+passed REJECTED   :", ok(True))
try:
    EvaluationReport(iteration=0, impeccable_available=False, gate_status="blocked")
    print("  unavailable+blocked accepted  :", ok(True))
except ValidationError as e:
    print("  unavailable+blocked accepted  :", ok(False), e)
print("  available+passed accepted     :",
      ok(bool(EvaluationReport(iteration=1, impeccable_available=True, gate_status="passed"))))

print()
print("=== C. Self-referencing ComponentNode ===")
t = ComponentTree(root=[ComponentNode(name="Page", children=[ComponentNode(name="Hero")])])
print("  nested tree builds   :", ok(t.root[0].children[0].name == "Hero"))

print()
print("=== D. ContextBuilder allowlist ===")
cb = ContextBuilder()
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN, statement="Use Inter"))
s.plan = Plan(objective="ship", acceptance_criteria=["loads fast"])
s.normalized_requirements = NormalizedRequirements(goal="g", input_density="sparse")

ra = cb.build(s, AgentRole.REQUIREMENT_ANALYZER, task="normalize")
print("  RA gets user_request :", ok("user_request" in ra.state))
print("  RA constraints       :", ok(ra.constraints == ["Use Inter"]))
print("  approved_decisions NOT in state:", ok("approved_decisions" not in ra.state))

cr = cb.build(s, AgentRole.CODE_REVIEWER, task="review")
print("  CodeReviewer sees no state:", ok(cr.state == {}))
print("  CodeReviewer no user_request:", ok("user_request" not in cr.state))

vc = cb.build(s, AgentRole.VISUAL_UX_CRITIC, task="critique", rendered_preview="http://x")
print("  derived acceptance_criteria:", ok(vc.state.get("acceptance_criteria") == ["loads fast"]))
print("  VC never sees plan   :", ok("plan" not in vc.state))

try:
    cb.build(s, AgentRole.PLANNER, task="plan", findings=[])
    print("  unpermitted input refused:", ok(False))
except ContextPermissionError:
    print("  unpermitted input refused:", ok(True))

print()
print("=== E. ProjectStateResponse redaction ===")
resp = ProjectStateResponse.from_state(s).model_dump_json()
print("  raw prompt excluded  :", ok("coffee shop" not in resp))
print("  node_scratch excluded:", ok("must-not-persist" not in resp))

print()
print("=== F. HitlDecisionRequest payload rules ===")
cases = [
    (dict(checkpoint_id="c", action=HitlAction.APPROVE), True, "approve alone"),
    (dict(checkpoint_id="c", action=HitlAction.APPROVE, instruction="x"), False, "approve + instruction"),
    (dict(checkpoint_id="c", action=HitlAction.SELECT_OPTION), False, "select_option w/o option_id"),
    (dict(checkpoint_id="c", action=HitlAction.SELECT_OPTION, option_id="o1"), True, "select_option + option_id"),
    (dict(checkpoint_id="c", action=HitlAction.CUSTOM), False, "custom w/o instruction"),
    (dict(checkpoint_id="c", action=HitlAction.CUSTOM, instruction="do x"), True, "custom + instruction"),
    (dict(checkpoint_id="c", action=HitlAction.CUSTOM, instruction="x", option_id="o"), False, "custom + both"),
]
for kw, should_pass, label in cases:
    try:
        HitlDecisionRequest(**kw); got = True
    except ValidationError:
        got = False
    print("  %-32s %s" % (label, ok(got == should_pass)))

print()
print("=== G. Repository round-trip (temp dir) ===")
tmp = _tmpdir.mkdtemp(prefix="repo-test-")
try:
    repo = JsonArtifactRepository(__import__("pathlib").Path(tmp))
    repo.save(s)
    back = repo.load(s.project_id)
    print("  save + load          :", ok(back.project_id == s.project_id))
    print("  requirements survive :", ok(back.normalized_requirements.goal == "g"))
    print("  approved decision survives:", ok(back.approved_decisions[0].statement == "Use Inter"))
    print("  node_scratch NOT persisted:", ok(back.node_scratch == {}))
    print("  exists()             :", ok(repo.exists(s.project_id)))
    print("  list_project_ids()   :", ok(repo.list_project_ids() == [s.project_id]))
    files = sorted(p.name for p in (__import__("pathlib").Path(tmp) / "projects" / s.project_id).iterdir())
    print("  artifacts written    :", files)
    repo.delete(s.project_id)
    print("  delete()             :", ok(not repo.exists(s.project_id)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
