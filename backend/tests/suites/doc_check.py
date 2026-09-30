"""Validate docs/agents.md against the implemented models. No network."""
import os, re, sys
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from backend.schemas.state import ReviewFindings, EvaluationReport, EvaluationFinding

ok = lambda b: "PASS" if b else "**FAIL**"
doc = open("docs/agents.md", encoding="utf-8").read()

print("=== 1. No reviewer still claims a bare EvaluationFinding[] output ===")
bad = [l.strip() for l in doc.splitlines()
       if l.strip().startswith("- **Output**") and "EvaluationFinding[]" in l]
print("   offending output lines:", bad or "NONE", ok(not bad))

print()
print("=== 2. All four reviewers declare ReviewFindings ===")
for sec, label in [("4.7 Visual & UX Critic", "visual_ux"), ("4.8 Anti-Slop Reviewer", "anti_slop"),
                   ("4.9 Accessibility Reviewer", "a11y"), ("4.10 Code Reviewer", "code")]:
    body = doc.split("### " + sec)[1].split("### 4.")[0]
    out = [l for l in body.splitlines() if l.strip().startswith("- **Output**")][0]
    print("   %-26s %s %s" % (label, "ReviewFindings" in out, ok("ReviewFindings" in out)))

print()
print("=== 3. Anti-Slop no longer asked to record gate fields ===")
anti = doc.split("### 4.8 Anti-Slop Reviewer")[1].split("### 4.9")[0]
resp = [l for l in anti.splitlines() if "**Responsibilities**" in l][0]
resp_block = anti.split("**Responsibilities**")[1].split("- **Allowed context**")[0]
for f in ("impeccable_available", "impeccable_version", "gate_status"):
    asked = f in resp_block
    print("   responsibilities mention %-22s %s" % (f, ok(not asked)))
print("   output says 'does **not** report':", ok("does **not** report" in anti))
print("   output names the forging risk    :", ok("forge the" in anti))

print()
print("=== 4. Gate ownership documented on EvaluationReport side ===")
print("   evaluation layer records them:", ok("evaluation layer probes Impeccable" in anti))
print("   invariant referenced          :", ok('gate_status: "blocked"' in anti))

print()
print("=== 5. MANDATORY gate language untouched ===")
print("   'mandatory, blocking' present :", ok("**mandatory, blocking**" in anti))
print("   substitute still prohibited   :", ok("running as a substitute for Impeccable" in anti))
print("   blocked-as-passed prohibited  :", ok("blocked or skipped check as completed or passed" in anti))
design = open("docs/design.md", encoding="utf-8").read()
print("   design.md 5 MANDATORY intact  :", ok("Impeccable is MANDATORY" in design))
print("   design.md blocked rule intact :", ok("impeccable_available: false" in design.lower() or
                                               "impeccable_available" in design))

print()
print("=== 6. Docs now match the code ===")
print("   ReviewFindings fields        :", sorted(ReviewFindings.model_fields),
      ok(set(ReviewFindings.model_fields) == {"findings"}))
print("   gate fields on EvaluationReport:",
      ok({"impeccable_available", "impeccable_version", "gate_status"} <= set(EvaluationReport.model_fields)))
print("   gate fields NOT on ReviewFindings:",
      ok(not ({"impeccable_available", "impeccable_version", "gate_status"} & set(ReviewFindings.model_fields))))

print()
print("=== 7. §3 contract rules explain the container once ===")
rules = doc.split("## 3. Contract Template")[1].split("---")[0]
print("   states 'bare list is not one'  :", ok("a bare list is not one" in rules))
print("   states source distinguishes    :", ok("`source` on each" in rules))
print("   states tooling facts not model :", ok("belong to the layer that ran it" in rules))
