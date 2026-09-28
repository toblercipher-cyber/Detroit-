import { useEffect, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";

const PROMPT = "Build a landing page for a coffee roastery in Detroit";

const STAGES = [
  { id: "plan", label: "Plan" },
  { id: "design", label: "Design" },
  { id: "build", label: "Build" },
  { id: "review", label: "Review" },
  { id: "secure", label: "Secure" },
] as const;

type StageStatus = "pending" | "active" | "done";

const TYPE_MS = 32;
const STAGE_MS = 620;
const HOLD_MS = 2200;

export function PipelineDemo() {
  const [typed, setTyped] = useState(0);
  const [stageIndex, setStageIndex] = useState(-1);
  const timeouts = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    function run() {
      timeouts.current.forEach(clearTimeout);
      timeouts.current = [];
      setTyped(0);
      setStageIndex(-1);

      for (let i = 1; i <= PROMPT.length; i++) {
        timeouts.current.push(
          setTimeout(() => setTyped(i), i * TYPE_MS),
        );
      }

      const stagesStart = PROMPT.length * TYPE_MS + 400;
      STAGES.forEach((_, i) => {
        timeouts.current.push(
          setTimeout(() => setStageIndex(i), stagesStart + i * STAGE_MS),
        );
      });

      const totalMs = stagesStart + STAGES.length * STAGE_MS + HOLD_MS;
      timeouts.current.push(setTimeout(run, totalMs));
    }

    run();
    return () => timeouts.current.forEach(clearTimeout);
  }, []);

  const statusFor = (i: number): StageStatus =>
    i < stageIndex ? "done" : i === stageIndex ? "active" : "pending";

  return (
    <div
      className="w-full max-w-md overflow-hidden rounded-md border border-border bg-card/80 shadow-[0_30px_60px_-20px_rgba(0,0,0,0.6)] backdrop-blur-sm"
      aria-label="Live demonstration of the DETROIT-AI build pipeline"
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/30" />
        <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/30" />
        <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground/30" />
        <span className="ml-2 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          detroit-ai · build
        </span>
      </div>

      <div className="space-y-5 p-5">
        <p className="min-h-[2.5rem] font-mono text-sm leading-relaxed text-foreground">
          <span className="text-ember">{"> "}</span>
          {PROMPT.slice(0, typed)}
          <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-[3px] animate-pulse bg-ember" />
        </p>

        <ul className="space-y-3">
          {STAGES.map((stage, i) => {
            const status = statusFor(i);
            return (
              <li key={stage.id} className="flex items-center gap-3">
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors duration-300 ${
                    status === "done"
                      ? "border-ember bg-ember text-primary-foreground"
                      : status === "active"
                        ? "border-ember text-ember"
                        : "border-border text-transparent"
                  }`}
                >
                  {status === "done" && <Check size={12} strokeWidth={3} />}
                  {status === "active" && (
                    <Loader2 size={12} className="animate-spin" />
                  )}
                </span>
                <span
                  className={`font-mono text-xs uppercase tracking-[0.18em] transition-colors duration-300 ${
                    status === "pending"
                      ? "text-muted-foreground/50"
                      : "text-foreground"
                  }`}
                >
                  {stage.label}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
