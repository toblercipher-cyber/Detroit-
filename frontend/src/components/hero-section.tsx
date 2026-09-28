import { ArrowUpRight } from "lucide-react";

import heroBg from "@/assets/hero-bg.jpg";
import { Button } from "@/components/ui/button";
import { SiteNav } from "@/components/site-nav";
import { PipelineDemo } from "@/components/pipeline-demo";

const STAGES = ["Plan", "Design", "Build", "Review", "Secure"];

export function HeroSection() {
  return (
    <div id="top" className="bg-background">
      <SiteNav />

      {/* Phase one: the pitch, full-bleed on the mountain */}
      <section
        className="relative flex min-h-[calc(100vh-5rem)] flex-col justify-center overflow-hidden px-6 py-10 sm:px-10 lg:px-14"
        style={{
          backgroundImage: `linear-gradient(180deg, rgba(10,8,7,0.55) 0%, rgba(10,8,7,0.32) 32%, rgba(10,8,7,0.82) 76%, rgba(10,8,7,0.96) 100%), url(${heroBg})`,
          backgroundSize: "cover",
          backgroundPosition: "center 55%",
        }}
      >
        <div className="mx-auto grid w-full max-w-6xl grid-cols-1 items-center gap-16 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="animate-fade-up">
            <h1 className="font-display text-[2.75rem] font-bold leading-[1.03] tracking-tightest text-foreground sm:text-6xl lg:text-[4.25rem]">
              Built in minutes.
              <br />
              Made to last.
            </h1>

            <p className="mt-6 max-w-md text-balance text-base leading-relaxed text-foreground/80 sm:text-lg">
              DETROIT-AI turns one prompt into a production-grade landing
              page — planned, designed, built, and reviewed before it ever
              reaches you.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-4">
              <Button asChild size="lg" className="group">
                <a href="#about">
                  Get early access
                  <ArrowUpRight
                    size={18}
                    className="transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                  />
                </a>
              </Button>
              <Button asChild variant="outline" size="lg">
                <a href="#pipeline">See how it builds</a>
              </Button>
            </div>
          </div>

          <div
            id="pipeline"
            className="scroll-mt-24 animate-fade-up [animation-delay:150ms]"
          >
            <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs uppercase tracking-[0.18em] text-foreground/70">
              {STAGES.map((stage, i) => (
                <span key={stage} className="flex items-center gap-2">
                  {stage}
                  {i < STAGES.length - 1 && (
                    <span className="text-ember/70">→</span>
                  )}
                </span>
              ))}
            </div>
            <PipelineDemo />
          </div>
        </div>
      </section>
    </div>
  );
}
