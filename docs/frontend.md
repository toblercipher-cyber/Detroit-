<!-- Purpose: Record the current frontend scope, the approved Landing Page rule, and the concrete multi-page routing specification for Pricing and Chatbot. -->

# Frontend

## 1. v1 Frontend Scope (authoritative)

| Surface | Route | Status |
|---|---|---|
| Landing Page | `/` | **Completed and approved** |
| Pricing Page | `/pricing` | Remaining |
| Chatbot Page (start / workspace) | `/chat` | Remaining |

**FAQ and Help pages are OUT OF SCOPE for v1.** Do not create them, plan for them,
link to them, or reserve navigation space for them unless the user explicitly
requests them later.

## 2. Existing Stack (do not change without approval)

```text
frontend/
├── index.html                  # single Vite entry
├── vite.config.ts              # "@" -> ./src alias, dev port 5173
├── tailwind.config.ts          # design tokens, fonts, keyframes
├── tsconfig*.json
└── src/
    ├── main.tsx                # createRoot -> <App />
    ├── App.tsx                 # flat single-page composition (8 sections)
    ├── index.css               # CSS custom properties (HSL design tokens)
    ├── components/             # 13 section components
    ├── components/ui/          # 14 primitives (hand-rolled, shadcn-style)
    ├── hooks/                  # use-in-view, use-tilt
    └── assets/                 # 17 background/card images
```

Dependencies in use: React 18.3, Vite 6, Tailwind 3.4, framer-motion 13,
lucide-react, `@radix-ui/react-slot`, `@radix-ui/react-label`,
`class-variance-authority`, `clsx`, `tailwind-merge`, `tailwindcss-animate`.

> Note: `PRODUCT.md` describes the stack as "shadcn/ui". There is no `components.json`
> and no shadcn registry in this repo — the primitives are hand-written in shadcn's
> style. Treat `src/components/ui/` as project-owned code.

## 3. The Landing Page Rule

The Landing Page is **approved**. It is the primary visual and structural reference
for the Pricing and Chatbot pages.

Before creating either remaining page:

1. Inspect the existing Landing Page implementation — the built page is the
   reference, not an image.
2. Inspect `Refrence_images/chatbot_refrence.png` **for the Chatbot page only**, and
   only at the Reference Analysis stage.
3. Identify the shared design language.
4. Preserve approved project requirements.
5. Implement only after the required HITL review.

Preserve consistency in: typography, spacing, colors, buttons, cards, navigation,
visual hierarchy, responsive behavior. Do not introduce an unrelated design system.

### Redesign vs. scoped technical change

These are different things and must not be conflated:

| Not allowed (redesign) | Allowed (scoped technical change) |
|---|---|
| Restyling Landing sections | Moving `SiteNav` out of `hero-section.tsx` so every route can mount it |
| Rewriting Landing copy | Making nav/footer links route-aware |
| Reordering or removing Landing sections | Extracting the Landing composition into a route component |
| Changing Landing tokens, fonts or palette | Adding a `Pricing` entry to the nav |

**Acceptance criterion for every scoped technical change: the Landing Page's rendered
output must remain visually identical.** If a change would alter what the Landing Page
looks like, it is a redesign and requires explicit user approval.

## 4. Routing Specification

### 4.0 This is the FRONTEND router only

React Router routes **URL paths to pages** in the browser. It has nothing to do with
the **Agent / Workflow Router** (Python + LangGraph + Agent Harness), which routes
**workflow nodes and agent roles** on the server.

| | Frontend Router (here) | Agent / Workflow Router |
|---|---|---|
| Tech | React Router (TypeScript) | LangGraph + Agent Harness (Python) |
| Routes | `/`, `/pricing`, `/chat` | `planning -> design -> build -> evaluation -> ...` |
| State | browser location | `ProjectState` + LangGraph checkpoint |
| Spec | this section | `docs/architecture.md` §3.7, `docs/state.md` §4 |

Never implement agent selection, stage transitions or HITL routing in frontend code,
and never name frontend routes after workflow stages.

### 4.1 Chosen approach: client-side routing with `react-router-dom`

Three routes in one Vite SPA:

```text
/          -> Landing Page   (existing, visually unchanged)
/pricing   -> Pricing Page   (to build)
/chat      -> Chatbot Page   (to build)
```

> `react-router-dom` is **not yet installed**. Installing it requires explicit user
> approval. Nothing in this section may be implemented before that approval.

### 4.2 Why this fits the existing structure

- **Preserves the one-dev-server constraint.** A single `index.html` entry and a
  single `npm run dev` on port 5173 keep serving everything. `.claude/launch.json`
  needs no change.
- **The Chatbot page requires persistent client state.** It holds a live session
  (SSE stream, stage/progress, message history, pending HITL checkpoint). Full page
  reloads between surfaces would drop that state and re-open the stream.
- **A shared shell is mandatory.** The nav must appear identically on all three
  routes; client-side routing lets it mount once in a layout instead of being
  duplicated per page.
- **Real URLs are required.** `/pricing` must be linkable, shareable and
  bookmarkable.
- **Minimal footprint.** One dependency, no build-config restructuring.

### 4.3 Rejected alternatives

| Approach | Why rejected |
|---|---|
| Multiple Vite HTML entries (`index.html`, `pricing.html`, `app.html`) | Zero new deps, but duplicates the shell markup per entry, forces hard page loads between surfaces, and destroys Chatbot session state on navigation. |
| State-based view switcher in `App.tsx` | Zero new deps, but produces no URLs — no deep links, no shareable `/pricing`, no browser back/forward. Unacceptable for a public pricing page. |

### 4.4 Files affected

| File | Change | Landing visual impact |
|---|---|---|
| `frontend/package.json` | add `react-router-dom` | none |
| `frontend/src/main.tsx` | wrap `<App />` in `<BrowserRouter>` | none |
| `frontend/src/App.tsx` | becomes the route table + shared layout; the current 8-section composition moves out | none |
| `frontend/src/pages/landing.tsx` **(new)** | receives the current `App.tsx` composition verbatim | none — same tree, same order |
| `frontend/src/pages/pricing.tsx` **(new)** | Pricing Page | n/a |
| `frontend/src/pages/chat.tsx` **(new)** | Chatbot Page | n/a |
| `frontend/src/components/site-layout.tsx` **(new)** | mounts `SiteNav` + `<Outlet />` for all routes | none |
| `frontend/src/components/hero-section.tsx` | **remove** the `<SiteNav />` mount (it moves to the layout); everything else untouched | none — the nav renders in the same position, it is just owned by the layout |
| `frontend/src/components/site-nav.tsx` | route-aware links + `Pricing` entry + a `/chat` CTA | nav gains one link; otherwise identical |
| `frontend/src/components/footer-section.tsx` | route-aware links | footer gains route links; styling untouched |
| `frontend/vite.config.ts` | none — the Vite dev server already serves the SPA fallback | none |

`vite build` + `vite preview` serve `index.html` for unknown paths, so deep links work
in dev and preview. A production static host would need an SPA rewrite rule; that is a
deployment concern, recorded here so it is not forgotten.

### 4.5 How existing hash navigation keeps working

The Landing Page's in-page anchors (`#top`, `#pipeline`, `#agents`, `#about`,
`#dashboard`, `#harness`, `#mcp`, `#demo`, `#access`) must keep working. Rules:

1. **On the Landing route**, section anchors stay plain in-page anchors. Native anchor
   behaviour plus the existing `scroll-behavior: smooth` is preserved — no JavaScript
   scroll hijacking is introduced.
2. **From `/pricing` or `/chat`**, a section link targets `/#section`. React Router
   changes the route but does **not** scroll to a hash on its own.
3. Therefore the shared layout mounts one small effect — a `ScrollToHash` component —
   that on location change scrolls to `location.hash` when present (honouring
   `prefers-reduced-motion`, consistent with `index.css`). With no hash it restores
   scroll to the top on route change.
4. `SiteNav` composes links as: on `/` use `#section`; elsewhere use `/#section`. Page
   links (`/pricing`, `/chat`) are always router links.
5. The existing smooth-scroll and reduced-motion rules in `index.css` stay as they
   are. They are not replaced.

### 4.6 Chatbot page ↔ backend

The Chatbot Page is the only surface that talks to the builder backend. It consumes
the contract in `docs/api.md`:

- `POST /api/v1/projects` to start a project
- `GET /api/v1/projects/{project_id}/events` (SSE) for live stage/progress
- `GET /api/v1/projects/{project_id}/hitl` and `POST .../hitl` for approval gates
- `GET /api/v1/projects/{project_id}/preview` for the generated-site preview

The Landing and Pricing pages stay fully static and make no backend calls.

## 5. Chatbot Reference

```text
Refrence_images/chatbot_refrence.png
```

Handling rules:

1. Analyze it **only at the Reference Analysis stage** — not during planning, not
   during implementation.
2. Extract **design intent and structure**, not pixels. Do not copy it literally.
3. **Do not silently adopt its palette.** The reference is indigo/violet on navy; the
   approved project language is ember-orange (`--ember: 14 88% 55%`) on warm
   near-black (`--background: 20 9% 6%`). The approved Landing Page wins.
4. Any meaningful visual conflict between the reference and the approved design
   language **must go through HITL** with concrete options. See `docs/design.md`.

### Structure observed in the reference (usable; its palette is not)

Three-column desktop workspace:

- **Left rail** — title, conversation/thread list with icons, a grouped section with
  one active highlighted item, user avatar + settings pinned at the bottom.
- **Center canvas** — large greeting headline + supporting paragraph, a row of
  suggestion/capability cards, alternating user/assistant messages, inline rich
  artifacts (chart, syntax-highlighted code block with a copy affordance), a thumbnail
  strip, and a bordered composer with attach control, placeholder, send action, and a
  monospace hint line beneath.
- **Right rail** — a connections panel with a connected status pill, resource rows
  with launch affordances, an add action, a performance block with labelled meters, a
  toggle group, and a secondary timestamped widget list.

This maps onto our product: threads = projects, right rail = MCP servers + run
telemetry + stage progress, center = the HITL conversation and preview.

## 6. Known Frontend Debt (recorded, not in scope to fix now)

| Item | Note |
|---|---|
| `src/components/cta-form.tsx` | 0 importers — dead code |
| `src/components/recent-builds.tsx` | 0 importers — dead code |
| `src/components/ui/pricing-card.tsx` | 0 importers — staged for the Pricing page; never rendered, so it is **not** part of the approved Landing Page. Its treatment is a Design-stage decision (see `docs/design.md`). |
| `src/assets/pricing-bg.jpg` | named for Pricing but currently consumed by `about-section.tsx` |
| No linter / formatter / test runner | the only executable frontend check is `npm run build` (`tsc -b && vite build`) |
