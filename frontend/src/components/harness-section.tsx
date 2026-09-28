import { Cpu, Wallet, Workflow } from "lucide-react";

import harnessBg from "@/assets/harness-bg.jpg";
import harnessRuntime from "@/assets/harness-card-runtime.jpg";
import harnessOrchestration from "@/assets/harness-card-orchestration.jpg";
import harnessUsage from "@/assets/harness-card-usage.jpg";
import { AnimatedText } from "@/components/ui/animated-text";
import { KineticGrid } from "@/components/ui/kinetic-grid";
import {
  InteractiveSelector,
  type SelectorOption,
} from "@/components/ui/interactive-selector";

const HARNESS_CARDS: SelectorOption[] = [
  {
    image: harnessRuntime,
    stage: "01",
    label: "Runtime",
    icon: <Cpu size={18} className="text-white" />,
  },
  {
    image: harnessOrchestration,
    stage: "02",
    label: "Orchestration",
    icon: <Workflow size={18} className="text-white" />,
  },
  {
    image: harnessUsage,
    stage: "03",
    label: "Usage & cost",
    icon: <Wallet size={18} className="text-white" />,
  },
];

export function HarnessSection() {
  return (
    <section
      id="harness"
      className="relative overflow-hidden px-6 py-20 sm:px-10 lg:px-14 lg:py-28"
      style={{
        backgroundImage: `linear-gradient(180deg, rgba(4,4,6,0.9) 0%, rgba(4,4,6,0.72) 30%, rgba(4,4,6,0.9) 78%, rgba(4,4,6,0.98) 100%), url(${harnessBg})`,
        backgroundSize: "cover",
        backgroundPosition: "center 30%",
      }}
    >
      <div className="mx-auto max-w-6xl">
        <div className="max-w-2xl">
          <AnimatedText
            as="h2"
            text="One harness, five minds in sync."
            className="font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl"
          />
          <AnimatedText
            as="p"
            text="Every model runs inside the same agent harness — shared context, shared guardrails, one consistent voice — so handing off between Claude, Gemini, Groq, Qwen, and DeepSeek never breaks the thread."
            delay={0.2}
            stagger={0.014}
            className="mt-5 max-w-md text-base leading-relaxed text-foreground/80"
          />
        </div>

        <div className="mt-12 overflow-hidden rounded-3xl border border-white/10">
          <KineticGrid className="flex min-h-[300px] flex-col items-center justify-center gap-6 px-8 py-12 sm:min-h-[460px] sm:px-16 sm:py-16">
            <InteractiveSelector
              options={HARNESS_CARDS}
              className="h-[220px] max-w-full sm:h-[360px]"
            />
            <p className="font-mono text-xs uppercase tracking-[0.3em] text-foreground/60">
              Agent-Harness
            </p>
          </KineticGrid>
        </div>
      </div>
    </section>
  );
}
