"""Preview serving: containment, headers, content types. Adversarial. No network."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
ok = lambda b: "PASS" if b else "**FAIL**"
os.environ["ANTHROPIC_API_KEY"] = "test-only-not-a-real-key"

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api import preview as preview_api
from backend.api.preview import resolve_preview_file, _CONTENT_TYPES, _CSP
from backend.memory.artifacts import JsonArtifactRepository
from backend.services.projects import ProjectService
from backend.config.settings import get_settings
from backend.schemas.state import ProjectState
from backend.tests.suites import _tmpdir

root = Path(_tmpdir.mkdtemp(prefix="pv_"))
repo = JsonArtifactRepository(root)
svc = ProjectService(repository=repo, workflow=object(), settings=get_settings())
st = svc.create(prompt="coffee shop")
PID = st.project_id

ws = repo.workspace_dir(PID)
dist = ws / "dist"
(dist / "assets").mkdir(parents=True)
(dist / "index.html").write_text("<h1>Coffee</h1>", encoding="utf-8")
(dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
(dist / "assets" / "app.css").write_text("body{}", encoding="utf-8")
(dist / "danger.exe").write_bytes(b"MZ")
(dist / "notes.txt").write_text("plain", encoding="utf-8")

# A secret OUTSIDE the workspace: the thing traversal would be trying to reach.
SECRET = root / "SECRET.env"
SECRET.write_text("ANTHROPIC_API_KEY=real-key", encoding="utf-8")
(ws / "package.json").write_text('{"name":"x"}', encoding="utf-8")

print("=== 1. Normal files resolve ===")
for rel, expect in [("", "index.html"), ("index.html", "index.html"),
                    ("assets/app.js", "app.js"), ("assets/app.css", "app.css")]:
    got = resolve_preview_file(dist, rel)
    print("   %-16s -> %-12s %s" % (repr(rel), got.name, ok(got.name == expect)))

print()
print("=== 2. TRAVERSAL: nothing escapes dist/ ===")
attacks = [
    "../package.json", "../../SECRET.env", "../../../SECRET.env",
    "..\\..\\SECRET.env", "assets/../../package.json",
    "assets/../../../SECRET.env", "./../../SECRET.env",
    "%2e%2e/SECRET.env", "....//SECRET.env",
    "/etc/passwd", "C:/Windows/System32/drivers/etc/hosts",
    str(SECRET), "assets/./../../SECRET.env",
]
leaked = []
for a in attacks:
    try:
        got = resolve_preview_file(dist, a)
        leaked.append((a, str(got)))
    except HTTPException as e:
        if e.status_code != 404:
            leaked.append((a, "wrong status %s" % e.status_code))
print("   attacks tried :", len(attacks))
print("   any escaped   :", leaked or "none", ok(not leaked))

print()
print("=== 3. Unlisted content types are refused, not guessed ===")
for rel in ["danger.exe", "notes.txt"]:
    try:
        resolve_preview_file(dist, rel)
        print("   %-12s SERVED **FAIL**" % rel)
    except HTTPException as e:
        print("   %-12s refused %s %s" % (rel, e.status_code, ok(e.status_code == 404)))
print("   .exe not in the type table:", ok(".exe" not in _CONTENT_TYPES))

print()
print("=== 4. Missing files 404, indistinguishable from refusal ===")
for rel in ["nope.js", "assets/nope.css", "deep/nope.html"]:
    try:
        resolve_preview_file(dist, rel)
        print("   %-18s SERVED **FAIL**" % rel)
    except HTTPException as e:
        print("   %-18s 404 %s" % (rel, ok(e.status_code == 404)))

print()
print("=== 5. CSP is restrictive in the ways that matter ===")
for directive, why in [("default-src 'none'", "deny by default"),
                       ("script-src 'self'", "no inline script"),
                       ("connect-src 'none'", "no exfiltration"),
                       ("form-action 'none'", "no posting out"),
                       ("base-uri 'none'", "no base hijack"),
                       ("frame-ancestors 'self'", "embeddable by us only")]:
    print("   %-26s %-24s %s" % (directive, why, ok(directive in _CSP)))
print("   script 'unsafe-inline' NOT granted:",
      ok("script-src 'self'" in _CSP and "script-src 'self' 'unsafe-inline'" not in _CSP))

print()
print("=== 6. Over HTTP, with the real app's error envelope ===")
from backend.main import create_app
client = TestClient(create_app(), raise_server_exceptions=False)
# Routes now require a verified caller; stub identity only.
from backend.tests.suites._auth_stub import authenticate
authenticate(client.app)
client.__enter__()
client.app.state.project_service = svc

r = client.get("/preview/%s/" % PID)
print("   index 200      :", r.status_code, ok(r.status_code == 200))
print("   body is ours   :", ok("Coffee" in r.text))
print("   nosniff        :", r.headers.get("x-content-type-options"),
      ok(r.headers.get("x-content-type-options") == "nosniff"))
print("   CSP present    :", ok("default-src 'none'" in r.headers.get("content-security-policy", "")))
print("   no-store       :", ok(r.headers.get("cache-control") == "no-store"))
print("   referrer policy:", ok(r.headers.get("referrer-policy") == "no-referrer"))
print("   content-type   :", r.headers.get("content-type"),
      ok("text/html" in r.headers.get("content-type", "")))

r = client.get("/preview/%s/assets/app.js" % PID)
print("   js served      :", r.status_code, ok(r.status_code == 200))
print("   js content-type:", r.headers.get("content-type"),
      ok("javascript" in r.headers.get("content-type", "")))
print("   headers on assets too:", ok(r.headers.get("x-content-type-options") == "nosniff"))

print()
print("=== 7. Over HTTP: traversal and unknown projects all 404 ===")
for path in ["/preview/%s/../../SECRET.env" % PID,
             "/preview/%s/..%%2f..%%2fSECRET.env" % PID,
             "/preview/%s/danger.exe" % PID,
             "/preview/proj_%s/index.html" % ("0" * 32),
             "/preview/not-an-id/index.html"]:
    rr = client.get(path)
    body_leaks = "ANTHROPIC" in rr.text or "real-key" in rr.text
    print("   %-46s %s leak=%s %s" % (
        path[:46], rr.status_code, body_leaks,
        ok(rr.status_code == 404 and not body_leaks)))

print()
print("=== 8. Unbuilt project serves nothing ===")
st2 = svc.create(prompt="never built")
r = client.get("/preview/%s/" % st2.project_id)
print("   404 when no dist/:", r.status_code, ok(r.status_code == 404))

print()
print("=== 9. Read-only: no write verbs exist ===")
methods = set()
for rt in preview_api.router.routes:
    methods |= {m for m in getattr(rt, "methods", set()) if m != "HEAD"}
print("   methods declared:", sorted(methods), ok(methods == {"GET"}))
for verb in ("post", "put", "delete", "patch"):
    rr = getattr(client, verb)("/preview/%s/index.html" % PID)
    print("   %-6s rejected: %s %s" % (verb.upper(), rr.status_code,
                                       ok(rr.status_code in (404, 405))))

print()
print("=== 10. GET /api/v1/projects/{id}/preview ===")
from datetime import datetime, timezone
from backend.schemas.state import PreviewStatus

P = "/api/v1/projects"

# (a) Nothing built yet -> 409 with the documented code.
r = client.get("%s/%s/preview" % (P, st2.project_id))
print("   409 when unbuilt :", r.status_code, ok(r.status_code == 409))
code = r.json()["error"]["code"]
print("   code             :", code, ok(code == "preview_not_available"))
print("   not 404          :", ok(r.status_code != 404), "(project exists; 'not yet')")
print("   distinct from the other 409s:", ok(code != "invalid_stage_for_action"))

# (b) After the preview node has run -> 200 with the real values.
built = datetime.now(timezone.utc)
s = svc.get(PID)
s.preview_status = PreviewStatus(
    preview_url="/preview/%s/" % PID, built_at=built, status="fresh")
svc._repository.save(s)
r = client.get("%s/%s/preview" % (P, PID))
print("   200 once built   :", r.status_code, ok(r.status_code == 200))
body = r.json()
print("   preview_url      :", body["preview_url"],
      ok(body["preview_url"] == "/preview/%s/" % PID))
print("   status fresh     :", body["status"], ok(body["status"] == "fresh"))
print("   built_at returned:", ok(body.get("built_at") is not None))
print("   no extra fields  :", ok(set(body) == {"preview_url", "built_at", "status"}))

# (c) The URL it reports actually serves.
r2 = client.get(body["preview_url"])
print("   reported URL serves:", r2.status_code, ok(r2.status_code == 200))

# (d) stale is reported, not hidden.
s.preview_status = PreviewStatus(
    preview_url="/preview/%s/" % PID, built_at=built, status="stale")
svc._repository.save(s)
print("   stale surfaced   :",
      ok(client.get("%s/%s/preview" % (P, PID)).json()["status"] == "stale"))

# (e) Unknown and malformed ids stay 404.
for bad in ["proj_" + "0" * 32, "../../etc"]:
    rr = client.get("%s/%s/preview" % (P, bad))
    print("   %-18s -> %s %s" % (bad[:18], rr.status_code, ok(rr.status_code == 404)))

print()
print("=== 11. Existing routes still behave ===")
for path, expect in [("%s/%s/status" % (P, PID), 200),
                     ("%s/%s" % (P, PID), 200),
                     ("%s/%s/hitl" % (P, PID), 204),
                     ("%s/%s/hitl/history" % (P, PID), 200)]:
    rr = client.get(path)
    print("   %-44s %s %s" % (path[-44:], rr.status_code, ok(rr.status_code == expect)))
