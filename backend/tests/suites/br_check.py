"""BuildResult verification. No network, no API key."""
import os, sys, re
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from pydantic import ValidationError
from backend.schemas.state import BuildResult

ok = lambda b: "PASS" if b else "**FAIL**"

print("=== 1. Fields match docs/agents.md 4.6 ===")
doc = open("docs/agents.md", encoding="utf-8").read().split("### 4.6 Builder")[1].split("### 4.7")[0]
docfields = set(re.findall(r"([a-z_]+)\[\]", doc.split("BuildResult {")[1].split("}")[0]))
have = set(BuildResult.model_fields)
print("   documented:", sorted(docfields))
print("   implemented:", sorted(have))
print("   missing    :", sorted(docfields - have) or "none", ok(not (docfields - have)))
print("   extra      :", sorted(have - docfields) or "none", ok(not (have - docfields)))

print("")
print("=== 2. Defaults: an empty report is valid (a step may change nothing) ===")
b = BuildResult()
print("   constructs empty:", ok(b.files_written == [] and b.deviations_from_plan == []))

print("")
print("=== 3. Legitimate workspace-relative paths accepted ===")
good = ["src/App.tsx", "src/components/ui/button.tsx", "src/assets/hero.jpg", "index.html"]
try:
    r = BuildResult(files_written=good, files_modified=["src/main.tsx"])
    print("   accepted:", ok(True), "|", len(r.files_written), "written")
except ValidationError as e:
    print("   REJECTED **FAIL**", e)

print("")
print("=== 4. Paths that could not have been written legitimately are rejected ===")
bad = [
    ("/etc/passwd", "absolute posix"),
    ("\\Windows\\System32", "absolute windows"),
    ("C:/Windows/system.ini", "drive letter"),
    ("../../../etc/passwd", "traversal"),
    ("src/../../secret", "embedded traversal"),
    ("src/..\\escape", "backslash traversal"),
    ("", "empty path"),
]
for path, label in bad:
    for field in ("files_written", "files_modified"):
        try:
            BuildResult(**{field: [path]})
            print("   %-22s in %-15s NOT REJECTED **FAIL**" % (label, field)); break
        except ValidationError:
            pass
    else:
        print("   %-22s rejected in both fields %s" % (label, ok(True)))

print("")
print("=== 5. A deep but legal path is still fine (not over-blocking) ===")
try:
    BuildResult(files_written=["src/components/sections/hero/parts/Title.tsx"])
    print("   deep relative path accepted:", ok(True))
except ValidationError as e:
    print("   over-blocked **FAIL**", e)

print("")
print("=== 6. Round-trips through JSON (artifact persistence) ===")
r = BuildResult(files_written=["src/App.tsx"], deviations_from_plan=["used a prop instead of a new component"])
back = BuildResult.model_validate_json(r.model_dump_json())
print("   round-trip equal:", ok(back == r))
print("   deviation preserved:", ok(back.deviations_from_plan == r.deviations_from_plan))
