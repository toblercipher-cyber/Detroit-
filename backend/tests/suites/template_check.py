"""The fixed project template: allowlist integrity, isolation, real buildability."""
import os, sys, json, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from backend.tools.template import (ALLOWED_DEPENDENCIES, ALLOWED_DEV_DEPENDENCIES,
                                    BUILDER_OWNED_FILES, SEED_SOURCE_FILES,
                                    scaffold_workspace)
from backend.tools.workspace import WorkspaceFiles, WorkspaceAccessError, PROTECTED_NAMES

print("=== 1. package.json is generated FROM the allowlist ===")
pkg = json.loads(BUILDER_OWNED_FILES["package.json"])
print("   deps match allowlist    :", ok(pkg["dependencies"] == ALLOWED_DEPENDENCIES))
print("   devDeps match allowlist :", ok(pkg["devDependencies"] == ALLOWED_DEV_DEPENDENCIES))
print("   nothing outside it      :",
      ok(set(pkg["dependencies"]) | set(pkg["devDependencies"])
         == set(ALLOWED_DEPENDENCIES) | set(ALLOWED_DEV_DEPENDENCIES)))

print()
print("=== 2. Versions are exact, never ranges ===")
allv = {**ALLOWED_DEPENDENCIES, **ALLOWED_DEV_DEPENDENCIES}
bad = [f"{n}@{v}" for n, v in allv.items() if any(c in v for c in "^~><*x |")]
print("   ranges found:", bad or "none", ok(not bad))
print("   all look like semver:",
      ok(all(len(v.split(".")) == 3 and v.split(".")[0].isdigit() for v in allv.values())))

print()
print("=== 3. No lifecycle scripts (defence beyond --ignore-scripts) ===")
scripts = pkg.get("scripts", {})
lifecycle = {"preinstall", "install", "postinstall", "prepare", "prepublish"}
print("   scripts:", sorted(scripts))
print("   no lifecycle hooks:", ok(not (lifecycle & set(scripts))))
print("   private: true     :", ok(pkg.get("private") is True))

print()
print("=== 4. Everything the toolchain EXECUTES is builder-owned ===")
executed = ["vite.config.ts", "tailwind.config.ts", "postcss.config.js", "package.json"]
for name in executed:
    print("   %-22s in template: %s" % (name, ok(name in BUILDER_OWNED_FILES)))

print()
print("=== 5. The write tool refuses every builder-owned file ===")
tmp = Path(tempfile.mkdtemp(prefix="tmpl_"))
scaffold_workspace(tmp)
wf = WorkspaceFiles(tmp)
gaps = []
for name in BUILDER_OWNED_FILES:
    try:
        wf.write_file(name, "pwned")
        gaps.append(name)
    except WorkspaceAccessError:
        pass
print("   files the model could overwrite:", gaps or "none", ok(not gaps))
for rel in SEED_SOURCE_FILES:
    try:
        wf.write_file(rel, "// replaced by the model\n")
        print("   %-18s model-writable: %s" % (rel, ok(True)))
    except WorkspaceAccessError as e:
        print("   %-18s UNEXPECTEDLY BLOCKED: %s **FAIL**" % (rel, e))

print()
print("=== 6. Scaffold is idempotent; builder-owned wins, sources survive ===")
tmp2 = Path(tempfile.mkdtemp(prefix="tmpl2_"))
scaffold_workspace(tmp2)
(tmp2 / "src" / "App.tsx").write_text("export default () => <p>MINE</p>;\n", encoding="utf-8")
(tmp2 / "vite.config.ts").write_text("// tampered\n", encoding="utf-8")
scaffold_workspace(tmp2)
print("   tampered config restored:",
      ok("tampered" not in (tmp2 / "vite.config.ts").read_text(encoding="utf-8")))
print("   generated source kept   :",
      ok("MINE" in (tmp2 / "src" / "App.tsx").read_text(encoding="utf-8")))

print()
print("=== 7. Files land on disk where docs/architecture.md 5.1 says ===")
names = sorted(p.name for p in tmp.iterdir() if p.is_file())
print("   root files:", names)
print("   all 6 present:", ok(len(names) == 6))
print("   src/ created :", ok((tmp / "src").is_dir()))
print("   no dist yet  :", ok(not (tmp / "dist").exists()))

print()
print("=== 8. index.html entry matches the seeded source ===")
html = BUILDER_OWNED_FILES["index.html"]
print("   references /src/main.tsx:", ok("/src/main.tsx" in html))
print("   that file is seeded     :", ok("src/main.tsx" in SEED_SOURCE_FILES))
print("   root div present        :", ok('id="root"' in html))

print()
print("=== 9. tsconfig type-checks src only, emits nothing ===")
# No project reference: a composite referenced project may not set noEmit, so
# `tsc --noEmit` would fail TS6310 on every build. Verified against real tsc.
print("   no project reference:", ok("references" not in BUILDER_OWNED_FILES["tsconfig.json"]))
print("   no tsconfig.node.json:", ok("tsconfig.node.json" not in BUILDER_OWNED_FILES))
ts = json.loads(BUILDER_OWNED_FILES["tsconfig.json"])
print("   noEmit  :", ok(ts["compilerOptions"]["noEmit"] is True))
print("   strict  :", ok(ts["compilerOptions"]["strict"] is True))
print("   include :", ts["include"], ok(ts["include"] == ["src"]))

print()
print("=== 10. PROTECTED_NAMES covers the executable config ===")
# tsconfig* is handled separately by workspace.py, so it is excluded here.
need = {"package.json", "vite.config.ts", "index.html",
        "tailwind.config.ts", "postcss.config.js"}
missing = sorted(need - set(PROTECTED_NAMES))
print("   PROTECTED_NAMES:", sorted(PROTECTED_NAMES))
print("   not listed but executable:", missing or "none", ok(not missing))
