"""Anti-Slop Reviewer context contract. No network, no API key."""
import os, sys
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from backend.context.builder import ContextBuilder, ContextInput, ContextPermissionError
from backend.schemas.enums import AgentRole, Severity
from backend.schemas.state import ProjectState, DesignSpec, EvaluationFinding, ApprovedDecision
from backend.schemas.enums import WorkflowStage

ok = lambda b: "PASS" if b else "**FAIL**"
cb = ContextBuilder()

print("=== 1. No new ContextInput was invented ===")
inputs = [i.value for i in ContextInput]
print("   inputs:", inputs)
print("   count still 7:", ok(len(inputs) == 7))
print("   no 'impeccable' input:", ok(not any("impeccable" in i for i in inputs)))

print()
print("=== 2. Anti-Slop now matches docs/agents.md 4.8 ===")
rt = sorted(i.value for i in cb.runtime_allowlist(AgentRole.ANTI_SLOP_REVIEWER))
st = sorted(cb.state_allowlist(AgentRole.ANTI_SLOP_REVIEWER))
print("   runtime:", rt, ok(rt == ["findings", "rendered_preview"]))
print("   state  :", st, ok(st == ["approved_decisions", "design_spec"]))
print("   doc grants rendered preview + Impeccable findings + design_spec + approved_decisions: satisfied",
      ok(set(rt) == {"rendered_preview", "findings"} and set(st) == {"design_spec", "approved_decisions"}))

print()
print("=== 3. Detector findings actually reach the agent ===")
s = ProjectState(user_request="SECRET-RAW")
s.design_spec = DesignSpec(font_families=["Inter"])
s.approved_decisions.append(ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN,
                                             statement="Gradients are intentional in the hero"))
detector = [EvaluationFinding(finding_id="imp-1", source=AgentRole.ANTI_SLOP_REVIEWER,
                              category="anti_slop", severity=Severity.MAJOR,
                              summary="gradient used decoratively")]
ctx = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="interpret",
               rendered_preview="http://localhost/preview/", findings=detector)
print("   findings in inputs :", ok("findings" in ctx.inputs))
print("   preview in inputs  :", ok("rendered_preview" in ctx.inputs))
print("   design_spec in slice:", ok("design_spec" in ctx.state))
print("   approved decision as constraint:", ctx.constraints, ok(len(ctx.constraints) == 1))
print("   raw prompt excluded:", ok("SECRET-RAW" not in str(ctx.state) + str(ctx.inputs)))

print()
print("=== 4. Least privilege unchanged for everyone else ===")
granted = [r.value for r in AgentRole if ContextInput.FINDINGS in cb.runtime_allowlist(r)]
print("   FINDINGS granted to:", granted, ok(set(granted) == {"anti_slop_reviewer", "fixer"}))
for role in (AgentRole.VISUAL_UX_CRITIC, AgentRole.ACCESSIBILITY_REVIEWER, AgentRole.CODE_REVIEWER):
    try:
        cb.build(s, role, task="x", findings=detector); print("   %-24s ACCEPTED **FAIL**" % role.value)
    except ContextPermissionError: print("   %-24s refused findings %s" % (role.value, ok(True)))
try:
    cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="x", changed_files=[])
    print("   anti_slop given changed_files ACCEPTED **FAIL**")
except ContextPermissionError:
    print("   anti_slop still refused changed_files:", ok(True))

print()
print("=== 5. Findings remain optional (gate blocked => no findings) ===")
ctx2 = cb.build(s, AgentRole.ANTI_SLOP_REVIEWER, task="x", rendered_preview="http://x")
print("   builds without findings:", ok("findings" not in ctx2.inputs))

print()
print("=== 6. Full allowlist snapshot ===")
for r in AgentRole:
    print("   %-24s state=%-52s runtime=%s" % (r.value,
          ",".join(sorted(cb.state_allowlist(r))) or "-",
          ",".join(sorted(i.value for i in cb.runtime_allowlist(r))) or "-"))
