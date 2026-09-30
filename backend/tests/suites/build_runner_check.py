"""BuildRunner: real toolchain, real exit codes, isolation and sanitization.

This suite genuinely runs npm/tsc/vite. It is the slowest in the set and that is
the point: the previous template bug (TS6310) passed every assertion and only
failed when the compiler ran.
"""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"
os.environ["SECRET_CANARY"] = "do-not-leak-me"

from backend.tools.build_runner import (BuildRunner, BUILD_STEPS, MAX_OUTPUT_CHARS,
                                        sanitize_output, _subprocess_env,
                                        _resolve_launcher)
from backend.tools.template import scaffold_workspace

print("=== 1. Only allowlisted commands exist, as argument LISTS ===")
names = [n for n, _ in BUILD_STEPS]
print("   steps        :", names, ok(names == ["install", "typecheck", "build"]))
expected = [
    ("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"),
    ("npx", "tsc", "--noEmit"),
    ("npx", "vite", "build"),
]
print("   exact commands:", ok([c for _, c in BUILD_STEPS] == expected))
print("   all are tuples, not strings:",
      ok(all(isinstance(c, tuple) for _, c in BUILD_STEPS)))
print("   --ignore-scripts on install:",
      ok("--ignore-scripts" in dict(BUILD_STEPS)["install"]))
src = open("backend/tools/build_runner.py", encoding="utf-8").read()
# Only line 292 should carry a shell kwarg, and it must be False. Searching the
# raw text matches the docstring and the comment that explain why True is banned,
# so strip comment lines and docstring prose first.
code = [l for l in src.splitlines()
        if not l.strip().startswith(('#', '*', '``', '"""'))]
shell_kwargs = [l.strip() for l in code if 'shell=' in l]
print("   shell kwargs in code :", shell_kwargs)
print("   only shell=False     :", ok(shell_kwargs == ['shell=False,']))
print("   no f-string/join command building:",
      ok(".join(command" not in src and 'f"npm' not in src))

print()
print("=== 1b. Launcher resolves to a real binary, never a shell shim ===")
for tool in ("npm", "npx"):
    argv = _resolve_launcher(tool)
    print("   %-4s -> %s" % (tool, [Path(a).name for a in argv]))
    print("        no .cmd/.bat shim:",
          ok(not any(str(a).lower().endswith((".cmd", ".bat")) for a in argv)))
    print("        first element exists on disk:", ok(Path(argv[0]).is_file()))

print()
print("=== 2. Builder secrets are NOT inherited ===")
env = _subprocess_env()
print("   ANTHROPIC_API_KEY dropped:", ok("ANTHROPIC_API_KEY" not in env))
print("   SECRET_CANARY dropped    :", ok("SECRET_CANARY" not in env))
print("   PATH kept (node findable):", ok("PATH" in env))
print("   CI=1 set                 :", ok(env.get("CI") == "1"))
print("   env is small             :", len(env), ok(len(env) < 20))

print()
print("=== 3. Output sanitization ===")
ws = Path(r"C:\Users\Admin\Desktop\Loveable_Clone\var\projects\p1\workspace")
raw = ("error at %s\\src\\App.tsx\n"
       "cache C:\\Users\\Admin\\AppData\\npm\n"
       "also /home/someone/.cache\n") % ws
clean = sanitize_output(raw, ws)
print("   workspace path redacted:", ok(str(ws) not in clean and "<workspace>" in clean))
print("   home dir redacted      :", ok("Admin" not in clean and "someone" not in clean))
print("   <home> marker present  :", ok("<home>" in clean))
long_text = "x" * (MAX_OUTPUT_CHARS * 3)
t = sanitize_output(long_text, ws)
print("   truncated to the TAIL  :", ok(len(t) <= MAX_OUTPUT_CHARS + 20
                                        and t.startswith("…truncated…")))
print("   empty stays empty      :", ok(sanitize_output("", ws) == ""))

print()
print("=== 4. Refuses an unscaffolded workspace before spawning anything ===")
empty = Path(tempfile.mkdtemp(prefix="br_empty_"))
r = BuildRunner(timeout_seconds=60).run(empty)
print("   ok False        :", ok(not r.ok))
print("   failed_step     :", r.failed_step, ok(r.failed_step == "scaffold"))
print("   no steps ran    :", ok(r.steps == ()))
print("   message is safe :", repr(r.message), ok(os.sep not in r.message))

print()
print("=== 5. REAL BUILD of a scaffolded workspace ===")
ws1 = Path(tempfile.mkdtemp(prefix="br_good_"))
scaffold_workspace(ws1)
start = time.time()
r1 = BuildRunner(timeout_seconds=600).run(ws1)
print("   elapsed        : %.0fs" % (time.time() - start))
print("   ok             :", ok(r1.ok))
print("   failed_step    :", r1.failed_step, ok(r1.failed_step is None))
print("   all 3 steps ran:", [s.step for s in r1.steps], ok(len(r1.steps) == 3))
print("   every exit 0   :", ok(all(s.exit_code == 0 for s in r1.steps)))
print("   dist produced  :", ok(r1.dist_dir is not None and r1.dist_dir.is_dir()))
print("   dist/index.html:", ok((ws1 / "dist" / "index.html").is_file()))
print("   hashed assets  :", ok(any((ws1 / "dist" / "assets").glob("*.js"))))
print("   tailwind css   :", ok(any((ws1 / "dist" / "assets").glob("*.css"))))
print("   no path leaked in output:",
      ok(all(str(ws1) not in s.output for s in r1.steps)))

print()
print("=== 6. A TYPE ERROR fails the run at typecheck, before vite ===")
(ws1 / "src" / "App.tsx").write_text(
    'export default function App() {\n'
    '  const n: number = "not a number";\n'
    '  return <p>{n}</p>;\n'
    '}\n', encoding="utf-8")
r2 = BuildRunner(timeout_seconds=600).run(ws1)
print("   ok False       :", ok(not r2.ok))
print("   failed_step    :", r2.failed_step, ok(r2.failed_step == "typecheck"))
print("   stopped early  :", [s.step for s in r2.steps], ok(len(r2.steps) == 2))
print("   vite NOT run   :", ok("build" not in [s.step for s in r2.steps]))
print("   non-zero exit  :", r2.steps[-1].exit_code, ok(r2.steps[-1].exit_code != 0))
print("   error text kept:", ok("TS2322" in r2.steps[-1].output
                                or "not assignable" in r2.steps[-1].output))
print("   message safe   :", repr(r2.message), ok(os.sep not in r2.message))

print()
print("=== 7. A BUILD error is reported as build, not typecheck ===")
# Must be something tsc cannot see. A missing module fails at TYPECHECK (tsc
# resolves imports), so that is the wrong probe -- my first attempt used it and
# was caught here. Invalid CSS is right: tsc never parses stylesheets, so this
# survives the type check and dies inside Tailwind/PostCSS during vite build.
(ws1 / "src" / "App.tsx").write_text(
    "export default function App() {\n  return <p>ok</p>;\n}\n", encoding="utf-8")
(ws1 / "src" / "main.tsx").write_text(
    # No `import React`: the template sets noUnusedLocals and uses the automatic
    # JSX runtime, so an unused React import is itself a type error.
    'import ReactDOM from "react-dom/client";\n'
    'import App from "./App";\n'
    'import "./index.css";\n'
    'ReactDOM.createRoot(document.getElementById("root")!).render(<App />);\n',
    encoding="utf-8")
(ws1 / "src" / "index.css").write_text(
    "@tailwind base;\n.x { @apply this-utility-does-not-exist; }\n",
    encoding="utf-8")
r3 = BuildRunner(timeout_seconds=600).run(ws1)
print("   ok False     :", ok(not r3.ok))
print("   failed_step  :", r3.failed_step, ok(r3.failed_step == "build"))
print("   typecheck PASSED first:",
      ok([s.step for s in r3.steps][:2] == ["install", "typecheck"]
         and r3.steps[1].exit_code == 0))
print("   all 3 ran    :", ok(len(r3.steps) == 3))
print("   exit non-zero:", ok(r3.steps[-1].exit_code != 0))
print("   message names build:", repr(r3.message), ok("build" in r3.message))

print()
print("=== 8. Timeout is bounded and reported, not hung ===")
ws2 = Path(tempfile.mkdtemp(prefix="br_slow_"))
scaffold_workspace(ws2)
start = time.time()
r4 = BuildRunner(timeout_seconds=1).run(ws2)   # npm install cannot finish in 1s
elapsed = time.time() - start
print("   returned in %.1fs" % elapsed, ok(elapsed < 60))
print("   ok False      :", ok(not r4.ok))
print("   failed_step   :", r4.failed_step, ok(r4.failed_step == "install"))
print("   timed_out flag:", ok(r4.steps[-1].timed_out))
print("   says timed out:", repr(r4.message), ok("timed out" in r4.message))

print()
print("=== 9. Failures are returned, never raised ===")
print("   no exception escaped any of the above:", ok(True))
print("   StepResult.ok is exit-code based:",
      ok(r1.steps[0].ok and not r2.steps[-1].ok))
