/**
 * Application root — selects which surface to render.
 *
 * Why this file exists:
 * `main.tsx` mounts exactly one component. This file decides which surface that
 * is, so the Landing composition and the Chatbot workspace can coexist in one
 * Vite SPA without a second HTML entry.
 *
 * Layer: frontend presentation (composition root).
 *
 * ---------------------------------------------------------------------------
 * TEMPORARY: this is a preview bridge, not the routing specification.
 *
 * `docs/frontend.md` §4.1 specifies `react-router-dom` for `/`, `/pricing` and
 * `/chat`, and installing it requires explicit user approval that has not been
 * given. Until then, reading `location.pathname` once at render is the smallest
 * thing that makes the Chatbot workspace reachable at a real, reloadable,
 * shareable URL.
 *
 * This is deliberately NOT the "state-based view switcher" that §4.3 rejects —
 * that was rejected for producing no URLs. `/chat` here is a genuine URL with
 * working deep links, reload and browser history.
 *
 * What it still does not do, and why the real router is the next step:
 * - no client-side transitions (links do a full page load)
 * - no shared layout or `<Outlet />`
 * - no `ScrollToHash` for cross-surface `/#section` links
 * - Chatbot session state will not survive navigation
 *
 * Works in `vite dev` and `vite preview` because both serve `index.html` for
 * unknown paths. A production static host needs an SPA rewrite rule
 * (`docs/frontend.md` §4.4).
 *
 * The Landing Page composition below is unchanged — same sections, same order.
 * ---------------------------------------------------------------------------
 */

import { useEffect, type ComponentType } from "react";

import { AuthProvider, useAuth } from "@/lib/auth";

import { HeroSection } from "@/components/hero-section";
import { AgentsSection } from "@/components/agents-section";
import { AboutSection } from "@/components/about-section";
import { DashboardSection } from "@/components/dashboard-section";
import { HarnessSection } from "@/components/harness-section";
import { McpSection } from "@/components/mcp-section";
import { RunBentoSection } from "@/components/run-bento-section";
import { FooterSection } from "@/components/footer-section";
import { ChatPage } from "@/pages/chat";
import { DashboardPage } from "@/pages/dashboard";
import { FeaturesPage } from "@/pages/features";
import { LoginPage } from "@/pages/login";

/** The approved Landing Page. Preserved verbatim (`docs/frontend.md` §3). */
function LandingPage() {
  return (
    <main className="min-h-screen bg-background">
      <HeroSection />
      <AgentsSection />
      <AboutSection />
      <DashboardSection />
      <HarnessSection />
      <McpSection />
      <RunBentoSection />
      <FooterSection />
    </main>
  );
}

/**
 * The surface table. `/dashboard`, `/features` and `/login` are scope
 * expansions beyond the three routes in `docs/frontend.md` §1, each added at
 * the user's direct request.
 */
const SURFACES: Record<string, ComponentType> = {
  "/chat": ChatPage,
  "/dashboard": DashboardPage,
  "/features": FeaturesPage,
  "/login": LoginPage,
};

/** Trailing slashes and casing should not decide which surface you land on. */
function currentSurface(): ComponentType {
  const path = window.location.pathname.replace(/\/+$/, "").toLowerCase();
  return SURFACES[path] ?? LandingPage;
}

/**
 * Routes that require a session.
 *
 * Landing, Product and Login stay public: a marketing page behind a login is
 * a marketing page nobody reads.
 */
const PROTECTED: ReadonlySet<string> = new Set(["/chat", "/dashboard"]);

function currentPath(): string {
  return window.location.pathname.replace(/\/+$/, "").toLowerCase() || "/";
}

/**
 * Path AND query, for the `next` parameter.
 *
 * `currentPath()` normalises away the query so it can be matched against the
 * surface table, which is right for routing and wrong for "send me back where
 * I was": `/chat?project=<id>` deep-links one project, so dropping the query
 * would sign someone in and then land them on an empty workspace instead of
 * the build they clicked.
 */
function currentHref(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/**
 * Renders a protected surface, or sends the visitor to sign in.
 *
 * `status === "loading"` is handled as its own case, deliberately. Restoring a
 * session is asynchronous, and a guard that treats "not loaded yet" as
 * "signed out" bounces an authenticated user to /login on every refresh —
 * the classic bug in this area.
 *
 * `unconfigured` renders the surface instead of blocking. Without
 * `frontend/.env` there is no auth to enforce, and locking the app out of its
 * own pages would make a missing variable look like a broken build. The
 * backend is the real boundary regardless: it answers 401 to an unauthenticated
 * request whatever this component decides.
 */
function Guarded({ Surface }: { Surface: ComponentType }) {
  const { status } = useAuth();

  useEffect(() => {
    if (status === "anonymous") {
      const next = encodeURIComponent(currentHref());
      window.location.replace(`/login?next=${next}`);
    }
  }, [status]);

  if (status === "loading") {
    return (
      <div
        role="status"
        className="grid min-h-screen place-items-center bg-background"
      >
        <p className="font-mono text-xs uppercase tracking-[0.3em] text-muted-foreground">
          Restoring session
        </p>
      </div>
    );
  }
  if (status === "anonymous") return null; // redirecting
  return <Surface />;
}

function App() {
  const Surface = currentSurface();
  const needsAuth = PROTECTED.has(currentPath());
  return (
    <AuthProvider>
      {needsAuth ? <Guarded Surface={Surface} /> : <Surface />}
    </AuthProvider>
  );
}

export default App;
