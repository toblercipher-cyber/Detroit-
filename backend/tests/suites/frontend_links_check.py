"""Internal links must point at routes that actually render.

WHY THIS SUITE EXISTS
    The Dashboard linked to ``/pricing`` from two places -- the Billing nav
    item and the Plan card's "Change" link. ``/pricing`` is NOT built: it is
    absent from the surface table in ``App.tsx``, so it falls through to the
    Landing page. Both links looked like navigation and silently delivered the
    marketing homepage instead.

    Nothing could have caught that. ``npm run build`` type-checks components;
    it has no opinion about the contents of an ``href`` string, and the project
    has no frontend test runner (``docs/frontend.md`` 6). So the check lives
    here, where the only executable test harness in the repository is.

WHY A PYTHON SUITE TESTS TYPESCRIPT
    Precedent: ``doc_check.py`` already validates a Markdown document against
    the Python models. The rule being enforced is not a TypeScript rule, it is
    a project rule -- "do not link to a surface that does not exist" -- and it
    is enforceable by reading the same two files a reviewer would read.

WHAT IT DOES NOT DO
    It does not fetch anything, render anything, or judge whether a link is
    the RIGHT destination. It only refuses links to destinations that cannot
    render. External links and in-page anchors are out of scope.

NO NETWORK.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True

_results: list[bool] = []


def ok(b: bool) -> str:
    _results.append(bool(b))
    return "PASS" if b else "**FAIL**"


SRC = Path("frontend/src")
APP = SRC / "App.tsx"

print("=== 1. The surface table is readable ===")
app_source = APP.read_text(encoding="utf-8")
table = re.search(r"const SURFACES[^=]*=\s*\{(.*?)\n\};", app_source, re.S)
print("   SURFACES found:", ok(table is not None))
routes = set(re.findall(r'"(/[a-z0-9\-/]*)"\s*:', table.group(1) if table else ""))
# "/" is the Landing fallback: any path not in the table renders it, and the
# table deliberately does not list it.
routes.add("/")
print("   routes:", sorted(routes), ok(len(routes) >= 4))

print()
print("=== 2. No internal link targets an unbuilt route ===")
# Only absolute internal links. "#anchor" is in-page, "http..." is external,
# and a template literal is a computed link this cannot resolve statically.
LINK = re.compile(r'href=(?:"(/[^"<>{}]*)"|\{`(/[^`]*)`\})')
offenders: list[str] = []
checked = 0
for path in sorted(SRC.rglob("*.tsx")):
    for m in LINK.finditer(path.read_text(encoding="utf-8")):
        raw = m.group(1) or m.group(2)
        if raw.startswith("${"):
            continue
        checked += 1
        # Split off a fragment and a query: the ROUTE is what must exist.
        route = raw.split("#")[0].split("?")[0].rstrip("/") or "/"
        route = route.split("${")[0] if "${" in route else route
        if route not in routes:
            offenders.append(f"{path.as_posix()} -> {raw}")

print(f"   internal links checked: {checked}")
for bad in offenders:
    print("   UNBUILT ROUTE:", bad)
print("   none point at an unbuilt route:", ok(not offenders))

print()
print("=== 3. /pricing specifically is gone ===")
# The exact regression: both Dashboard links used to go here.
pricing_links = [
    f"{p.as_posix()}"
    for p in SRC.rglob("*.tsx")
    if 'href="/pricing"' in p.read_text(encoding="utf-8")
]
print("   files still linking to /pricing:", pricing_links, ok(not pricing_links))

print()
print("=== 4. Their replacement actually resolves ===")
dash = (SRC / "pages" / "dashboard.tsx").read_text(encoding="utf-8")
hits = dash.count('href="/features#pricing"') + dash.count('href: "/features#pricing"')
print("   dashboard links to /features#pricing twice:", hits, ok(hits == 2))
print("   /features is a real surface            :", ok("/features" in routes))

tiers = (SRC / "components" / "pricing-tiers.tsx").read_text(encoding="utf-8")
print('   the #pricing anchor exists             :', ok('id="pricing"' in tiers))

features = (SRC / "pages" / "features.tsx").read_text(encoding="utf-8")
print("   /features renders PricingTiers         :", ok("<PricingTiers />" in features))

passed, total = sum(_results), len(_results)
print()
print(f"=== {passed}/{total} assertions passed ===")
sys.exit(0 if passed == total else 1)
