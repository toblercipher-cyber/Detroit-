/**
 * Compact footer bar for the application surfaces.
 *
 * Why this file exists:
 * The Landing Page closes with `footer-section` and the Product page closes
 * with `shutter-glyph-footer` — both tall marketing footers. `/chat`,
 * `/dashboard` and `/login` had no footer at all, so the brand, the copyright
 * and the way back out of a working surface simply stopped existing once you
 * signed in.
 *
 * Layer: frontend presentation (shared chrome).
 *
 * Why this is a bar and not one of the two existing footers:
 * `/chat` and `/dashboard` are `h-screen overflow-hidden` shells. They do not
 * scroll as a page — their rails and composer are fixed and only the centre
 * column scrolls — so a tall footer either gets clipped by `overflow-hidden`
 * or forces the shell to scroll, which breaks the fixed panes. `/login` is a
 * two-panel grid where each panel already fills the viewport. A full-bleed
 * wordmark has nowhere to go on any of the three.
 *
 * Consistency here means a shared vocabulary, not a copied block: the same
 * Space Mono caps at `tracking-[0.14em]`, the same `border-border` hairline,
 * the same ember hover and the same `DETROIT-AI` lockup the nav and both
 * footers use — at a density an app shell can carry.
 */

import { cn } from "@/lib/utils";

export interface FooterBarLink {
  label: string;
  href: string;
}

/**
 * The default set. Deliberately excludes "Sign in": every surface that uses
 * this default is one you reach while signed in, and `/chat` and `/dashboard`
 * are guarded, so offering sign-in there is either redundant or a loop.
 * `/login` passes its own set for the same reason in reverse.
 */
const DEFAULT_LINKS: readonly FooterBarLink[] = [
  { label: "Home", href: "/" },
  { label: "Product", href: "/features" },
  { label: "Builder", href: "/chat" },
  { label: "Dashboard", href: "/dashboard" },
];

export function AppFooterBar({
  current,
  links = DEFAULT_LINKS,
  showBrand = true,
  className,
}: {
  /** Href of the surface being rendered; dropped from the link list. */
  current?: string;
  links?: readonly FooterBarLink[];
  /** `/login` already floats the lockup top-left, so it turns this off. */
  showBrand?: boolean;
  className?: string;
}) {
  const visible = links.filter((link) => link.href !== current);

  return (
    <footer
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-border px-4 py-3 sm:px-6",
        className,
      )}
    >
      {showBrand ? (
        <div className="flex items-center gap-3">
          <a
            href="/"
            className="font-display text-sm font-bold tracking-tight text-foreground"
          >
            DETROIT<span className="text-ember">-AI</span>
          </a>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            &copy; {new Date().getFullYear()}
          </p>
        </div>
      ) : (
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          &copy; {new Date().getFullYear()} DETROIT-AI
        </p>
      )}

      <nav aria-label="Footer">
        <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {visible.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-ember"
              >
                {link.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </footer>
  );
}
