/**
 * Demo section of the Landing Page.
 *
 * This is where the pipeline gets shown rather than described. The slot used to
 * hold a dashed "demo video — coming soon" placeholder; it now holds
 * `AgenticFactory3D`, an interactive three.js machine whose five stations map
 * onto DETROIT's own stages (prompt in, plan, build, publish, quality gate).
 *
 * Two deliberate decisions:
 *
 * 1. The scene is code-split and mounted only once it scrolls into view.
 *    `three` is a large dependency and this is the approved Landing Page — the
 *    first paint must not pay for a section that sits below the hero. Until it
 *    is in view the slot renders a still frame of the same size, so nothing
 *    reflows when the real scene arrives.
 *
 * 2. The scene keeps its own control panels (modes, cameras, play/pause). The
 *    component's `embed` prop hides them, but it also pushes the machine 21% to
 *    the right to clear a host headline — wrong for a centred box — and
 *    removes the one thing that makes this better than a video: the visitor can
 *    drive it.
 */

import { lazy, Suspense, useEffect, useRef, useState } from "react";

import heroBg from "@/assets/hero-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";

const AgenticFactory3D = lazy(() => import("@/components/ui/agentic-factory-3d"));

/**
 * Sizing lives here so the placeholder and the real scene cannot disagree.
 *
 * The machine is a wide object, so in a narrow frame its size is capped by the
 * width, not the height. A tall box on a phone therefore just adds empty sky
 * above it — hence the smaller floor below `sm`, which is still enough room for
 * the scene's own control panels.
 */
const STAGE =
  "relative mt-12 aspect-video min-h-[24rem] w-full overflow-hidden rounded-3xl border border-border/60 bg-background/60 sm:min-h-[30rem]";

export function DemoVideoSection() {
  const stageRef = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || inView) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(stage);
    return () => observer.disconnect();
  }, [inView]);

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
            text="The whole pipeline as one machine. Five stations carry a single request from prompt to shipped page — turn it, cut it open, or follow one order all the way through."
            delay={0.2}
            stagger={0.014}
            className="mt-5 max-w-md text-base leading-relaxed text-foreground/80"
          />
        </div>

        <div ref={stageRef} className={STAGE}>
          {inView ? (
            <Suspense fallback={<StageLoading />}>
              <AgenticFactory3D height="100%" />
            </Suspense>
          ) : (
            <StageLoading />
          )}
        </div>

        <p className="mt-4 font-mono text-xs uppercase tracking-[0.3em] text-foreground/50">
          Drag to turn · scroll to zoom · pick a station
        </p>
      </div>
    </section>
  );
}

/**
 * Holds the stage at its final size while the scene chunk loads, so the page
 * does not jump. Same geometry, none of the cost.
 */
function StageLoading() {
  return (
    <div className="absolute inset-0 grid place-items-center">
      <p className="font-mono text-xs uppercase tracking-[0.3em] text-foreground/40">
        Assembling the machine
      </p>
    </div>
  );
}
