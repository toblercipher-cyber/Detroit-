import { useEffect, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type SelectorOption = {
  image: string;
  stage: string;
  label: string;
  description?: string;
  icon: ReactNode;
};

export function InteractiveSelector({
  options,
  className,
}: {
  options: SelectorOption[];
  className?: string;
}) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [revealed, setRevealed] = useState<number[]>([]);

  useEffect(() => {
    const timers = options.map((_, i) =>
      setTimeout(() => setRevealed((prev) => [...prev, i]), 150 * i),
    );
    return () => timers.forEach(clearTimeout);
  }, [options]);

  return (
    <div
      className={cn(
        "flex w-full items-stretch overflow-hidden rounded-2xl",
        className,
      )}
      style={{ perspective: "1000px" }}
    >
      {options.map((option, index) => {
        const isActive = activeIndex === index;
        const isRevealed = revealed.includes(index);
        return (
          <div
            key={option.label}
            onClick={() => setActiveIndex(index)}
            className={cn(
              "group relative flex cursor-pointer flex-col justify-end overflow-hidden border transition-[flex-grow,box-shadow] duration-700 ease-in-out",
              isActive ? "border-white/70" : "border-white/10",
            )}
            style={{
              backgroundImage: `url(${option.image})`,
              backgroundSize: "cover",
              backgroundPosition: "center",
              backgroundRepeat: "no-repeat",
              backgroundColor: "#18181b",
              boxShadow: isActive
                ? "0 20px 60px rgba(0,0,0,0.5)"
                : "0 10px 30px rgba(0,0,0,0.3)",
              flex: isActive ? "7 1 0%" : "1 1 0%",
              zIndex: isActive ? 10 : 1,
              minWidth: "56px",
              opacity: isRevealed ? 1 : 0,
              transform: isRevealed ? "translateX(0)" : "translateX(-40px)",
              willChange: "flex-grow, box-shadow, background-size",
            }}
          >
            <div
              className="pointer-events-none absolute inset-x-0 bottom-0 transition-all duration-700 ease-in-out"
              style={{
                height: isActive ? "55%" : "40%",
                boxShadow: isActive
                  ? "inset 0 -140px 100px -60px rgba(0,0,0,0.92)"
                  : "inset 0 -70px 60px -50px rgba(0,0,0,0.75)",
              }}
            />

            <div className="relative z-10 flex w-full items-center gap-3 px-4 pb-5">
              <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full border-2 border-white/20 bg-black/60 backdrop-blur-sm transition-colors duration-300 group-hover:border-white/40">
                {option.icon}
              </div>
              <div
                className="min-w-0 overflow-hidden whitespace-nowrap transition-all duration-700 ease-in-out"
                style={{
                  opacity: isActive ? 1 : 0,
                  transform: isActive ? "translateX(0)" : "translateX(20px)",
                }}
              >
                <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ember">
                  {option.stage}
                </p>
                <p className="text-sm font-semibold text-white sm:text-base">
                  {option.label}
                </p>
                {option.description ? (
                  <p className="text-xs text-white/70">{option.description}</p>
                ) : null}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
