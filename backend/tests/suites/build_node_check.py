"""The Build node: scaffold -> Builder writes src/ -> real toolchain compiles it.

The stub model issues genuine write_workspace_file tool calls, and the REAL
BuildRunner compiles the result. So this proves the whole path: a file "written by
the model" actually builds.
"""
import os, sys, json, tempfile, asyncio
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from backend.graph.workflow import build_node, WorkflowDependencies, _workspace_manifest
from backend.agents.builder import BuilderAgent
from backend.agents.fixer import FixerAgent
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.context.builder import ContextBuilder
from backend.memory.artifacts import JsonArtifactRepository
from backend.tools.build_runner import BuildRunner
from backend.tools.impeccable import ImpeccableStatus
from backend.agents.visual_ux_critic import VisualUxCriticAgent
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent
from backend.agents.accessibility_reviewer import AccessibilityReviewerAgent
from backend.agents.code_reviewer import CodeReviewerAgent
from backend.tools.workspace import WorkspaceFiles
from backend.schemas.enums import RunStatus, WorkflowStage
from backend.schemas.state import (ProjectState, Plan, DesignSpec, ComponentTree,
                                   ComponentNode, FrontendArchitecture, BuildResult)

GOOD_APP = (
    "export default function App() {\n"
    '  return <main className="p-8"><h1>Coffee</h1></main>;\n'
    "}\n"
)
BAD_APP = (
    "export default function App() {\n"
    '  const n: number = "not a number";\n'
    "  return <main>{n}</main>;\n"
    "}\n"
)


def make_chat(app_source, extra_writes=()):
    """A model that issues one real write tool call, then stops."""
    class Chat(Runnable):
        def __init__(self, **kw): self.calls = 0
        def invoke(self, i, config=None, **k): raise NotImplementedError
        def bind_tools(self, tools):
            outer = self
            class _T(Runnable):
                def invoke(s, i, config=None, **k): raise NotImplementedError
                async def ainvoke(s, i, config=None, **k):
                    outer.calls += 1
                    if outer.calls == 1:
                        writes = [{"name": "write_workspace_file",
                                   "args": {"path": "src/App.tsx", "content": app_source},
                                   "id": "c0", "type": "tool_call"}]
                        for n, (p, c) in enumerate(extra_writes, start=1):
                            writes.append({"name": "write_workspace_file",
                                           "args": {"path": p, "content": c},
                                           "id": f"c{n}", "type": "tool_call"})
                        return AIMessage(content="", tool_calls=writes)
                    return AIMessage(content="done")
            return _T()
        def with_structured_output(self, model):
            class _S(Runnable):
                def invoke(s, i, config=None, **k): raise NotImplementedError
                async def ainvoke(s, i, config=None, **k): return model()
            return _S()
    return Chat()


def deps_for(chat, repo, timeout=600):
    stub = make_chat(GOOD_APP)
    return WorkflowDependencies(
        context_builder=ContextBuilder(),
        requirement_analyzer=RequirementAnalyzerAgent(stub),
        planner=PlannerAgent(stub),
        design_director=DesignDirectorAgent(stub),
        reference_analyzer=ReferenceAnalyzerAgent(stub),
        frontend_architect=FrontendArchitectAgent(stub),
        build_builder=lambda ws: BuilderAgent(chat, ws),
        build_fixer=lambda ws: FixerAgent(chat, ws),
        max_auto_iterations=3,
        build_runner=BuildRunner(timeout_seconds=timeout),
        visual_ux_critic=VisualUxCriticAgent(stub),
        anti_slop_reviewer=AntiSlopReviewerAgent(stub),
        accessibility_reviewer=AccessibilityReviewerAgent(stub),
        code_reviewer=CodeReviewerAgent(stub),
        impeccable_probe=lambda: ImpeccableStatus(False, reason='x'),
        repository=repo)


def ready_state(repo):
    st = ProjectState(user_request="a coffee shop landing page")
    st.plan = Plan(objective="a coffee shop landing page", pages=["/"])
    st.design_spec = DesignSpec(font_families=["Inter"], visual_tone="warm")
    st.frontend_architecture = FrontendArchitecture(
        pages=["/"], component_tree=ComponentTree(root=[ComponentNode(name="App")]))
    st.component_tree = st.frontend_architecture.component_tree
    repo.save(st)
    return st


print("=== 1. Happy path: model writes, real toolchain compiles ===")
from backend.tests.suites import _tmpdir
repo = JsonArtifactRepository(Path(_tmpdir.mkdtemp(prefix="bn_ok_")))
st = ready_state(repo)
chat = make_chat(GOOD_APP)
out = asyncio.run(build_node(st, deps_for(chat, repo)))
ws = repo.workspace_dir(st.project_id)

print("   run_status RUNNING:", out["run_status"].value,
      ok(out["run_status"] is RunStatus.RUNNING))
print("   stage BUILD       :", ok(out["current_stage"] is WorkflowStage.BUILD))
bs = out["build_status"]
print("   build succeeded   :", bs.status, ok(bs.status == "succeeded"))
print("   timestamps set    :", ok(bs.started_at is not None and bs.finished_at is not None))
print("   no error recorded :", ok(bs.error is None))
print("   dist/ produced    :", ok((ws / "dist" / "index.html").is_file()))

print()
print("=== 2. The model's file is really on disk and really compiled ===")
written = (ws / "src" / "App.tsx").read_text(encoding="utf-8")
print("   App.tsx is the model's:", ok(written == GOOD_APP))
bundle = list((ws / "dist" / "assets").glob("*.js"))
print("   bundle emitted        :", ok(len(bundle) == 1))
print("   model's text in bundle:", ok("Coffee" in bundle[0].read_text(encoding="utf-8")))

print()
print("=== 3. generated_files is observed from disk, not claimed ===")
gen = out["generated_files"]
paths = sorted(f.path for f in gen)
print("   paths:", paths)
print("   only src/ listed :", ok(all(p.startswith("src/") for p in gen and paths)))
print("   no node_modules  :", ok(not any("node_modules" in p for p in paths)))
print("   no dist          :", ok(not any(p.startswith("dist") for p in paths)))
print("   no builder config:", ok(not any("package.json" in p or "vite" in p for p in paths)))
app = next(f for f in gen if f.path == "src/App.tsx")
import hashlib
print("   size is real     :", ok(app.size_bytes == len(GOOD_APP.encode())))
print("   sha256 is real   :", ok(app.sha256 == hashlib.sha256(GOOD_APP.encode()).hexdigest()))

print()
print("=== 4. Builder-owned config was scaffolded, not left to the model ===")
for name in ("package.json", "vite.config.ts", "tailwind.config.ts", "tsconfig.json"):
    print("   %-20s present: %s" % (name, ok((ws / name).is_file())))
pkg = json.loads((ws / "package.json").read_text(encoding="utf-8"))
print("   deps come from the allowlist:", ok("react" in pkg["dependencies"]))

print()
print("=== 5. Scaffold overwrites a tampered config before building ===")
(ws / "vite.config.ts").write_text("// tampered by something\n", encoding="utf-8")
st2 = repo.load(st.project_id)
st2.frontend_architecture = st.frontend_architecture
asyncio.run(build_node(st2, deps_for(make_chat(GOOD_APP), repo)))
print("   tampered config restored:",
      ok("tampered" not in (ws / "vite.config.ts").read_text(encoding="utf-8")))

print()
print("=== 6. A type error fails the stage and is recorded, not raised ===")
repo2 = JsonArtifactRepository(Path(_tmpdir.mkdtemp(prefix="bn_bad_")))
stb = ready_state(repo2)
outb = asyncio.run(build_node(stb, deps_for(make_chat(BAD_APP), repo2)))
print("   run_status FAILED :", outb["run_status"].value,
      ok(outb["run_status"] is RunStatus.FAILED))
bsb = outb["build_status"]
print("   build_status failed:", bsb.status, ok(bsb.status == "failed"))
print("   step named        :", bsb.step, ok(bsb.step == "typecheck"))
print("   error code        :", bsb.error.code, ok(bsb.error.code == "build_failed"))
print("   message sanitized :", repr(bsb.error.message),
      ok(os.sep not in bsb.error.message and "Admin" not in bsb.error.message))
print("   files still recorded:", ok(len(outb["generated_files"]) > 0))
print("   last_error set    :", ok(outb.get("last_error") is not None))

print()
print("=== 7. Manifest helper ignores everything it should ===")
w = Path(_tmpdir.mkdtemp(prefix="bn_man_"))
(w / "src" / "nested").mkdir(parents=True)
(w / "src" / "a.tsx").write_text("a", encoding="utf-8")
(w / "src" / "nested" / "b.css").write_text("bb", encoding="utf-8")
(w / "package.json").write_text("{}", encoding="utf-8")
(w / "dist").mkdir(); (w / "dist" / "x.js").write_text("x", encoding="utf-8")
(w / "node_modules").mkdir(); (w / "node_modules" / "y.js").write_text("y", encoding="utf-8")
m = sorted(f.path for f in _workspace_manifest(w))
print("   manifest:", m, ok(m == ["src/a.tsx", "src/nested/b.css"]))
print("   posix separators:", ok(all("\\\\" not in p for p in m)))
print("   empty workspace -> []:", ok(_workspace_manifest(Path(_tmpdir.mkdtemp())) == []))

print()
print("=== 8. Preview node: publishes the built output ===")
from backend.graph.workflow import preview_node, NODE_PREVIEW, NODE_BUILD
from backend.schemas.state import PreviewStatus

# Reuses the workspace built in section 1, so dist/ is real.
st_ok = repo.load(st.project_id)
pv = preview_node(st_ok, deps_for(make_chat(GOOD_APP), repo))
print("   stage PREVIEW    :", ok(pv["current_stage"] is WorkflowStage.PREVIEW))
print("   run_status RUNNING:", ok(pv["run_status"] is RunStatus.RUNNING))
ps = pv["preview_status"]
print("   is a PreviewStatus:", ok(isinstance(ps, PreviewStatus)))
print("   url is relative  :", ps.preview_url,
      ok(ps.preview_url == "/preview/%s/" % st.project_id))
print("   no host baked in :", ok("http" not in ps.preview_url
                                  and "8000" not in ps.preview_url))
print("   built_at set     :", ok(ps.built_at is not None))
print("   status fresh     :", ps.status, ok(ps.status == "fresh"))
print("   last_error cleared:", ok(pv["last_error"] is None))

print()
print("=== 9. Preview refuses when there is no build output ===")
import shutil as _sh
repo3 = JsonArtifactRepository(Path(_tmpdir.mkdtemp(prefix="bn_nopv_")))
st3 = ready_state(repo3)
pv2 = preview_node(st3, deps_for(make_chat(GOOD_APP), repo3))
print("   no dist -> FAILED:", pv2["run_status"].value,
      ok(pv2["run_status"] is RunStatus.FAILED))
print("   no preview_status written:", ok("preview_status" not in pv2))
print("   error recorded   :", ok(pv2.get("last_error") is not None))

# dist/ present but empty: index.html is what a browser actually asks for.
ws3 = repo3.workspace_dir(st3.project_id)
(ws3 / "dist").mkdir(parents=True, exist_ok=True)
pv3 = preview_node(st3, deps_for(make_chat(GOOD_APP), repo3))
print("   dist without index.html -> FAILED:",
      ok(pv3["run_status"] is RunStatus.FAILED))

print()
print("=== 10. The URL the node writes is the one that actually serves ===")
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.services.projects import ProjectService
from backend.config.settings import get_settings
svc = ProjectService(repository=repo, workflow=object(), settings=get_settings())
client = TestClient(create_app(), raise_server_exceptions=False)
client.__enter__()
client.app.state.project_service = svc
r = client.get(ps.preview_url)
print("   GET", ps.preview_url, "->", r.status_code, ok(r.status_code == 200))
print("   serves the built page:", ok("<!doctype html" in r.text.lower()))
print("   security headers present:",
      ok(r.headers.get("x-content-type-options") == "nosniff"))

print()
print("=== 11. Graph runs build -> preview -> END ===")
from backend.graph.workflow import build_workflow
g = build_workflow(deps_for(make_chat(GOOD_APP), repo))
nodes = sorted(n for n in g.get_graph().nodes if not n.startswith("__"))
print("   nodes:", nodes)
print("   15 nodes      :", ok(len(nodes) == 15))
print("   preview present:", ok(NODE_PREVIEW in nodes))
edges = {(e.source, e.target) for e in g.get_graph().edges}
print("   build -> preview:", ok((NODE_BUILD, NODE_PREVIEW) in edges))
print("   build no longer -> END:", ok((NODE_BUILD, "__end__") not in edges))
print("   preview -> evaluation:",
      ok((NODE_PREVIEW, "evaluation") in edges))
print("   evaluation -> END :", ok(("evaluation", "__end__") in edges))
