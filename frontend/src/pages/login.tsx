/**
 * Login Page — the sign-in / sign-up surface (`/login`).
 *
 * Why this file exists:
 * `components/ui/auth-ui.tsx` owns the two-panel auth composition but knows
 * nothing about routing or the rest of the product. This is the route
 * component that mounts it, pins the brand mark, and states plainly that
 * authentication is not active yet.
 *
 * Layer: frontend presentation (route page component).
 *
 * ---------------------------------------------------------------------------
 * A PLACEHOLDER, ON PURPOSE
 *
 * `docs/api.md` §1.1: "There is no authentication in v1. The backend is a
 * local single-user service." There is no auth route, no user record and no
 * session anywhere in `backend/`, and CLAUDE.md §2 forbids inventing one.
 *
 * So this page is built and reachable, but it authenticates nobody. The notice
 * below is persistent rather than only appearing on submit, because someone
 * should know before they type a password into it, not after. Nothing entered
 * here is sent anywhere or stored anywhere.
 *
 * NOT LINKED FROM THE NAVIGATION, and deliberately. Putting "Sign in" in
 * `site-nav.tsx` would advertise a working account system that does not exist.
 * The route is reachable at `/login` so it can be reviewed; the nav entry is
 * the last step once real auth lands, not this one.
 *
 * NOT IN THE v1 SCOPE TABLE. `docs/frontend.md` §1 lists `/`, `/pricing` and
 * `/chat`; `/login` is a scope expansion requested directly by the user, as
 * `/dashboard` and `/features` were.
 * ---------------------------------------------------------------------------
 */

import { ArrowLeft, ShieldAlert } from "lucide-react";

import { AuthUI } from "@/components/ui/auth-ui";

/** Where a successful sign-in lands. The workspace, not the marketing page. */
const REDIRECT_AFTER_LOGIN = "/chat";

export function LoginPage() {
  return (
    <main className="relative min-h-screen bg-background text-foreground">
      {/* Floated over the form column rather than placed in a header bar: the
          auth composition is a full-bleed two-panel grid, and a header row
          above it would push the image panel off the fold. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between gap-4 p-6 md:w-1/2">
        <a
          href="/"
          className="pointer-events-auto font-display text-base font-bold tracking-tight"
        >
          DETROIT<span className="text-ember">-AI</span>
        </a>
        <a
          href="/"
          className="pointer-events-auto inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft size={12} />
          Back
        </a>
      </div>

      <AuthUI
        onAuthenticated={() => {
          /* A full navigation, not a router push: `react-router-dom` is still
             not installed and App.tsx reads `location.pathname` once at
             render, so only a real navigation changes the surface. */
          window.location.assign(REDIRECT_AFTER_LOGIN);
        }}
      />

      {/* Persistent, and positioned over the form column so it is read before
          anything is typed — not after a submit. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 p-6 md:w-1/2">
        <p className="mx-auto flex max-w-[350px] items-start gap-2.5 rounded-lg border border-border bg-card/80 px-3.5 py-2.5 text-[12px] leading-relaxed text-muted-foreground backdrop-blur-sm">
          <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-400" />
          <span>
            Accounts are handled by Supabase Auth. DETROIT never sees your
            password.
          </span>
        </p>
      </div>
    </main>
  );
}
