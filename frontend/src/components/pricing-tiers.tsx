/**
 * Pricing tiers — a fanned deck of subscription plans.
 *
 * Why this file exists:
 * The Product page needs a price list. A fan keeps all four tiers on screen at
 * once while still reading as one deck, which a flat four-column grid does not
 * at laptop widths. It lives in `components/` rather than `components/ui/`
 * because the tiers and their copy are product facts, not a reusable primitive.
 *
 * Layer: frontend presentation (section component).
 *
 * ---------------------------------------------------------------------------
 * THE FAN IS REBUILT, NOT VENDORED
 *
 * The reference for this look was `card-fan-carousel.tsx` (gsap). It could not
 * be dropped in, for four reasons, each verified before this was written:
 *
 *   1. It renders IMAGES ONLY. Its item type is `{ imgUrl, alt?, linkUrl? }`
 *      and each card is a single `<img>` — there is no children slot, so a
 *      price, a feature list and a CTA have nowhere to go.
 *   2. It ships NO CSS for `.fan-card` / `.fan-layout`. Those classes carry
 *      the card's size and absolute positioning; gsap only writes transforms.
 *      Without them every card collapses to 0×0.
 *   3. `gsap` is not a dependency here, and the motion below does not need it.
 *   4. Its controls are styled with `dark:` variants, which are inert in this
 *      project — `darkMode: ["class"]` with no `dark` class on `<html>` — so
 *      they would have rendered near-black on a near-black ground.
 *
 * So the geometry is reimplemented with CSS transforms: same fan, same hover
 * lift and neighbour push, no new runtime dependency, and cards that hold real
 * content.
 *
 * Below `lg` the fan is abandoned for a snapping scroller. Four rotated cards
 * do not fit a phone, and a scroller is the honest control there.
 *
 * NOT `components/ui/pricing-card.tsx`: `docs/design.md` §4 records its
 * treatment as open decision D-1 (glassmorphism, which §5.4 flags), and §4
 * says that may not be settled during implementation. This builds from
 * primitives instead — option (c) — and leaves that file untouched.
 *
 * NO BACKEND. Every CTA is a plain anchor into `/chat`. There is no billing
 * API and no checkout; these are display prices.
 * ---------------------------------------------------------------------------
 */

import { useEffect, useState } from "react";
import { Check, ChevronLeft, ChevronRight } from "lucide-react";

import featuresPricingBg from "@/assets/features-pricing-bg.jpg";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Pakistani rupees. `en-PK` renders PKR as "Rs 1,000" rather than the
    three-letter code `en-US` would give. No decimals: these are whole-rupee
    subscription prices. */
const pkr = new Intl.NumberFormat("en-PK", {
  style: "currency",
  currency: "PKR",
  maximumFractionDigits: 0,
});

type Tier = {
  name: string;
  price: number;
  credits: string;
  blurb: string;
  features: string[];
  featured?: boolean;
};

/**
 * The range runs Rs 1,000 → Rs 15,000, split across four tiers.
 *
 * Credit allowances are the unit the Dashboard already meters, and Studio's
 * 2,500 deliberately matches the allowance shown there, so the two surfaces
 * do not contradict each other.
 */
const TIERS: Tier[] = [
  {
    name: "Starter",
    price: 1000,
    credits: "500 credits / month",
    blurb: "One page at a time, start to finish.",
    features: ["All twelve pipeline stages", "Human review gates", "Impeccable quality gate"],
  },
  {
    name: "Studio",
    price: 4000,
    credits: "2,500 credits / month",
    blurb: "The working plan for a small team.",
    features: [
      "Everything in Starter",
      "Reference-image analysis",
      "MCP connections",
      "Usage dashboard",
    ],
    featured: true,
  },
  {
    name: "Scale",
    price: 9000,
    credits: "6,000 credits / month",
    blurb: "Several projects running at once.",
    features: ["Everything in Studio", "Parallel runs", "Priority build queue", "Run history export"],
  },
  {
    name: "Agency",
    price: 15000,
    credits: "12,000 credits / month",
    blurb: "Client work, many surfaces, one pipeline.",
    features: ["Everything in Scale", "Unlimited projects", "Shared workspaces", "Dedicated support"],
  },
];

const FEATURED = TIERS.findIndex((t) => t.featured);
const MID = (TIERS.length - 1) / 2;

/** Fan geometry for one card. `active` is the raised card, if any. */
function fanStyle(index: number, active: number | null) {
  const d = index - MID; // -1.5 … 1.5 for four cards
  // Spread is deliberately tighter than the raw card pitch suggests: rotating
  // about `origin-bottom` swings each card's visual centre outward on top of
  // its translation, so 15rem/9deg put the outermost card past the viewport.
  let rot = d * 8;
  let x = d * 13;
  let y = d * d * 0.9;
  let scale = 1 - 0.05 * d * d;
  let z = 10 - Math.round(Math.abs(d) * 2);

  if (active !== null) {
    if (index === active) {
      // The raised card straightens up and steps forward.
      rot = 0;
      y -= 2;
      scale += 0.07;
      z = 30;
    } else {
      // Neighbours part to make room, more the closer they are.
      const dir = index < active ? -1 : 1;
      const gap = Math.abs(index - active);
      x += (dir * 2.2) / gap;
      rot += (dir * 2) / gap;
    }
  }

  return {
    // The `-50%` is part of THIS transform on purpose. The card sits at
    // `left: 50%`, and Tailwind's `-translate-x-1/2` is itself a transform —
    // an inline `transform` replaces it wholesale rather than composing with
    // it, so centring has to be folded in here. Leaving it to the class
    // shifted every card half its width right and pushed the last one past
    // the viewport.
    transform: `translate(calc(-50% + ${x}rem), ${y}rem) rotate(${rot}deg) scale(${scale})`,
    zIndex: z,
  };
}

function TierCard({ tier, className }: { tier: Tier; className?: string }) {
  return (
    <div
      className={cn(
        "flex h-full flex-col rounded-2xl border bg-card p-6",
        tier.featured ? "border-ember/45" : "border-border",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          {tier.name}
        </p>
        {tier.featured && (
          <span className="rounded-full border border-ember/50 px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.14em] text-ember">
            Most picked
          </span>
        )}
      </div>

      <p className="mt-5 font-display text-[2rem] font-bold leading-none tracking-tight tabular-nums text-foreground">
        {pkr.format(tier.price)}
        <span className="ml-1.5 font-sans text-xs font-normal tracking-normal text-muted-foreground">
          / month
        </span>
      </p>
      <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ember">
        {tier.credits}
      </p>

      <p className="mt-4 text-[13px] leading-relaxed text-muted-foreground">{tier.blurb}</p>

      <ul className="mt-6 space-y-2.5">
        {tier.features.map((f) => (
          <li key={f} className="flex items-start gap-2.5 text-[13px] text-foreground/80">
            <Check size={14} className="mt-0.5 shrink-0 text-ember" />
            {f}
          </li>
        ))}
      </ul>

      <div className="mt-auto pt-8">
        <Button asChild size="sm" variant={tier.featured ? "default" : "outline"} className="w-full">
          <a href="/chat">Start with {tier.name}</a>
        </Button>
      </div>
    </div>
  );
}

export function PricingTiers() {
  const [isFan, setIsFan] = useState(false);
  const [reduced, setReduced] = useState(false);
  const [selected, setSelected] = useState(FEATURED);
  const [hovered, setHovered] = useState<number | null>(null);

  // The fan needs real width; below `lg` the scroller takes over instead.
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 1024px)");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => {
      setIsFan(wide.matches);
      setReduced(motion.matches);
    };
    sync();
    wide.addEventListener("change", sync);
    motion.addEventListener("change", sync);
    // `resize` as well as the media query: the query alone is enough in a real
    // browser, but it is the only signal here, and a missed `change` leaves
    // the fan rendering at phone width where it does not fit. `sync` only
    // writes state when the value actually differs, so this costs nothing.
    window.addEventListener("resize", sync);
    return () => {
      wide.removeEventListener("change", sync);
      motion.removeEventListener("change", sync);
      window.removeEventListener("resize", sync);
    };
  }, []);

  const active = hovered ?? selected;
  const step = (d: -1 | 1) =>
    setSelected((s) => (s + d + TIERS.length) % TIERS.length);

  return (
    <section
      id="pricing"
      aria-labelledby="pricing-heading"
      className="relative isolate scroll-mt-20 overflow-hidden border-t border-border px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10 bg-cover bg-center"
        style={{ backgroundImage: `url(${featuresPricingBg})` }}
      />
      {/* Light scrim: this source is almost entirely black, so the heavier
          alphas used on the hero would erase it. ~65% transmission through the
          middle, clamped hard at both edges so the section still blends into
          the trace above and the closing CTA below. The tier cards are opaque
          `bg-card`, so prices never sit on the photograph. */}
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10"
        style={{
          background:
            "linear-gradient(180deg, rgba(17,15,14,0.96) 0%, rgba(17,15,14,0.45) 20%, rgba(17,15,14,0.35) 58%, rgba(17,15,14,0.86) 88%, rgba(17,15,14,0.99) 100%)",
        }}
      />

      <div className="mx-auto max-w-6xl">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-ember">Pricing</p>
            <h2
              id="pricing-heading"
              className="mt-5 max-w-2xl font-display text-3xl font-bold leading-[1.1] tracking-tight sm:text-4xl"
            >
              From {pkr.format(1000)} to {pkr.format(15000)} a month.
            </h2>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-muted-foreground">
              Every plan runs the same twelve-stage pipeline and the same quality
              gate. What changes is how many builds you get.
            </p>
          </div>

          {isFan && (
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={() => step(-1)}
                aria-label="Raise the previous plan"
                className="flex size-9 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:border-foreground/25 hover:text-foreground"
              >
                <ChevronLeft size={16} />
              </button>
              <button
                type="button"
                onClick={() => step(1)}
                aria-label="Raise the next plan"
                className="flex size-9 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:border-foreground/25 hover:text-foreground"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          )}
        </div>

        {isFan ? (
          /* ---------- Fan, lg and up ---------- */
          <ul
            aria-label="Subscription plans"
            onMouseLeave={() => setHovered(null)}
            className="relative mt-16 h-[34rem]"
          >
            {TIERS.map((tier, i) => (
              <li
                key={tier.name}
                onMouseEnter={() => setHovered(i)}
                onFocus={() => setSelected(i)}
                style={{
                  ...fanStyle(i, active),
                  transitionDuration: reduced ? "0ms" : "520ms",
                  // Inline rather than an arbitrary-value easing utility:
                  // Tailwind calls that form ambiguous, warns at build time
                  // and may not emit it at all. Same expo-out curve that
                  // `tailwind.config.ts` uses for `fade-up`, so the deck
                  // decelerates like everything else on the page.
                  transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
                }}
                className={cn(
                  "absolute left-1/2 top-0 h-[31rem] w-[17.5rem] origin-bottom",
                  // Timing function is set inline (see the style prop above):
                  // an arbitrary-value easing utility is ambiguous to
                  // Tailwind, which warns and may not emit it.
                  "transition-transform will-change-transform",
                )}
              >
                <TierCard
                  tier={tier}
                  className={cn(
                    "shadow-[0_24px_60px_-30px_rgba(0,0,0,0.9)] transition-colors",
                    i !== active && "border-border",
                  )}
                />
              </li>
            ))}
          </ul>
        ) : (
          /* ---------- Scroller, below lg ---------- */
          <ul
            tabIndex={0}
            aria-label="Subscription plans"
            className={cn(
              "mt-12 flex snap-x snap-mandatory gap-4 overflow-x-auto pb-2",
              "[scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ember focus-visible:ring-offset-4 focus-visible:ring-offset-background",
            )}
          >
            {TIERS.map((tier) => (
              <li key={tier.name} className="w-[280px] shrink-0 snap-start sm:w-[300px]">
                <TierCard tier={tier} />
              </li>
            ))}
          </ul>
        )}

        <p className="mt-6 font-mono text-[10px] uppercase leading-relaxed tracking-[0.14em] text-muted-foreground/70">
          Display prices · no checkout is wired up yet
        </p>
      </div>
    </section>
  );
}
