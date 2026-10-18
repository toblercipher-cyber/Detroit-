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
 * NO LONGER A PLACEHOLDER
 *
 * This page used to authenticate nobody, and said so at length, because there
 * was no auth anywhere in the product. Supabase Auth is now live
 * (`docs/database.md` §1.3): `AuthUI` is wired to the real `signIn`/`signUp`
 * in `@/lib/auth`, a successful submit produces a real session, and
 * `App.tsx` guards `/chat` and `/dashboard` against its absence.
 *
 * The old notice claiming nothing was sent or stored has been removed. It was
 * describing a build that no longer exists, and a false reassurance next to a
 * password field is worse than none.
 *
 * STILL NOT LINKED FROM THE NAVIGATION. That is now a UX decision rather than
 * an honesty one, and changing it belongs with whoever owns the nav.
 *
 * NOT IN THE v1 SCOPE TABLE. `docs/frontend.md` §1 lists `/`, `/pricing` and
 * `/chat`; `/login` is a scope expansion requested directly by the user, as
 * `/dashboard` and `/features` were.
 * ---------------------------------------------------------------------------
 */

import { ArrowLeft, ShieldAlert } from "lucide-react";

import { AppFooterBar } from "@/components/app-footer-bar";
import { AuthUI } from "@/components/ui/auth-ui";

/** Where a successful sign-in lands when nothing else is requested. */
const REDIRECT_AFTER_LOGIN = "/chat";

/**
 * Where to go after signing in.
 *
 * WHY THIS EXISTS
 *   `App.tsx`'s route guard already redirects an anonymous visitor to
 *   `/login?next=<path>`. Nothing read that parameter, so someone bounced off
 *   `/dashboard` signed in and landed on `/chat` — the guard recorded where
 *   they were going and the login page threw it away.
 *
 * WHY ONLY A SAME-SITE PATH IS ACCEPTED
 *   `next` comes from the URL, so it is attacker-controllable. Echoing it into
 *   a navigation unchecked is an open redirect: `/login?next=https://evil.example`
 *   would send someone who just authenticated straight off-site, on a link
 *   that looks like ours. So it must start with a single `/` and must not
 *   start with `//` or `/\`, both of which browsers read as protocol-relative
 *   URLs pointing at another host.
 */
function redirectTarget(): string {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next) return REDIRECT_AFTER_LOGIN;
  if (!next.startsWith("/")) return REDIRECT_AFTER_LOGIN;
  if (next.startsWith("//") || next.startsWith("/\\")) return REDIRECT_AFTER_LOGIN;
  /* `/login` itself would loop. Compared on the PATH alone, so
     `/login?next=...` cannot slip past by carrying a query string. */
  const path = next.split(/[?#]/)[0].replace(/\/+$/, "").toLowerCase();
  if (path === "/login") return REDIRECT_AFTER_LOGIN;
  return next;
}

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
          window.location.assign(redirectTarget());
        }}
      />

      {/* Persistent, and positioned over the form column so it is read before
          anything is typed — not after a submit. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 p-6 md:w-1/2">
        <div className="mx-auto max-w-[350px]">
          <p className="flex items-start gap-2.5 rounded-lg border border-border bg-card/80 px-3.5 py-2.5 text-[12px] leading-relaxed text-muted-foreground backdrop-blur-sm">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-400" />
            <span>
              Accounts are handled by Supabase Auth. DETROIT never sees your
              password.
            </span>
          </p>

          {/* Inside the bottom overlay rather than appended below the auth
              panel: both panels are `min-h-screen`, so a footer in normal
              flow would sit a full viewport down where nobody would see it.
              No brand lockup — this page already floats one top-left — and
              only public destinations, since `/chat` and `/dashboard` are
              guarded and would bounce an anonymous visitor straight back
              here. */}
          <AppFooterBar
            current="/login"
            showBrand={false}
            links={[
              { label: "Home", href: "/" },
              { label: "Product", href: "/features" },
            ]}
            className="pointer-events-auto mt-3 border-t-0 px-0 py-0"
          />
        </div>
      </div>
    </main>
  );
}
