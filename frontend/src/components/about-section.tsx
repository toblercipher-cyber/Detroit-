import pricingBg from "@/assets/pricing-bg.jpg";
import { AnimatedText } from "@/components/ui/animated-text";
import { KineticMatrix } from "@/components/ui/kinetic-matrix";
import { FlipCardDeck } from "@/components/flip-card-deck";

export function AboutSection() {
  return (
    <section
      id="about"
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(6,7,8,0.85) 0%, rgba(6,7,8,0.7) 30%, rgba(6,7,8,0.9) 78%, rgba(6,7,8,0.98) 100%), url(${pricingBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 35%",
      }}
    >
      <div className="mx-auto grid w-full max-w-6xl grid-cols-1 items-center gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:gap-16">
        <div>
          <AnimatedText
            as="h2"
            text="A pipeline, not a black box."
            className="font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl"
          />

          <div className="mt-5 max-w-md">
            <AnimatedText
              as="p"
              text="DETROIT-AI is a prompt-to-page pipeline. Describe the page you need, and five coordinated models plan, design, build, review, and secure it — turning one sentence into a live, production-ready page."
              delay={0.15}
              stagger={0.014}
              className="text-lg leading-relaxed text-foreground/90 sm:text-xl"
            />
          </div>

          <div className="mt-6">
            <FlipCardDeck />
          </div>
        </div>

        <div className="h-[380px] w-full sm:h-[440px] lg:h-[480px]">
          <KineticMatrix title="DETROIT-AI" className="h-full w-full" />
        </div>
      </div>
    </section>
  );
}
