import { useState } from "react";
import { Menu, X } from "lucide-react";

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
      <div className="container flex h-20 items-center justify-between">
        <a
          href="#top"
          className="font-display text-lg font-bold tracking-tight text-foreground"
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

        <a
          href="#about"
          className="hidden font-mono text-xs uppercase tracking-[0.18em] text-foreground underline decoration-ember decoration-2 underline-offset-8 transition-colors hover:text-ember xl:block"
        >
          Get early access
        </a>

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
