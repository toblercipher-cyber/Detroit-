import { Play } from "lucide-react";

import heroBg from "@/assets/hero-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";

export function DemoVideoSection() {
  return (
    <section
      id="demo"
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(10,8,7,0.9) 0%, rgba(10,8,7,0.75) 30%, rgba(10,8,7,0.9) 78%, rgba(10,8,7,0.98) 100%), url(${heroBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 55%",
      }}
    >
      <div className="mx-auto max-w-6xl">
        <div className="max-w-2xl">
          <AnimatedText
            as="h2"
            text="See it run, start to finish."
            className="font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl"
          />
          <AnimatedText
            as="p"
            text="A walkthrough of the full pipeline — from a single prompt to a page running on your own machine — is on its way."
            delay={0.2}
            stagger={0.014}
            className="mt-5 max-w-md text-base leading-relaxed text-foreground/80"
          />
        </div>

        <div className="mt-12 flex aspect-video w-full items-center justify-center overflow-hidden rounded-3xl border border-dashed border-white/20 bg-white/[0.03] backdrop-blur-sm">
          <div className="flex flex-col items-center gap-4 text-center">
            <span className="flex h-16 w-16 items-center justify-center rounded-full border border-white/20 bg-black/40 text-foreground">
              <Play size={24} className="ml-1" />
            </span>
            <p className="font-mono text-xs uppercase tracking-[0.3em] text-foreground/60">
              Demo video — coming soon
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
