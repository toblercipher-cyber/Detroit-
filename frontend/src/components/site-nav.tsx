/**
 * Site navigation for the Landing Page.
 *
 * Why this file exists:
 * It owns the sticky header: the brand mark, the in-page section links, and the
 * entry point into the builder.
 *
 * Layer: frontend presentation (shared chrome).
 *
 * The "Open builder" control is the `/chat` CTA that `docs/frontend.md` §4.4
 * specifies this component should gain. It is a plain anchor, not a router
 * link, because `react-router-dom` is not installed yet (§4.1) — `App.tsx`
 * currently selects the surface from `location.pathname`. When the real router
 * lands, this becomes a `<Link>` and nothing else here changes.
 *
 * Unlike the section links and "Get early access", this control stays visible
 * below `xl` instead of hiding behind the menu toggle: it is the way into the
 * product, and on a 1024px laptop the collapsed nav otherwise left no visible
 * call to action at all.
 */

import { useState } from "react";
import { Menu, X } from "lucide-react";

import { Button } from "@/components/ui/button";

/* In-page section anchors only. Page destinations live in the right-hand
   cluster instead: this list is hidden below `xl`, so a page link placed here
   is invisible at ordinary laptop widths. */
const LINKS = [
  { label: "Pipeline", href: "#pipeline" },
  { label: "Agents", href: "#agents" },
  { label: "About", href: "#about" },
  { label: "System", href: "#dashboard" },
  { label: "Harness", href: "#harness" },
  { label: "Connect", href: "#mcp" },
];

export function SiteNav() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 border-b border-white/10 bg-background/40 backdrop-blur-xl backdrop-saturate-150 supports-[backdrop-filter]:bg-background/25">
      <div className="container flex h-20 items-center justify-between gap-4">
        <a
          href="#top"
          className="shrink-0 font-display text-lg font-bold tracking-tight text-foreground"
        >
          DETROIT<span className="text-ember">-AI</span>
        </a>

        <nav className="hidden items-center gap-5 xl:flex">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-4 sm:gap-6">
          <a
            href="#about"
            className="hidden font-mono text-xs uppercase tracking-[0.18em] text-foreground underline decoration-ember decoration-2 underline-offset-8 transition-colors hover:text-ember xl:block"
          >
            Get early access
          </a>

          {/* Secondary: for someone who already has builds. Hidden on the
              narrowest phones so the logo, primary action and menu toggle do
              not collide; it is in the drawer there instead. */}
          <Button asChild size="sm" variant="outline" className="hidden sm:inline-flex">
            <a href="/dashboard">Dashboard</a>
          </Button>

          <Button asChild size="sm">
            <a href="/chat">Open builder</a>
          </Button>

          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label="Toggle menu"
            className="flex h-10 w-10 items-center justify-center text-foreground xl:hidden"
          >
            {open ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>

      {open && (
        <div className="container pb-6 xl:hidden">
          <div className="flex flex-col gap-5 border-t border-border pt-6">
            {LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="font-mono text-sm uppercase tracking-[0.18em] text-muted-foreground hover:text-foreground"
              >
                {link.label}
              </a>
            ))}
            {/* The phone-width home for the Dashboard action hidden above. */}
            <a
              href="/dashboard"
              onClick={() => setOpen(false)}
              className="font-mono text-sm uppercase tracking-[0.18em] text-muted-foreground hover:text-foreground sm:hidden"
            >
              Dashboard
            </a>
            <a
              href="#about"
              onClick={() => setOpen(false)}
              className="font-mono text-sm uppercase tracking-[0.18em] text-ember"
            >
              Get early access
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
