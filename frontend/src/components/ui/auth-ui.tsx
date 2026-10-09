/**
 * Authentication UI — the sign-in / sign-up surface.
 *
 * Why this file exists:
 * It owns the two-panel auth composition: the form column on the left, and the
 * image column on the right whose quote types itself out. It is presentation
 * only — it holds no session, calls no endpoint, and decides nothing about who
 * a user is.
 *
 * Layer: frontend presentation (vendored UI component, `components/ui/`).
 *
 * ---------------------------------------------------------------------------
 * THIS LOGIN IS DELIBERATELY NOT WIRED UP
 *
 * `docs/api.md` §1.1 is explicit: "There is no authentication in v1. The
 * backend is a local single-user service." There is no auth endpoint, no user
 * record, no session and no token anywhere in `backend/`, and CLAUDE.md §2
 * forbids inventing one.
 *
 * So this component is a placeholder built to the user's request, and it says
 * so on screen. Concretely:
 *
 *   - Submitting either form performs NO network call. The handler calls
 *     preventDefault and renders a status line; nothing is sent anywhere.
 *   - Nothing is stored. No credential reaches localStorage, a cookie, or any
 *     other sink — a dummy form that quietly kept a password would be worse
 *     than no form at all.
 *   - "Continue with Google" is layout, not an integration. No OAuth client,
 *     no redirect, no provider.
 *
 * When real auth arrives it slots in behind `onSignIn` / `onSignUp`, and the
 * notice comes out. Until then the notice stays, because a login box that
 * looks functional and is not is the one thing this page must not be.
 *
 * ---------------------------------------------------------------------------
 * ADAPTATIONS MADE TO THE SUPPLIED COMPONENT
 *
 * 1. `'use client'` dropped — a Next.js App Router marker. This is a Vite SPA.
 *
 * 2. The supplied file carried its own private copies of `cn`, `Label`,
 *    `Button` and `Input`. Those are deleted and the project's own primitives
 *    imported instead. This is the difference between the page looking like
 *    DETROIT and looking like default shadcn: the project's `Input` is a
 *    bottom-ruled field on a transparent ground, its `Button` is
 *    `rounded-sm` with an ember hover, and its `Label` is an uppercase mono
 *    eyebrow. A second set of primitives would also have been a second source
 *    of truth for button styling (CLAUDE.md §29).
 *
 * 3. The supplied Tailwind CSS block is Tailwind 4 syntax (`@theme inline`,
 *    oklch values, a `--destructive-foreground` token). This project is
 *    Tailwind 3.4 with HSL triplets in `src/index.css`, and has no
 *    `--destructive` token at all. None of that CSS was added; the component
 *    uses tokens that already exist.
 *
 * 4. The three remote images (two 21st.dev CDN photographs and a CDN Google
 *    icon) are replaced. The panels use the project's own `hero-bg.jpg` and
 *    `phase-two-bg.jpg`, and the Google mark is inlined as SVG — so the page
 *    renders offline and pulls nothing from a third-party CDN.
 *
 * 5. The Typewriter's loop branch scheduled a nested `setTimeout` that was
 *    never cleared, so it could fire after unmount. It is tracked and cleared.
 *
 * THE ANIMATION IS KEPT. The typed quote and its pulsing cursor are the point
 * of the right-hand panel and are preserved exactly, as is the 500ms
 * cross-fade when the panel swaps between sign-in and sign-up.
 * ---------------------------------------------------------------------------
 */

import * as React from "react";
import { useEffect, useId, useRef, useState } from "react";
import { Eye, EyeOff } from "lucide-react";

import heroBg from "@/assets/hero-bg.jpg";
import phaseTwoBg from "@/assets/phase-two-bg.jpg";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ *
 * Typewriter
 * ------------------------------------------------------------------ */

export interface TypewriterProps {
  text: string | string[];
  speed?: number;
  cursor?: string;
  loop?: boolean;
  deleteSpeed?: number;
  delay?: number;
  className?: string;
}

export function Typewriter({
  text,
  speed = 100,
  cursor = "|",
  loop = false,
  deleteSpeed = 50,
  delay = 1500,
  className,
}: TypewriterProps) {
  const [displayText, setDisplayText] = useState("");
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isDeleting, setIsDeleting] = useState(false);
  const [textArrayIndex, setTextArrayIndex] = useState(0);
  /* The pause before deleting is a second, nested timer. Tracking it is what
     stops it firing into an unmounted component. */
  const pauseTimer = useRef<ReturnType<typeof setTimeout>>();

  const textArray = Array.isArray(text) ? text : [text];
  const currentText = textArray[textArrayIndex] || "";

  useEffect(() => {
    if (!currentText) return;

    const timeout = setTimeout(
      () => {
        if (!isDeleting) {
          if (currentIndex < currentText.length) {
            setDisplayText((prev) => prev + currentText[currentIndex]);
            setCurrentIndex((prev) => prev + 1);
          } else if (loop) {
            pauseTimer.current = setTimeout(() => setIsDeleting(true), delay);
          }
        } else {
          if (displayText.length > 0) {
            setDisplayText((prev) => prev.slice(0, -1));
          } else {
            setIsDeleting(false);
            setCurrentIndex(0);
            setTextArrayIndex((prev) => (prev + 1) % textArray.length);
          }
        }
      },
      isDeleting ? deleteSpeed : speed,
    );

    return () => {
      clearTimeout(timeout);
      clearTimeout(pauseTimer.current);
    };
  }, [
    currentIndex,
    isDeleting,
    currentText,
    loop,
    speed,
    deleteSpeed,
    delay,
    displayText,
    textArray.length,
  ]);

  return (
    <span className={className}>
      {displayText}
      <span className="animate-pulse">{cursor}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * Password field
 * ------------------------------------------------------------------ */

export interface PasswordInputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

const PasswordInput = React.forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className, label, ...props }, ref) => {
    const id = useId();
    const [showPassword, setShowPassword] = useState(false);

    return (
      <div className="grid w-full items-center gap-2">
        {label && <Label htmlFor={id}>{label}</Label>}
        <div className="relative">
          <Input
            id={id}
            type={showPassword ? "text" : "password"}
            className={cn("pe-10", className)}
            ref={ref}
            {...props}
          />
          <button
            type="button"
            onClick={() => setShowPassword((prev) => !prev)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            aria-pressed={showPassword}
            className="absolute inset-y-0 end-0 flex h-full w-10 items-center justify-center text-muted-foreground transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
          >
            {showPassword ? (
              <EyeOff className="size-4" aria-hidden="true" />
            ) : (
              <Eye className="size-4" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
    );
  },
);
PasswordInput.displayName = "PasswordInput";

/* ------------------------------------------------------------------ *
 * Forms
 * ------------------------------------------------------------------ */

/** The Google wordmark, inlined so the page pulls nothing from a CDN. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className="size-4">
      <path
        fill="#4285F4"
        d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z"
      />
      <path
        fill="#34A853"
        d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z"
      />
      <path
        fill="#FBBC05"
        d="M11.69 28.18A13.2 13.2 0 0 1 11 24c0-1.45.25-2.86.69-4.18v-5.7H4.34A21.99 21.99 0 0 0 2 24c0 3.55.85 6.91 2.34 9.88l7.35-5.7z"
      />
      <path
        fill="#EA4335"
        d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z"
      />
    </svg>
  );
}

interface AuthFormProps {
  onSubmit: (email: string, password: string) => Promise<void>;
  busy: boolean;
}

/**
 * The outcome of the last attempt, in Supabase's own words.
 *
 * Three tones, because the three outcomes are genuinely different: a failure
 * is rose, "check your email" is amber (nothing is wrong, but nothing is
 * finished either), and nothing at all renders on success — the app navigates
 * instead, which is a better signal than a message.
 */
function AuthNotice({
  message,
  tone,
}: {
  message: string | null;
  tone: "error" | "info";
}) {
  if (!message) return null;
  return (
    <p
      role="status"
      className={cn(
        "rounded-lg border px-3.5 py-2.5 text-[12px] leading-relaxed",
        tone === "error"
          ? "border-rose-500/40 bg-rose-500/[0.07] text-rose-200/90"
          : "border-amber-500/35 bg-amber-500/[0.07] text-amber-200/90",
      )}
    >
      {message}
    </p>
  );
}

function SignInForm({ onSubmit, busy }: AuthFormProps) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void onSubmit(String(data.get("email")), String(data.get("password")));
      }}
      autoComplete="on"
      className="flex flex-col gap-8"
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="font-display text-2xl font-bold tracking-tight text-foreground">
          Sign in to your account
        </h1>
        <p className="text-balance text-sm text-muted-foreground">
          Enter your email below to sign in
        </p>
      </div>
      <div className="grid gap-5">
        <div className="grid gap-2">
          <Label htmlFor="signin-email">Email</Label>
          <Input
            id="signin-email"
            name="email"
            type="email"
            placeholder="you@example.com"
            required
            autoComplete="email"
          />
        </div>
        <PasswordInput
          name="password"
          label="Password"
          required
          autoComplete="current-password"
          placeholder="Password"
        />
        <Button type="submit" variant="outline" className="mt-2" disabled={busy}>
          {busy ? "Signing in…" : "Sign In"}
        </Button>
      </div>
    </form>
  );
}

function SignUpForm({ onSubmit, busy }: AuthFormProps) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void onSubmit(String(data.get("email")), String(data.get("password")));
      }}
      autoComplete="on"
      className="flex flex-col gap-8"
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="font-display text-2xl font-bold tracking-tight text-foreground">
          Create an account
        </h1>
        <p className="text-balance text-sm text-muted-foreground">
          Enter your details below to sign up
        </p>
      </div>
      <div className="grid gap-5">
        <div className="grid gap-2">
          <Label htmlFor="signup-name">Full name</Label>
          <Input
            id="signup-name"
            name="name"
            type="text"
            placeholder="Jordan Rivera"
            required
            autoComplete="name"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="signup-email">Email</Label>
          <Input
            id="signup-email"
            name="email"
            type="email"
            placeholder="you@example.com"
            required
            autoComplete="email"
          />
        </div>
        <PasswordInput
          name="password"
          label="Password"
          required
          autoComplete="new-password"
          placeholder="Password"
        />
        <Button type="submit" variant="outline" className="mt-2" disabled={busy}>
          {busy ? "Creating…" : "Sign Up"}
        </Button>
      </div>
    </form>
  );
}

function AuthFormContainer({
  isSignIn,
  onToggle,
  onAuthenticated,
}: {
  isSignIn: boolean;
  onToggle: () => void;
  onAuthenticated?: () => void;
}) {
  const { signIn, signUp } = useAuth();
  const [notice, setNotice] = useState<string | null>(null);
  const [tone, setTone] = useState<"error" | "info">("error");
  const [busy, setBusy] = useState(false);

  /**
   * One handler for both forms.
   *
   * On success it does NOT set a message: the caller navigates, and a
   * "signed in" banner that flashes for 200ms before the page changes is
   * noise. Only the two outcomes that keep the user here say anything.
   */
  async function attempt(email: string, password: string) {
    setBusy(true);
    setNotice(null);
    const result = isSignIn
      ? await signIn(email, password)
      : await signUp(email, password);
    setBusy(false);

    if (!result.ok) {
      setTone("error");
      setNotice(result.message ?? "That did not work. Please try again.");
      return;
    }
    if (result.needsEmailConfirmation) {
      setTone("info");
      setNotice(result.message ?? "Check your email to confirm the account.");
      return;
    }
    onAuthenticated?.();
  }

  return (
    <div className="mx-auto grid w-[350px] gap-4">
      {isSignIn ? (
        <SignInForm onSubmit={attempt} busy={busy} />
      ) : (
        <SignUpForm onSubmit={attempt} busy={busy} />
      )}

      <AuthNotice message={notice} tone={tone} />

      <div className="text-center text-sm text-muted-foreground">
        {isSignIn ? "Don't have an account?" : "Already have an account?"}{" "}
        <Button
          variant="link"
          size="sm"
          className="h-auto px-1"
          onClick={() => {
            setNotice(null);
            onToggle();
          }}
        >
          {isSignIn ? "Sign up" : "Sign in"}
        </Button>
      </div>

      <div className="relative text-center text-sm after:absolute after:inset-0 after:top-1/2 after:z-0 after:flex after:items-center after:border-t after:border-border">
        <span className="relative z-10 bg-background px-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          Or continue with
        </span>
      </div>

      {/* Layout only. There is no OAuth client and no provider behind this. */}
      {/* Layout only: no OAuth client is configured for this project, so the
          honest behaviour is to say so rather than start a flow that cannot
          finish. It becomes real by enabling the Google provider in Supabase
          and calling signInWithOAuth here. */}
      <Button
        variant="outline"
        type="button"
        onClick={() => {
          setTone("info");
          setNotice("Google sign-in is not configured for this project yet.");
        }}
      >
        <GoogleMark />
        Continue with Google
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * The two-panel composition
 * ------------------------------------------------------------------ */

interface AuthContentProps {
  image?: { src: string; alt: string };
  quote?: { text: string; author: string };
}

export interface AuthUIProps {
  signInContent?: AuthContentProps;
  signUpContent?: AuthContentProps;
  /** Called once a session exists. The page decides where to go next. */
  onAuthenticated?: () => void;
}

/* The panels use the project's own section grounds rather than a stock photo
   from a CDN, so the page matches the rest of the product and renders with no
   network access. */
const defaultSignInContent = {
  image: { src: heroBg, alt: "A ridgeline under heavy weather" },
  quote: {
    text: "Welcome back. The pipeline is where you left it.",
    author: "DETROIT-AI",
  },
};

const defaultSignUpContent = {
  image: { src: phaseTwoBg, alt: "Low light across industrial forms" },
  quote: {
    text: "One prompt in. A reviewed page out.",
    author: "DETROIT-AI",
  },
};

export function AuthUI({
  signInContent = {},
  signUpContent = {},
  onAuthenticated,
}: AuthUIProps) {
  const [isSignIn, setIsSignIn] = useState(true);

  const finalSignIn = {
    image: { ...defaultSignInContent.image, ...signInContent.image },
    quote: { ...defaultSignInContent.quote, ...signInContent.quote },
  };
  const finalSignUp = {
    image: { ...defaultSignUpContent.image, ...signUpContent.image },
    quote: { ...defaultSignUpContent.quote, ...signUpContent.quote },
  };

  const current = isSignIn ? finalSignIn : finalSignUp;

  return (
    <div className="w-full bg-background md:grid md:min-h-screen md:grid-cols-2">
      {/* The browser's own password reveal would sit on top of ours. */}
      <style>{`
        input[type="password"]::-ms-reveal,
        input[type="password"]::-ms-clear {
          display: none;
        }
      `}</style>

      <div className="flex min-h-screen items-center justify-center p-6 md:min-h-0 md:p-0 md:py-12">
        <AuthFormContainer
          isSignIn={isSignIn}
          onToggle={() => setIsSignIn((prev) => !prev)}
          onAuthenticated={onAuthenticated}
        />
      </div>

      <div
        role="img"
        aria-label={current.image.alt}
        className="relative hidden bg-cover bg-center transition-all duration-500 ease-in-out md:block"
        style={{ backgroundImage: `url(${current.image.src})` }}
        key={current.image.src}
      >
        {/* Two scrims, not one. The photographs are bright at the top, and the
            quote sits at the bottom over the busiest part of each frame — a
            single fade left the cursor competing with the texture behind it. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-background/35"
        />
        <div
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-background via-background/80 to-transparent"
        />

        <div className="relative z-10 flex h-full flex-col items-center justify-end p-6 pb-10">
          <blockquote className="space-y-2 text-center text-foreground">
            <p className="text-lg font-medium">
              “
              <Typewriter
                key={current.quote.text}
                text={current.quote.text}
                speed={60}
              />
              ”
            </p>
            <cite className="block font-mono text-[10px] uppercase not-italic tracking-[0.18em] text-muted-foreground">
              — {current.quote.author}
            </cite>
          </blockquote>
        </div>
      </div>
    </div>
  );
}
