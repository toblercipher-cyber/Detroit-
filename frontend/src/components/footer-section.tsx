/**
 * Landing Page closing section: the page's single conversion moment.
 *
 * Why this file exists:
 * This is the last surface a visitor sees, so it owns the one real call to
 * action on the Landing Page. It mounts the `#access` anchor that
 * `docs/frontend.md` §4.5 lists as a landing anchor — previously that anchor
 * was documented but rendered nowhere, because the only component carrying it
 * (`cta-form.tsx`) has no importers.
 *
 * Layer: frontend presentation (Landing Page section component).
 *
 * Design notes, per `docs/design.md` §2:
 * - Accent discipline. Ember marks the action — the submit button and the
 *   eyebrow — rather than filling area. The previous ember gradient slab made
 *   the accent decorative, which the §5.4 anti-slop checklist flags as an
 *   unnecessary gradient.
 * - Motion language. Headings reveal through `AnimatedText`, the same as every
 *   other section, which also inherits the `prefers-reduced-motion` handling
 *   already built into `useInView`.
 *
 * The form has no backend. Submission is local state only — see
 * `docs/frontend.md` §4.6: the Landing Page makes no backend calls.
 */

import { useState, type FormEvent } from "react";
import { ArrowUpRight, Check } from "lucide-react";

import heroBg from "@/assets/hero-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/* Mirrors the SiteNav link set so the two never drift. Every target is a real
   section on this page — no placeholder hrefs. */
const NAVIGATE_LINKS = [
  { label: "Pipeline", href: "#pipeline" },
  { label: "Agents", href: "#agents" },
  { label: "About", href: "#about" },
  { label: "System", href: "#dashboard" },
  { label: "Harness", href: "#harness" },
  { label: "Connect", href: "#mcp" },
];

export function FooterSection({
  title = "Ready to ship your next build?",
}: {
  title?: string;
}) {
  const [submitted, setSubmitted] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitted(true);
  }

  return (
    <footer
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(10,8,7,0.94) 0%, rgba(10,8,7,0.88) 30%, rgba(10,8,7,0.96) 78%, rgba(10,8,7,1) 100%), url(${heroBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 55%",
      }}
    >
      <div className="mx-auto max-w-6xl">
        {/* The call to action. `#access` resolves here. */}
        <div
          id="access"
          className="scroll-mt-24 rounded-2xl border border-border bg-card/70 p-8 backdrop-blur-sm sm:p-12 lg:p-14"
        >
          <div className="grid grid-cols-1 gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:items-end lg:gap-16">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.18em] text-ember">
                Early access
              </p>
              <AnimatedText
                as="h2"
                text={title}
                className="mt-4 font-display text-3xl font-bold leading-[1.1] tracking-tight text-foreground sm:text-4xl"
              />
              <AnimatedText
                as="p"
                text="Describe the page you need. The pipeline plans, designs, builds, reviews and secures it — and you get a live page back."
                delay={0.2}
                stagger={0.014}
                className="mt-5 max-w-md text-base leading-relaxed text-foreground/80"
              />
            </div>

            <div>
              {submitted ? (
                <div
                  role="status"
                  className="flex items-center gap-3 rounded-lg border border-border bg-background/60 px-5 py-4"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ember text-primary-foreground">
                    <Check size={16} strokeWidth={3} />
                  </span>
                  <p className="text-sm text-foreground">
                    You&apos;re on the list — we&apos;ll be in touch.
                  </p>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className="max-w-md">
                  <label htmlFor="access-email" className="sr-only">
                    Email address
                  </label>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                    <Input
                      id="access-email"
                      name="email"
                      type="email"
                      autoComplete="email"
                      placeholder="jordan@studio.com"
                      required
                      className="h-11 border-b-foreground/25 sm:flex-1"
                    />
                    <Button type="submit" className="group shrink-0">
                      Get early access
                      <ArrowUpRight
                        size={18}
                        className="transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                      />
                    </Button>
                  </div>
                  <p className="mt-4 font-mono text-xs leading-relaxed tracking-[0.08em] text-muted-foreground">
                    No spam. One email when your access is ready.
                  </p>
                </form>
              )}
            </div>
          </div>
        </div>

        {/* Identity + real in-page navigation. */}
        <div className="mt-16 flex flex-col gap-10 border-t border-border pt-10 sm:flex-row sm:justify-between sm:gap-8">
          <div className="max-w-xs">
            <a
              href="#top"
              className="font-display text-lg font-bold tracking-tight text-foreground"
            >
              DETROIT<span className="text-ember">-AI</span>
            </a>
            <p className="mt-3 text-sm leading-relaxed text-foreground/60">
              A prompt-to-page pipeline — planned, designed, built, reviewed and
              secured before it reaches you.
            </p>
            <p className="mt-6 text-sm text-foreground/40">
              &copy; {new Date().getFullYear()} DETROIT-AI. All rights reserved.
            </p>
          </div>

          <nav aria-label="Page sections">
            <h3 className="font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">
              Navigate
            </h3>
            <ul className="mt-4 grid grid-cols-2 gap-x-10 gap-y-3 sm:grid-cols-1 sm:gap-y-2.5">
              {NAVIGATE_LINKS.map((link) => (
                <li key={link.href}>
                  <a
                    href={link.href}
                    className="font-mono text-xs uppercase tracking-[0.12em] text-foreground/60 transition-colors hover:text-ember"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </div>
    </footer>
  );
}
