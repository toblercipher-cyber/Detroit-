/**
 * Supabase Auth — the browser's identity layer.
 *
 * WHY THIS FILE EXISTS
 *   Identity has to live in exactly one place: one Supabase client, one
 *   session, one subscription. Spread across pages it becomes several clients
 *   racing to refresh the same token, and a component that silently holds a
 *   stale session. This module owns all of it and exposes a hook.
 *
 * LAYER
 *   Frontend infrastructure (`src/lib/`), beside `api.ts`. It imports nothing
 *   from `components/` or `pages/`, so no page can create a cycle through it.
 *   It renders no UI of its own.
 *
 * WHAT IT OWNS
 *   - the single Supabase client, built from the publishable key
 *   - sign-up, sign-in, sign-out against Supabase Auth
 *   - the session, restored on reload and kept current
 *   - `getAccessToken()`, the one way to obtain a bearer token
 *
 * WHAT IT DOES NOT OWN
 *   - **Route protection.** Which routes require a session is a routing
 *     decision and belongs with the route table.
 *   - **The login UI.** `components/ui/auth-ui.tsx` owns the form; it is not
 *     wired to this module yet and still carries its placeholder notices.
 *   - **Attaching the header.** `api.ts` is the only module that performs
 *     transport; it will call `getAccessToken()` rather than import a client.
 *   - **Any backend trust.** Nothing here authorizes anything. A session in
 *     this browser is a claim; the backend decides whether to believe it.
 *
 * ---------------------------------------------------------------------------
 * TWO RULES THIS FILE ENFORCES RATHER THAN DESCRIBES
 *
 * 1. THE USER ID IS NEVER SENT. There is deliberately no exported helper that
 *    returns a user id for transport. The id is available for *rendering*
 *    (`useAuth().user`), and the backend derives identity from the verified
 *    token alone — a client that can name its own user can claim anyone's
 *    project (`docs/database.md` §1.3.5, `docs/api.md` §1.1).
 *
 * 2. ONLY THE PUBLISHABLE KEY. `assertPublishableKey` below refuses a
 *    service-role credential at startup. That key bypasses RLS completely
 *    (`docs/database.md` §1.3.4), and pasting it into a frontend `.env` is
 *    the single most damaging configuration mistake available here — it would
 *    ship in a JavaScript bundle and hand every visitor full read-write on
 *    every user's data. A loud failure is the correct response.
 *
 * ---------------------------------------------------------------------------
 * CONFIGURATION — REQUIRED, AND NOT PRESENT YET
 *
 * Vite only exposes variables prefixed `VITE_`, and only from the FRONTEND
 * env file. The project's root `.env` is read by pydantic-settings for the
 * backend and is invisible to the browser, by design.
 *
 * So `frontend/.env` must exist and contain:
 *
 *     VITE_SUPABASE_URL=https://<project-ref>.supabase.co
 *     VITE_SUPABASE_PUBLISHABLE_KEY=<the publishable / anon key>
 *
 * Both values are safe in a bundle. The publishable key is designed to be
 * public: it identifies the project, not the caller, and the caller's rights
 * come from their JWT evaluated against RLS. **That is only true while RLS is
 * enabled** — which is what `migrations/0001_application_schema.sql` does, and
 * why it is not optional.
 *
 * Never put `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_DB_PASSWORD` or
 * `DATABASE_URL` in this file. `.gitignore` covers `frontend/.env`, verified.
 *
 * WITHOUT THAT FILE this module does not throw on import and does not break
 * the app. It reports `status: "unconfigured"`, and the auth calls fail with a
 * message that says what is missing. A landing page should not go blank
 * because nobody has configured login yet.
 * ---------------------------------------------------------------------------
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  createClient,
  type Session,
  type SupabaseClient,
  type User,
} from "@supabase/supabase-js";

/* ------------------------------------------------------------------ *
 * Configuration
 * ------------------------------------------------------------------ */

const SUPABASE_URL: string = (import.meta.env.VITE_SUPABASE_URL ?? "")
  .toString()
  .trim()
  .replace(/\/+$/, "");

const SUPABASE_PUBLISHABLE_KEY: string = (
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ?? ""
)
  .toString()
  .trim();

/** True when both values are present. Never asserts that they are correct. */
export const isAuthConfigured: boolean = Boolean(
  SUPABASE_URL && SUPABASE_PUBLISHABLE_KEY,
);

/**
 * Refuse a service-role credential before it can be used.
 *
 * Two shapes are rejected. New-style Supabase secret keys are prefixed
 * `sb_secret_`. Legacy keys are JWTs whose payload carries
 * `"role":"service_role"`, so the middle segment is decoded and inspected —
 * no verification, no library, just enough to read a claim we must never see
 * here.
 *
 * Throwing is deliberate. Falling back to "unconfigured" would hide the one
 * mistake that must never reach production quietly.
 */
function assertPublishableKey(key: string): void {
  if (!key) return;

  if (key.startsWith("sb_secret_")) {
    throw new Error(
      "VITE_SUPABASE_PUBLISHABLE_KEY holds a Supabase SECRET key. That key " +
        "bypasses row-level security and must never reach a browser. Use the " +
        "publishable (anon) key.",
    );
  }

  const segments = key.split(".");
  if (segments.length !== 3) return; // not a JWT; nothing further to check
  try {
    const payload = JSON.parse(
      atob(segments[1].replace(/-/g, "+").replace(/_/g, "/")),
    ) as { role?: unknown };
    if (payload.role === "service_role") {
      throw new Error(
        "VITE_SUPABASE_PUBLISHABLE_KEY holds a SERVICE ROLE key. That key " +
          "bypasses row-level security and must never reach a browser. Use " +
          "the publishable (anon) key.",
      );
    }
  } catch (error) {
    // Re-throw our own refusal; ignore a key that simply is not decodable.
    if (error instanceof Error && error.message.includes("SERVICE ROLE")) throw error;
  }
}

assertPublishableKey(SUPABASE_PUBLISHABLE_KEY);

/**
 * The one client for the whole app.
 *
 * Module-level so it is created once. A second instance would run a second
 * token-refresh timer against the same stored session, and the two would race.
 *
 * `persistSession` is what makes reload work: the session is written to
 * localStorage and read back by `getSession()` on the next load.
 * `autoRefreshToken` renews the access token before expiry, which is the
 * reason this uses the official client rather than hand-rolled fetch calls —
 * refresh is the part that is easy to get subtly wrong.
 */
export const supabase: SupabaseClient | null = isAuthConfigured
  ? createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        // The app has no OAuth redirect flow yet; parsing the URL for a
        // session would be looking for something nothing produces.
        detectSessionInUrl: false,
      },
    })
  : null;

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

/**
 * `loading` is a real state, not a detail.
 *
 * Restoring a session is asynchronous. A route guard that treats "not loaded
 * yet" as "signed out" bounces an authenticated user to the login page on
 * every refresh, which is the classic bug in this area. Consumers must wait
 * for `loading` to clear before deciding anything.
 */
export type AuthStatus = "loading" | "authenticated" | "anonymous" | "unconfigured";

export interface AuthResult {
  ok: boolean;
  /** Safe to display. Supabase's own message, or one from this module. */
  message?: string;
  /**
   * True when sign-up succeeded but no session was issued, because the
   * project requires email confirmation. Not an error, and the difference
   * matters: the UI must say "check your email", not "signed in".
   */
  needsEmailConfirmation?: boolean;
}

export interface AuthContextValue {
  status: AuthStatus;
  session: Session | null;
  user: User | null;
  signUp: (email: string, password: string) => Promise<AuthResult>;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<AuthResult>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Turn Supabase's wording into something that names the actual problem.
 *
 * Two of these are genuinely misleading as shipped, and both cost real
 * debugging time:
 *
 *   "Email rate limit exceeded" sounds like a login throttle. It is not — it
 *   means the project's mailer has sent its hourly allowance of CONFIRMATION
 *   emails. The password was never checked. Someone re-trying a signup sees
 *   it and reasonably concludes their credentials are being rejected.
 *
 *   "Email not confirmed" is accurate but gives no way forward, and the way
 *   forward is usually a project setting rather than anything the person can
 *   do from this form.
 *
 * Supabase's own text is kept where it is already clear — notably the
 * deliberately vague "Invalid login credentials", which does not say whether
 * the email exists, because saying so would make this form an account oracle.
 */
function explain(message: string): string {
  const text = message.toLowerCase();

  if (text.includes("rate limit")) {
    return (
      "Too many confirmation emails have been sent from this project in the " +
      "last hour. This is not a password problem — the sign-in was never " +
      "attempted. Wait an hour, or turn off email confirmation in Supabase " +
      "(Authentication → Sign In / Providers → Email)."
    );
  }
  if (text.includes("not confirmed")) {
    return (
      "This account exists but its email has not been confirmed yet. Use the " +
      "link in the confirmation email, or turn off email confirmation in " +
      "Supabase for local development."
    );
  }
  if (text.includes("already registered") || text.includes("already been registered")) {
    return "An account with that email already exists. Try signing in instead.";
  }
  if (text.includes("is invalid") && text.includes("email")) {
    return (
      "Supabase rejected that email address. Addresses at example.com and " +
      "similar placeholder domains are not accepted."
    );
  }
  return message;
}

const NOT_CONFIGURED: AuthResult = {
  ok: false,
  message:
    "Sign-in is not configured in this build. frontend/.env needs " +
    "VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY.",
};

/* ------------------------------------------------------------------ *
 * Provider
 * ------------------------------------------------------------------ */

/**
 * Holds the session and keeps it current. Mount once, above the route table.
 *
 * Both halves are needed, and they are not redundant:
 *   - `getSession()` reads the persisted session immediately on mount, so a
 *     reload does not flash the signed-out state.
 *   - `onAuthStateChange` keeps it correct afterwards — token refresh,
 *     sign-out, and sign-out performed in another browser tab.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState<boolean>(isAuthConfigured);
  /* Guards against setting state after unmount, and against a late
     getSession() resolving over a newer value from the subscription. */
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    if (!supabase) {
      setLoading(false);
      return () => {
        alive.current = false;
      };
    }

    void supabase.auth.getSession().then(({ data }) => {
      if (!alive.current) return;
      setSession(data.session);
      setLoading(false);
    });

    const { data: subscription } = supabase.auth.onAuthStateChange(
      (_event, next) => {
        if (!alive.current) return;
        setSession(next);
        setLoading(false);
      },
    );

    return () => {
      alive.current = false;
      subscription.subscription.unsubscribe();
    };
  }, []);

  const signUp = useCallback(
    async (email: string, password: string): Promise<AuthResult> => {
      if (!supabase) return NOT_CONFIGURED;
      const { data, error } = await supabase.auth.signUp({ email, password });
      if (error) return { ok: false, message: explain(error.message) };
      /* A user with no session means the project requires confirmation. */
      if (data.user && !data.session) {
        return {
          ok: true,
          needsEmailConfirmation: true,
          message: "Check your email to confirm the account, then sign in.",
        };
      }
      return { ok: true };
    },
    [],
  );

  const signIn = useCallback(
    async (email: string, password: string): Promise<AuthResult> => {
      if (!supabase) return NOT_CONFIGURED;
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      /* Supabase answers wrong-password and unknown-email identically, on
         purpose: distinguishing them would turn the form into an account
         oracle. Passed through unchanged. */
      return error
        ? { ok: false, message: explain(error.message) }
        : { ok: true };
    },
    [],
  );

  const signOut = useCallback(async (): Promise<AuthResult> => {
    if (!supabase) return NOT_CONFIGURED;
    const { error } = await supabase.auth.signOut();
    return error ? { ok: false, message: error.message } : { ok: true };
  }, []);

  const value = useMemo<AuthContextValue>(() => {
    const status: AuthStatus = !isAuthConfigured
      ? "unconfigured"
      : loading
        ? "loading"
        : session
          ? "authenticated"
          : "anonymous";
    return {
      status,
      session,
      user: session?.user ?? null,
      signUp,
      signIn,
      signOut,
    };
  }, [loading, session, signUp, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** The session and the auth actions. Throws if used outside the provider. */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside <AuthProvider>.");
  }
  return context;
}

/* ------------------------------------------------------------------ *
 * Token access for the transport layer
 * ------------------------------------------------------------------ */

/**
 * The current access token, or `null` when nobody is signed in.
 *
 * Deliberately NOT a hook. `api.ts` calls it from inside a request function,
 * where hooks cannot run, and it must read the live token rather than one
 * captured by a render.
 *
 * `getSession()` rather than a cached value: it returns the stored session and
 * refreshes it first if it has expired, so a long-idle tab sends a valid token
 * instead of a stale one that the backend would reject.
 *
 * This is the ONLY thing that should ever be sent to the backend as proof of
 * identity — the token, in the `Authorization: Bearer` header. No user id, no
 * email, no role (`docs/api.md` §1.1).
 */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session) return null;
  return data.session.access_token;
}
