import cardAgents from "@/assets/card-agents.jpg";
import cardBuild from "@/assets/card-build.jpg";
import cardProject from "@/assets/card-project.jpg";
import cardSecurity from "@/assets/card-security.jpg";
import phaseTwoBg from "@/assets/phase-two-bg.jpg";
import { TiltCard } from "@/components/ui/tilt-card";
import { useInView } from "@/hooks/use-in-view";

const MODELS = ["Claude", "Gemini", "Groq", "Qwen", "DeepSeek"];

type CardData = {
  image: string;
  eyebrow: string;
  title: string;
  description: string;
  tags?: string[];
};

const CARDS: CardData[] = [
  {
    image: cardAgents,
    eyebrow: "The agents",
    title: "Five models, working in shifts",
    description:
      "Claude, Gemini, Groq, Qwen, and DeepSeek each take a pass through the pipeline, not in isolation.",
    tags: MODELS,
  },
  {
    image: cardProject,
    eyebrow: "The project",
    title: "Built in Detroit, shipped everywhere",
    description:
      "One prompt becomes a production-grade page — no drag-and-drop builder, no blank canvas.",
  },
  {
    image: cardSecurity,
    eyebrow: "The review",
    title: "Reviewed and secured, every time",
    description:
      "Every page clears a security and review pass — dependency checks, input handling, a final sign-off — before it ships.",
  },
  {
    image: cardBuild,
    eyebrow: "The build",
    title: "Production infrastructure, not a prototype",
    description:
      "Typed components, optimized assets, a build that's ready for real traffic from the moment it ships.",
  },
];

function PhaseCard({
  card,
  delayMs,
  visible,
}: {
  card: CardData;
  delayMs: number;
  visible: boolean;
}) {
  return (
    <div
      className="transition-[opacity,transform] ease-out"
      style={{
        transitionDuration: "800ms",
        transitionDelay: `${delayMs}ms`,
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(28px)",
      }}
    >
      <TiltCard imageUrl={card.image} className="aspect-[3/4] w-full">
        <div className="rounded-2xl border border-white/15 bg-white/[0.07] p-4 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.12)] backdrop-blur-md">
          <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-ember">
            {card.eyebrow}
          </p>
          <h3 className="mt-1.5 font-display text-base font-bold leading-tight text-white">
            {card.title}
          </h3>
          <p className="mt-1.5 text-[11px] leading-relaxed text-white/70">
            {card.description}
          </p>
          {card.tags && (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {card.tags.map((tag) => (
                <li
                  key={tag}
                  className="rounded-full border border-white/20 bg-white/10 px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-white"
                >
                  {tag}
                </li>
              ))}
            </ul>
          )}
        </div>
      </TiltCard>
    </div>
  );
}

export function AgentsSection() {
  const { ref, inView } = useInView<HTMLDivElement>();

  return (
    <section
      id="agents"
      className="relative overflow-hidden px-6 py-16 sm:px-10 lg:px-14 lg:py-20"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(8,9,10,0.9) 0%, rgba(8,9,10,0.75) 25%, rgba(8,9,10,0.88) 75%, rgba(8,9,10,0.97) 100%), url(${phaseTwoBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 30%",
      }}
    >
      <div ref={ref} className="mx-auto max-w-6xl">
        <div
          className="max-w-2xl transition-all duration-700 ease-out"
          style={{
            opacity: inView ? 1 : 0,
            transform: inView ? "translateY(0)" : "translateY(20px)",
          }}
        >
          <h2 className="font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            Five models. One pipeline.
          </h2>
          <p className="mt-4 text-balance text-base leading-relaxed text-muted-foreground sm:text-lg">
            DETROIT-AI doesn't lean on a single model. Every page is planned,
            designed, built, reviewed, and secured by a coordinated pipeline
            of five frontier models — each one checking the last before
            anything ships.
          </p>
        </div>

        <div className="mx-auto mt-10 grid max-w-sm grid-cols-1 gap-8 sm:mx-0 sm:max-w-none sm:grid-cols-2 sm:items-stretch lg:grid-cols-4">
          {CARDS.map((card, i) => (
            <PhaseCard
              key={card.title}
              card={card}
              visible={inView}
              delayMs={i * 100}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
