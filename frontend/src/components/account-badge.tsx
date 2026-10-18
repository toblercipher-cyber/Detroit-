/**
 * The signed-in account block, with sign-out.
 *
 * Why this file exists:
 * `/chat` and `/dashboard` both pin an account block to the bottom of their
 * left rail, and both previously hard-coded "Local workspace / No sign-in
 * required" from before Supabase Auth existed (`docs/frontend.md` §1.1 names
 * replacing both). Identity UI copied into two pages means two places where
 * sign-out can be wrong, and sign-out is the one control a user needs to be
 * able to trust. So it lives here once and both pages mount it.
 *
 * Layer: frontend presentation (shared component). It reads `@/lib/auth` and
 * nothing else — no transport, no project state.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   - It does not guard anything. Whether a surface requires a session is a
 *     routing decision and lives in `App.tsx` (`docs/frontend.md` §1.1).
 *   - It does not read or display a user id. The id is not identity for
 *     transport purposes and showing it invites someone to send it; the
 *     backend derives identity from the verified token alone
 *     (`docs/api.md` §1.1, `docs/database.md` §1.3.5).
 *
 * WHY SIGN-OUT NAVIGATES RATHER THAN RE-RENDERS
 *   `react-router-dom` is still not installed and `App.tsx` reads
 *   `location.pathname` once at render (`docs/frontend.md` §4.1), so only a
 *   real navigation changes surface. Signing out from a protected page also
 *   has to leave it: staying put would let the route guard's redirect race the
 *   cleared session. Landing on `/` is deliberate — the marketing page is
 *   public, so it cannot bounce.
 */

import { useState } from "react";
import { LogOut } from "lucide-react";

import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/**
 * A display name from an email address.
 *
 * Supabase issues no profile by default, so the local part is the only name
 * available. `user_metadata.full_name` is preferred when some other flow has
 * set it, which is why it is checked rather than assumed absent.
 */
function displayName(email: string | undefined, fullName: unknown): string {
  if (typeof fullName === "string" && fullName.trim()) return fullName.trim();
  if (!email) return "Signed in";
  const local = email.split("@")[0] ?? email;
  return local || email;
}

/** One letter for the avatar. Uppercased so casing in an email does not show. */
function initial(name: string): string {
  const char = name.trim().charAt(0);
  return char ? char.toUpperCase() : "D";
}

export function AccountBadge({ className }: { className?: string }) {
  const { status, user, signOut } = useAuth();
  const [busy, setBusy] = useState(false);

  async function handleSignOut() {
    if (busy) return;
    setBusy(true);
    const result = await signOut();
    if (result.ok) {
      window.location.assign("/");
      return;
    }
    /* Sign-out failing is rare and means the session is still live, so the
       button has to become usable again rather than leaving a dead control. */
    setBusy(false);
  }

  const shell = cn(
    "flex shrink-0 items-center gap-3 border-t border-border px-5 py-4",
    className,
  );

  /* Without `frontend/.env` there is no auth to represent. Saying so beats
     showing an empty avatar, and matches how the route guard treats this
     case — it lets the surface render rather than blocking it. */
  if (status === "unconfigured") {
    return (
      <div className={shell}>
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-card font-mono text-[10px] text-foreground/80">
          D
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] text-foreground">
            Local workspace
          </span>
          <span className="block font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
            Sign-in not configured
          </span>
        </span>
      </div>
    );
  }

  if (status === "loading") {
    return (
      <div className={shell}>
        <span className="size-8 shrink-0 animate-pulse rounded-full border border-border bg-card" />
        <span className="min-w-0 flex-1">
          <span className="block font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
            Restoring session
          </span>
        </span>
      </div>
    );
  }

  /* `anonymous` on a protected surface means the guard is mid-redirect. On a
     public one it is simply the truth, and an explicit link beats a blank. */
  if (status === "anonymous" || !user) {
    return (
      <div className={shell}>
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-card font-mono text-[10px] text-foreground/80">
          ?
        </span>
        <span className="min-w-0 flex-1">
          <a
            href="/login"
            className="block truncate text-[13px] text-foreground hover:text-ember"
          >
            Sign in
          </a>
          <span className="block font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
            Not signed in
          </span>
        </span>
      </div>
    );
  }

  const name = displayName(user.email, user.user_metadata?.full_name);

  return (
    <div className={shell}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border bg-card font-mono text-[10px] text-foreground/80">
        {initial(name)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] text-foreground">{name}</span>
        <span className="block truncate text-[11px] text-muted-foreground">
          {user.email}
        </span>
      </span>
      <button
        type="button"
        onClick={handleSignOut}
        disabled={busy}
        aria-label="Sign out"
        title="Sign out"
        className="shrink-0 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
      >
        <LogOut size={16} />
      </button>
    </div>
  );
}
