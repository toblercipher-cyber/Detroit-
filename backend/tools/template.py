"""The fixed, builder-owned project template every generated site starts from.

WHY THIS FILE EXISTS
    ``docs/architecture.md`` 5.2 calls the template constraint "the primary
    isolation mechanism", and it is the reason a build is safe to run at all.

    A build executes project code: ``vite.config.ts`` is a module Vite imports and
    runs, ``postcss.config.js`` and ``tailwind.config.ts` likewise, and npm
    lifecycle scripts run arbitrary commands. If a model could write any of those,
    "generate a website" would mean "execute code the model wrote on the builder's
    machine". Freezing them here removes that ability rather than trying to detect
    it afterwards.

    So: the model writes ``src/``. Everything that the toolchain *executes* comes
    from this file and is not model-writable (``backend/tools/workspace.py``
    enforces the write side).

WHY THE CONTENT LIVES IN PYTHON, NOT IN A TEMPLATE DIRECTORY
    Two reasons. The dependency allowlist and the ``package.json`` that must match
    it sit side by side here, so they cannot drift. And files on disk invite being
    edited per project, which is precisely the property the template must not have.

VERSIONS ARE EXACT, NOT CARETS
    ``docs/architecture.md`` 5.2: "The model cannot add, change or pin a
    dependency." A caret range means the resolved tree changes over time without
    anyone deciding it did, so a build that passed review could install different
    code later. Exact pins make the dependency set a reviewed decision.

    These versions are the ones the builder's own frontend already runs on, so they
    are known to work together rather than guessed.

ADDING A DEPENDENCY IS A HITL DECISION
    Not an autonomous action (``docs/architecture.md`` 5.2). Extending
    :data:`ALLOWED_DEPENDENCIES` is a deliberate change to this file, reviewed like
    any other.

LAYER
    Tools (``backend/tools/``). Pure content plus one filesystem function; it
    imports no agent, no state and no graph.

Spec: docs/architecture.md 5.1, 5.2, 5.3; docs/security.md 3; skills.md 22.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The complete set a generated site may depend on. Nothing outside this table can
# reach a workspace, because package.json is written from it and is not
# model-writable (docs/architecture.md 5.2).
#
# Deliberately small. Every entry is code that runs at build time or ships to the
# visitor, so each one is surface. The builder's own UI kit (framer-motion, radix,
# lucide) is NOT here: those belong to this application, not to the sites it
# generates, and adding them "just in case" would widen the allowlist for a need
# nobody has stated yet.
ALLOWED_DEPENDENCIES: dict[str, str] = {
    "react": "18.3.1",
    "react-dom": "18.3.1",
}

ALLOWED_DEV_DEPENDENCIES: dict[str, str] = {
    "@types/react": "18.3.18",
    "@types/react-dom": "18.3.5",
    "@vitejs/plugin-react": "4.3.4",
    "autoprefixer": "10.4.20",
    "postcss": "8.5.1",
    "tailwindcss": "3.4.17",
    "typescript": "5.7.3",
    "vite": "6.0.7",
}


def _package_json() -> str:
    """``package.json``, generated from the allowlist so the two cannot disagree.

    No ``preinstall``/``postinstall``/``prepare`` entries: the build runner passes
    ``--ignore-scripts``, and a template that also declares none means the
    protection does not rest on a single flag.
    """
    document = {
        "name": "generated-site",
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "scripts": {
            "build": "vite build",
            "typecheck": "tsc --noEmit",
        },
        "dependencies": dict(ALLOWED_DEPENDENCIES),
        "devDependencies": dict(ALLOWED_DEV_DEPENDENCIES),
    }
    return json.dumps(document, indent=2) + "\n"


_VITE_CONFIG = """\
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builder-owned. Not model-writable: Vite imports and EXECUTES this file, so a
// model able to edit it could run arbitrary code at build time
// (docs/architecture.md 5.2).
export default defineConfig({
  plugins: [react()],
  // Relative so the static output can be served from a per-project path
  // (docs/architecture.md 5.4).
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
"""

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Generated site</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

# Type-checks src/ ONLY. There is deliberately no tsconfig.node.json and no project
# reference. That pairing exists to type-check vite.config.ts, but a composite
# referenced project may not set noEmit, so `tsc --noEmit` fails with TS6310 —
# which would break step 3 of EVERY build (docs/architecture.md 5.3). vite.config.ts
# is builder-owned and frozen, so type-checking it once per generated project
# proves nothing. Verified against the real toolchain, not assumed.
_TSCONFIG = """\
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true
  },
  "include": ["src"]
}
"""

_TAILWIND_CONFIG = """\
import type { Config } from "tailwindcss";

// Builder-owned. Tailwind imports and EXECUTES this file during the build, so it
// carries the same risk as vite.config.ts and the same protection.
//
// Design tokens belong in src/index.css as CSS custom properties, which IS
// model-writable: the approved DesignSpec can be expressed there without anyone
// needing to edit executable config.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
} satisfies Config;
"""

_POSTCSS_CONFIG = """\
// Builder-owned; executed during the build (docs/architecture.md 5.2).
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""

# Files the toolchain executes or that define the project itself. Written on every
# scaffold and overwritten if present, because "builder-owned" has to mean the
# builder's copy wins.
BUILDER_OWNED_FILES: dict[str, str] = {
    "package.json": _package_json(),
    "vite.config.ts": _VITE_CONFIG,
    "index.html": _INDEX_HTML,
    "tsconfig.json": _TSCONFIG,
    "tailwind.config.ts": _TAILWIND_CONFIG,
    "postcss.config.js": _POSTCSS_CONFIG,
}

_SRC_MAIN = """\
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
"""

_SRC_APP = """\
export default function App() {
  return <main className="p-8">Nothing has been generated yet.</main>;
}
"""

_SRC_INDEX_CSS = """\
@tailwind base;
@tailwind components;
@tailwind utilities;
"""

# A scaffold that builds on its own, before any model has written a line. This is
# not decoration: it means the build runner can be exercised against a real npm and
# a real vite without involving an agent, so a build failure is unambiguous about
# which half broke. Everything here is under src/ and therefore model-writable —
# the Builder replaces it rather than working around it.
SEED_SOURCE_FILES: dict[str, str] = {
    "src/main.tsx": _SRC_MAIN,
    "src/App.tsx": _SRC_APP,
    "src/index.css": _SRC_INDEX_CSS,
}


def _write_lf(path: Path, content: str) -> None:
    """Write UTF-8 with LF line endings on every platform.

    Without ``newline="\\n"`` Python translates ``\\n`` to ``\\r\\n`` on Windows, so
    the same scaffold would have different bytes — and different
    ``FileRef.sha256`` values — depending on the machine. Preview freshness
    compares those checksums (docs/architecture.md 5.4), so a platform-dependent
    hash would make "has this file changed?" unanswerable.
    """
    path.write_text(content, encoding="utf-8", newline="\n")


def scaffold_workspace(workspace_dir: Path, *, seed_source: bool = True) -> list[str]:
    """Write the template into ``workspace_dir``, creating it if needed.

    Idempotent: builder-owned files are rewritten every time, which is what makes
    them builder-owned. If a model somehow altered one, the next scaffold restores
    it rather than building whatever is there.

    Seed sources are written **only when absent**, so re-scaffolding an existing
    project never destroys generated work.

    Args:
        workspace_dir: the project's ``workspace/`` directory.
        seed_source: write the placeholder ``src/`` when it is missing. Set False
            to scaffold config alone.

    Returns:
        The relative paths written, for logging and tests.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for name, content in BUILDER_OWNED_FILES.items():
        # Explicit LF so a scaffold is byte-identical on every platform; see
        # backend/tools/workspace.py for why that matters to FileRef.sha256.
        _write_lf(workspace_dir / name, content)
        written.append(name)

    if seed_source:
        for rel, content in SEED_SOURCE_FILES.items():
            target = workspace_dir / rel
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_lf(target, content)
            written.append(rel)

    logger.info("scaffolded workspace files=%d dir=%s", len(written), workspace_dir.name)
    return written
