"""ReviewFindings verification. No network, no API key."""
import os, sys, inspect
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import BaseModel, ValidationError
from langchain_anthropic import ChatAnthropic
from backend.schemas.state import ReviewFindings, EvaluationFinding, EvaluationReport
from backend.schemas.enums import AgentRole, Severity

ok = lambda b: "PASS" if b else "**FAIL**"

print("=== 1. Shape ===")
print("   fields         :", sorted(ReviewFindings.model_fields), ok(set(ReviewFindings.model_fields) == {"findings"}))
print("   BaseModel subclass:", ok(issubclass(ReviewFindings, BaseModel)))
print("   empty is valid :", ok(ReviewFindings().findings == []))

print()
print("=== 2. Usable as structured output (the reason it exists) ===")
m = ChatAnthropic(api_key="construction-only", model="claude-sonnet-5", temperature=0.0, max_tokens=100)
try:
    m.with_structured_output(ReviewFindings); print("   with_structured_output accepts it:", ok(True))
except Exception as e:
    print("   REJECTED **FAIL**", e)

print()
print("=== 3. Carries real findings from any reviewer ===")
rf = ReviewFindings(findings=[
    EvaluationFinding(finding_id="f1", source=AgentRole.ANTI_SLOP_REVIEWER, category="anti_slop",
                      severity=Severity.MAJOR, summary="gradient used decoratively"),
    EvaluationFinding(finding_id="f2", source=AgentRole.ACCESSIBILITY_REVIEWER, category="a11y",
                      severity=Severity.BLOCKER, summary="contrast below 4.5:1"),
    EvaluationFinding(finding_id="f3", source=AgentRole.CODE_REVIEWER, category="code",
                      severity=Severity.MINOR, summary="unused import"),
])
print("   holds mixed sources:", ok(len({f.source for f in rf.findings}) == 3))
print("   source distinguishes reviewers:", [f.source.value for f in rf.findings])
print("   round-trips JSON   :", ok(ReviewFindings.model_validate_json(rf.model_dump_json()) == rf))

print()
print("=== 4. The gate fields are NOT settable by a reviewer ===")
for field in ("impeccable_available", "impeccable_version", "gate_status"):
    print("   %-22s absent from ReviewFindings %s" % (field, ok(field not in ReviewFindings.model_fields)))
try:
    ReviewFindings(findings=[], impeccable_available=True)
    print("   extra gate field ACCEPTED **FAIL**")
except ValidationError:
    print("   model rejects an injected gate field:", ok(True))
print("   those fields live on EvaluationReport:",
      ok({"impeccable_available", "impeccable_version", "gate_status"} <= set(EvaluationReport.model_fields)))

print()
print("=== 5. EvaluationReport invariant still holds (gate stays unforgeable) ===")
try:
    EvaluationReport(iteration=0, impeccable_available=False, gate_status="passed")
    print("   blocked-gate-as-passed ACCEPTED **FAIL**")
except ValidationError:
    print("   blocked gate still cannot be reported as passed:", ok(True))

print()
print("=== 6. Findings compose into a report ===")
rep = EvaluationReport(iteration=1, impeccable_available=True, impeccable_version="1.2.3",
                       gate_status="failed", findings=rf.findings)
print("   report accepts reviewer findings:", ok(len(rep.findings) == 3))
