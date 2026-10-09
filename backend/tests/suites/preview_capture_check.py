"""Preview capture: real Chromium, real build, post-render DOM, both viewports.

This suite really scaffolds a project, really builds it with vite, and really
renders it. The point of the capture is that it sees what React produced, and
only an actual browser can prove that.
"""
import os, sys, base64, tempfile, subprocess
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from backend.tools.preview_capture import (capture_preview, capture_viewports,
                                           PreviewCaptureError, DESKTOP, MOBILE,
                                           VIEWPORTS, _index_path)
from backend.tools.template import scaffold_workspace
from backend.tools.build_runner import BuildRunner
from backend.context.builder import RenderedPreviewPayload

print("=== 1. Entry point containment ===")
empty = Path(tempfile.mkdtemp(prefix="pc_empty_"))
try:
    _index_path(empty)
    print("   missing index.html accepted **FAIL**")
except PreviewCaptureError as e:
    print("   no index.html -> raises:", ok(True))
    print("   message says so   :", repr(str(e)), ok("index.html" in str(e)))

print()
print("=== 2. Build a REAL project to render ===")
ws = Path(tempfile.mkdtemp(prefix="pc_ws_"))
scaffold_workspace(ws)
# Content that only exists AFTER React runs. If the capture sees this, it
# captured the rendered DOM; if it sees only <div id="root">, it did not.
(ws / "src" / "App.tsx").write_text(
    "export default function App() {\n"
    "  return (\n"
    '    <main className="p-8">\n'
    '      <h1 className="text-3xl font-bold">RENDERED-BY-REACT</h1>\n'
    "      <p>A coffee shop landing page.</p>\n"
    '      <button type="button">Order now</button>\n'
    "    </main>\n"
    "  );\n"
    "}\n", encoding="utf-8")
result = BuildRunner(timeout_seconds=600).run(ws)
print("   build ok      :", ok(result.ok), "(failed_step=%s)" % result.failed_step)
dist = ws / "dist"
print("   dist/index.html:", ok((dist / "index.html").is_file()))
shipped = (dist / "index.html").read_text(encoding="utf-8")
print("   shipped HTML does NOT contain the heading:",
      ok("RENDERED-BY-REACT" not in shipped), "(it is an empty root div)")

print()
print("=== 3. Capture both viewports ===")
caps = capture_viewports(dist, "/preview/proj_x/")
print("   keys          :", sorted(caps), ok(set(caps) == {"desktop", "mobile"}))
print("   both are payloads:",
      ok(all(isinstance(v, RenderedPreviewPayload) for v in caps.values())))

print()
print("=== 4. The DOM is POST-RENDER, not the shipped shell ===")
d = caps["desktop"]
print("   heading present in captured DOM:", ok("RENDERED-BY-REACT" in d.dom_html))
print("   button present                 :", ok("Order now" in d.dom_html))
print("   not just the empty root div    :", ok(len(d.dom_html) > len(shipped)))
print("   captured %d chars vs %d shipped" % (len(d.dom_html), len(shipped)))

print()
print("=== 5. Screenshots are real PNGs, and the two viewports differ ===")
for name in ("desktop", "mobile"):
    raw = base64.b64decode(caps[name].screenshot_base64)
    print("   %-8s PNG magic :" % name, ok(raw[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])))
    print("   %-8s bytes     :" % name, len(raw), ok(len(raw) > 1000))
    print("   %-8s media type:" % name,
          ok(caps[name].screenshot_media_type == "image/png"))
print("   desktop != mobile image:",
      ok(caps["desktop"].screenshot_base64 != caps["mobile"].screenshot_base64),
      "(a resized page would often match)")

print()
print("=== 6. The viewport actually applied ===")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    sizes = {}
    for vp in VIEWPORTS:
        ctx = b.new_context(viewport={"width": vp.width, "height": vp.height},
                            is_mobile=vp.name == "mobile")
        pg = ctx.new_page(); pg.goto((dist / "index.html").as_uri())
        sizes[vp.name] = pg.evaluate("() => window.innerWidth")
        ctx.close()
    b.close()
print("   desktop innerWidth:", sizes["desktop"], ok(sizes["desktop"] == DESKTOP.width))
print("   mobile  innerWidth:", sizes["mobile"], ok(sizes["mobile"] == MOBILE.width))

print()
print("=== 7. url is RECORDED for humans; the local build is what renders ===")
c = capture_viewports(dist, "/preview/proj_zzz/")["desktop"]
print("   url recorded  :", c.url, ok(c.url == "/preview/proj_zzz/"))
print("   content is still the local build:", ok("RENDERED-BY-REACT" in c.dom_html))
unreachable = capture_viewports(dist, "http://127.0.0.1:1/nope")["desktop"]
print("   unreachable url did not break capture:",
      ok("RENDERED-BY-REACT" in unreachable.dom_html))

print()
print("=== 8. capture_preview matches the graph's seam ===")
one = capture_preview(dist, "/preview/p/")
print("   returns a single payload:", ok(isinstance(one, RenderedPreviewPayload)))
print("   it is the DESKTOP capture:",
      ok(one.dom_html == caps["desktop"].dom_html))
print("   payload passes its own validator:",
      ok(one.screenshot_base64 is not None and one.screenshot_media_type is not None))

print()
print("=== 9. A page that blocks on a dialog still captures ===")
(ws / "src" / "App.tsx").write_text(
    "export default function App() {\n"
    "  if (typeof window !== 'undefined') { window.alert('blocking'); }\n"
    '  return <h1>SURVIVED-THE-DIALOG</h1>;\n'
    "}\n", encoding="utf-8")
r2 = BuildRunner(timeout_seconds=600).run(ws)
if r2.ok:
    got = capture_viewports(dist, "/preview/p/")["desktop"]
    print("   dialog dismissed, capture completed:",
          ok("SURVIVED-THE-DIALOG" in got.dom_html))
else:
    print("   rebuild failed, skipped:", "**FAIL**")

print()
print("=== 10. Only documented Playwright APIs are used ===")
src = open("backend/tools/preview_capture.py", encoding="utf-8").read()
for api in ("sync_playwright", "chromium.launch", "new_context", "new_page",
            "page.goto", "page.content", "page.screenshot"):
    print("   uses %-20s %s" % (api, ok(api.split(".")[-1] in src)))
for banned in ("live-browser", "modern-screenshot", "impeccable", "_impl",
               "_channel", "cdp"):
    print("   avoids %-18s %s" % (banned, ok(banned not in src.lower())))

print()
print("=== 11. file:// would NOT have worked — this is why the server exists ===")
from playwright.sync_api import sync_playwright as _sp
errs = []
with _sp() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto((dist / "index.html").resolve().as_uri(), wait_until="networkidle")
    pg.wait_for_timeout(300)
    root_html = pg.evaluate("() => document.getElementById('root').innerHTML")
    b.close()
print("   over file:// #root is EMPTY   :", ok(root_html == ""))
print("   CORS error reported           :",
      ok(any("CORS" in e or "blocked" in e for e in errs)))
print("   -> the loopback server is load-bearing, not a preference")

print()
print("=== 12. The server is loopback-only and does not outlive the capture ===")
import socket, re as _re
from backend.tools.preview_capture import _serve
with _serve(dist) as origin:
    print("   origin           :", origin, ok(origin.startswith("http://127.0.0.1:")))
    port = int(_re.search(r":(\d+)/", origin).group(1))
    s_ = socket.socket(); s_.settimeout(2)
    print("   reachable while open:", ok(s_.connect_ex(("127.0.0.1", port)) == 0)); s_.close()
s_ = socket.socket(); s_.settimeout(2)
closed = s_.connect_ex(("127.0.0.1", port)) != 0
s_.close()
print("   closed after the block:", ok(closed))

print()
print("=== 13. main.py wires the REAL capture into the graph ===")
import backend.main as _m
from backend.config.settings import get_settings as _gs
from backend.tools.preview_capture import capture_preview as _cp
_caught = {}
_orig = _m.build_workflow
def _spy(deps, **kw):
    _caught["deps"] = deps
    return _orig(deps, **kw)
_m.build_workflow = _spy
from langgraph.checkpoint.memory import InMemorySaver
from backend.memory.artifacts import JsonArtifactRepository
_m.build_project_service(_gs(), InMemorySaver(),
                         JsonArtifactRepository(_gs().builder_data_dir))
_m.build_workflow = _orig
_d = _caught["deps"]
print("   preview_capture is capture_preview:", ok(_d.preview_capture is _cp))
print("   not None (would block)           :", ok(_d.preview_capture is not None))
_st = _d.impeccable_probe()
print("   all 3 gate preconditions met     :",
      ok(bool(_st.available and _d.impeccable_detector and _d.preview_capture)))

print()
print("=== 14. EVALUATION END TO END with the real capture ===")
import asyncio, json
from langchain_core.runnables import Runnable
from backend.graph.workflow import evaluation_node, WorkflowDependencies
from backend.context.builder import ContextBuilder
from backend.memory.artifacts import JsonArtifactRepository
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
from backend.tools.impeccable import probe_impeccable, run_detector
from backend.schemas.state import (ProjectState, ReviewFindings, PreviewStatus,
                                   FileRef)
from backend.schemas.enums import RunStatus

SAW = {"dom": None, "img": None, "n": 0}


class RevStub(Runnable):
    def invoke(self, i, config=None, **k): raise NotImplementedError
    def with_structured_output(self, model):
        class _S(Runnable):
            def invoke(s, i, config=None, **k): raise NotImplementedError
            async def ainvoke(s, i, config=None, **k):
                SAW["n"] += 1
                blob = str(i.to_messages() if hasattr(i, "to_messages") else i)
                if "RENDERED-BY-REACT" in blob or "SURVIVED" in blob:
                    SAW["dom"] = True
                if '"type": "image"' in blob or "'type': 'image'" in blob:
                    SAW["img"] = True
                return ReviewFindings(findings=[])
        return _S()


# Rebuild the known-good page so the DOM marker is present again.
(ws / "src" / "App.tsx").write_text(
    "export default function App() {\n"
    '  return <main className="p-8"><h1>RENDERED-BY-REACT</h1></main>;\n'
    "}\n", encoding="utf-8")
assert BuildRunner(timeout_seconds=600).run(ws).ok

repo = JsonArtifactRepository(Path(tempfile.mkdtemp(prefix="pc_eval_")))
st = ProjectState(user_request="coffee shop")
st.preview_status = PreviewStatus(preview_url="/preview/p/", status="fresh")
st.generated_files = [FileRef(path="src/App.tsx", size_bytes=1, sha256="a")]
repo.save(st)
# Point the graph's workspace at the project we really built.
repo.workspace_dir = lambda pid: ws

rs = RevStub()
deps = WorkflowDependencies(
    context_builder=ContextBuilder(),
    requirement_analyzer=RequirementAnalyzerAgent(rs), planner=PlannerAgent(rs),
    design_director=DesignDirectorAgent(rs), reference_analyzer=ReferenceAnalyzerAgent(rs),
    frontend_architect=FrontendArchitectAgent(rs),
    build_builder=lambda w_: BuilderAgent(rs, w_),
        build_fixer=lambda w_: FixerAgent(rs, w_),
        max_auto_iterations=3,
    build_runner=BuildRunner(timeout_seconds=600),
    visual_ux_critic=VisualUxCriticAgent(rs), anti_slop_reviewer=AntiSlopReviewerAgent(rs),
    accessibility_reviewer=AccessibilityReviewerAgent(rs), code_reviewer=CodeReviewerAgent(rs),
    impeccable_probe=probe_impeccable, repository=repo,
    impeccable_detector=run_detector, preview_capture=capture_preview)

out = asyncio.run(evaluation_node(st, deps))
rep = out["evaluation_results"]
print("   gate NOT blocked   :", rep.gate_status, ok(rep.gate_status != "blocked"))
print("   run_status RUNNING :", ok(out["run_status"] is RunStatus.RUNNING))
print("   impeccable version :", rep.impeccable_version, ok(bool(rep.impeccable_version)))
print("   all 4 reviewers ran:", SAW["n"], ok(SAW["n"] == 4))
print("   a reviewer saw the RENDERED DOM:", ok(SAW["dom"] is True))
print("   a reviewer saw a SCREENSHOT    :", ok(SAW["img"] is True))
