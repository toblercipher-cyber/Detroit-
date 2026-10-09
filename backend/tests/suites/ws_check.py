"""Workspace tool verification — real filesystem, temp dir. No network, no API key."""
import os, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from backend.tools.workspace import (WorkspaceFiles, WorkspaceAccessError,
                                     build_workspace_tools)
from backend.tests.suites import _tmpdir

ok = lambda b: "PASS" if b else "**FAIL**"
root = Path(_tmpdir.mkdtemp(prefix="ws-test-"))
outside = Path(_tmpdir.mkdtemp(prefix="ws-outside-"))
try:
    # lay down a template exactly as docs/architecture.md 5.1 describes
    (root / "src" / "components").mkdir(parents=True)
    (root / "package.json").write_text('{"name":"t"}', encoding="utf-8")
    (root / "vite.config.ts").write_text("export default {}", encoding="utf-8")
    (root / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "tsconfig.app.json").write_text("{}", encoding="utf-8")
    (root / "src" / "App.tsx").write_text("export default function App(){}", encoding="utf-8")
    (outside / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")

    ws = WorkspaceFiles(root)

    print("=== 1. Legitimate writes under src/ ===")
    for p in ["src/App.tsx", "src/components/Hero.tsx", "src/assets/notes.txt",
              "src/a/b/c/Deep.tsx"]:
        try:
            r = ws.write_file(p, "x = 1\n")
            print("   %-28s written as %s %s" % (p, r, ok(r == p)))
        except WorkspaceAccessError as e:
            print("   %-28s REFUSED **FAIL** %s" % (p, e))

    print()
    print("=== 2. Builder-owned template files are NOT writable ===")
    for p in ["package.json", "vite.config.ts", "index.html", "tsconfig.json",
              "tsconfig.app.json", "tsconfig.node.json"]:
        try:
            ws.write_file(p, "pwned"); print("   %-22s WRITTEN **FAIL**" % p)
        except WorkspaceAccessError:
            print("   %-22s refused %s" % (p, ok(True)))
    print("   package.json unchanged on disk:",
          ok((root / "package.json").read_text(encoding="utf-8") == '{"name":"t"}'))

    print()
    print("=== 3. Writes outside src/ are refused ===")
    for p in ["public/evil.js", "README.md", "node_modules/x.js", ".env", "dist/index.html"]:
        try:
            ws.write_file(p, "x"); print("   %-24s WRITTEN **FAIL**" % p)
        except WorkspaceAccessError:
            print("   %-24s refused %s" % (p, ok(True)))

    print()
    print("=== 4. Escape attempts ===")
    attacks = ["../outside.txt", "src/../../escape.txt", "/etc/passwd",
               "C:/Windows/system.ini", "src\\..\\..\\escape.txt",
               "..\\..\\escape.txt", "", "   "]
    for p in attacks:
        try:
            ws.write_file(p, "x"); print("   %-24r WRITTEN **FAIL**" % p)
        except WorkspaceAccessError:
            print("   %-24r refused %s" % (p, ok(True)))
    print("   nothing created outside the root:",
          ok(list(outside.iterdir()) == [outside / "secret.txt"]))

    print()
    print("=== 5. Reads are workspace-wide but still contained ===")
    print("   reads template config:", ok(ws.read_file("vite.config.ts") == "export default {}"))
    print("   reads src file        :", ok("x = 1" in ws.read_file("src/App.tsx")))
    for p in ["../secret.txt", "/etc/passwd", "src/../../secret.txt"]:
        try:
            ws.read_file(p); print("   %-22r READ **FAIL**" % p)
        except WorkspaceAccessError:
            print("   %-22r refused %s" % (p, ok(True)))
    try:
        ws.read_file("src/missing.tsx"); print("   missing file READ **FAIL**")
    except WorkspaceAccessError as e:
        print("   missing file refused  :", ok("No such file" in str(e)))

    print()
    print("=== 6. Error messages do not leak the root ===")
    try:
        ws.write_file("../escape.txt", "x")
    except WorkspaceAccessError as e:
        print("   message:", e)
        print("   root absent from message:", ok(str(root) not in str(e)))

    print()
    print("=== 7. exists() never raises on a hostile path ===")
    print("   exists('src/App.tsx') :", ok(ws.exists("src/App.tsx")))
    print("   exists('../secret.txt'):", ok(ws.exists("../secret.txt") is False))

    print()
    print("=== 8. LangChain tools bound to this workspace ===")
    tools = build_workspace_tools(ws)
    print("   tool names:", [t.name for t in tools])
    print("   two tools :", ok(len(tools) == 2))
    w = [t for t in tools if t.name == "write_workspace_file"][0]
    r = [t for t in tools if t.name == "read_workspace_file"][0]
    print("   args schema hides the root:",
          ok(set(w.args_schema.model_fields) == {"path", "content"}))
    print("   write via tool:", w.invoke({"path": "src/Tool.tsx", "content": "ok\n"}))
    print("   read via tool :", r.invoke({"path": "src/Tool.tsx"}).strip())
    try:
        w.invoke({"path": "vite.config.ts", "content": "pwned"})
        print("   protected file via tool WRITTEN **FAIL**")
    except WorkspaceAccessError:
        print("   protected file via tool refused:", ok(True))
finally:
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(outside, ignore_errors=True)
print()
print("  (all writes happened in temp dirs; project tree untouched)")
