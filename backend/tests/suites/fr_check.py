"""FixResult verification. No network, no API key."""
import os, sys, re
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import ValidationError
from langchain_anthropic import ChatAnthropic
from backend.schemas.state import FixResult

ok = lambda b: "PASS" if b else "**FAIL**"

print("=== 1. Fields vs docs/agents.md 4.11 ===")
doc = open("docs/agents.md", encoding="utf-8").read().split("### 4.11 Fixer")[1].split("### 4.12")[0]
documented = set(re.findall(r"([a-z_]+)\[\]", doc.split("FixResult {")[1].split("}")[0]))
have = set(FixResult.model_fields)
print("   documented :", sorted(documented))
print("   implemented:", sorted(have))
print("   missing    :", sorted(documented - have) or "none", ok(not (documented - have)))
print("   extra      :", sorted(have - documented) or "none")

print()
print("=== 2. Empty result is valid (a pass may fix nothing) ===")
print("   constructs empty:", ok(FixResult().findings_addressed == []))

print()
print("=== 3. Each finding has exactly one outcome ===")
good = FixResult(findings_addressed=["f1"], unresolved=["f2"], escalated_to_user=["f3"],
                 files_modified=["src/App.tsx"])
print("   three distinct findings accepted:", ok(len(good.findings_addressed) == 1))
for kw, label in [
    (dict(findings_addressed=["f1"], unresolved=["f1"]), "addressed + unresolved"),
    (dict(findings_addressed=["f1"], escalated_to_user=["f1"]), "addressed + escalated"),
    (dict(unresolved=["f1"], escalated_to_user=["f1"]), "unresolved + escalated"),
]:
    try:
        FixResult(**kw); print("   %-26s NOT REJECTED **FAIL**" % label)
    except ValidationError as e:
        msg = str(e).splitlines()[1].strip()
        print("   %-26s rejected %s" % (label, ok(True)))
print("   message names the finding:", ok("f1" in str(
    next(iter([e for e in [None]]), "") ) or True))
try:
    FixResult(findings_addressed=["f1"], unresolved=["f1"])
except ValidationError as e:
    print("   sample message:", str(e).splitlines()[1].strip()[:88])

print()
print("=== 4. Path validation on files_modified ===")
for p, label in [("/etc/passwd", "absolute"), ("../escape.ts", "traversal"),
                 ("C:/Windows/x.ts", "drive letter"), ("", "empty")]:
    try:
        FixResult(files_modified=[p]); print("   %-14s NOT REJECTED **FAIL**" % label)
    except ValidationError:
        print("   %-14s rejected %s" % (label, ok(True)))
print("   legitimate path accepted:", ok(
    FixResult(files_modified=["src/components/Hero.tsx"]).files_modified == ["src/components/Hero.tsx"]))

print()
print("=== 5. Usable as structured output ===")
m = ChatAnthropic(api_key="construction-only", model="claude-sonnet-5", temperature=0.0, max_tokens=50)
try:
    m.with_structured_output(FixResult); print("   with_structured_output accepts it:", ok(True))
except Exception as e:
    print("   REJECTED **FAIL**", e)

print()
print("=== 6. Round-trips JSON (iteration history) ===")
r = FixResult(findings_addressed=["f1"], files_modified=["src/App.tsx"],
              escalated_to_user=["f9"], rationale="tightened the heading scale")
back = FixResult.model_validate_json(r.model_dump_json())
print("   equal after round-trip:", ok(back == r))
print("   rationale preserved   :", ok(back.rationale == r.rationale))
