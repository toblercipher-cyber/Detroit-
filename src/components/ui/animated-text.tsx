import { useInView } from "@/hooks/use-in-view";

interface AnimatedTextProps {
  text: string;
  as?: "h1" | "h2" | "h3" | "p" | "span";
  className?: string;
  delay?: number;
  stagger?: number;
}

export function AnimatedText({
  text,
  as: Tag = "span",
  className,
  delay = 0,
  stagger = 0.045,
}: AnimatedTextProps) {
  const words = text.split(" ");
  const { ref, inView } = useInView<HTMLElement>();

  return (
    <Tag ref={ref as never} className={className}>
      {words.map((word, i) => (
        <span
          key={`${word}-${i}`}
          className="inline-block overflow-hidden pb-[0.15em]"
        >
          <span
            className="inline-block transition-[transform,opacity]"
            style={{
              transitionDuration: "700ms",
              transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
              transitionDelay: `${(delay + i * stagger) * 1000}ms`,
              transform: inView ? "translateY(0%)" : "translateY(110%)",
              opacity: inView ? 1 : 0,
            }}
          >
            {word}
            {i < words.length - 1 ? " " : ""}
          </span>
        </span>
      ))}
    </Tag>
  );
}
