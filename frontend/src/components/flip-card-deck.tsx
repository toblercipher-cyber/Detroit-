import { useState } from "react";

import flipPlanning from "@/assets/flip-planning.jpg";
import flipBuilding from "@/assets/flip-building.jpg";
import flipSecurity from "@/assets/flip-security.jpg";
import { AnimatedFlipCard } from "@/components/ui/animated-card";
import { cn } from "@/lib/utils";

export type DeckCard = {
  image: string;
  stage: string;
  label: string;
};

const DEFAULT_CARDS: DeckCard[] = [
  { image: flipPlanning, stage: "01", label: "Planning" },
  { image: flipBuilding, stage: "02", label: "Building" },
  { image: flipSecurity, stage: "03", label: "Security checks" },
];

const LAYOUT = [
  { rotate: -8, x: "-70%", y: "8%", zIndex: 0 },
  { rotate: 0, x: "0%", y: "-6%", zIndex: 20 },
  { rotate: 8, x: "70%", y: "8%", zIndex: 10 },
];

export function FlipCardDeck({
  cards = DEFAULT_CARDS,
  containerClassName = "h-[250px] max-w-[360px] sm:h-[280px]",
  cardClassName = "h-[220px] w-[156px]",
}: {
  cards?: DeckCard[];
  containerClassName?: string;
  cardClassName?: string;
}) {
  const [order, setOrder] = useState(cards.map((_, i) => i));

  const bringToFront = (position: number) => {
    if (position === 1) return;
    setOrder((prev) => {
      const next = [...prev];
      const [picked] = next.splice(position, 1);
      next.splice(1, 0, picked);
      return next;
    });
  };

  return (
    <div
      className={cn("relative mx-auto w-full", containerClassName)}
      style={{ perspective: "1000px" }}
    >
      {order.map((cardIndex, position) => {
        const layout = LAYOUT[position];
        const card = cards[cardIndex];
        return (
          <AnimatedFlipCard
            key={card.label}
            imageUrl={card.image}
            stage={card.stage}
            label={card.label}
            onClick={() => bringToFront(position)}
            className={cn("left-0 right-0 top-0 mx-auto", cardClassName)}
            style={{
              rotate: layout.rotate,
              x: layout.x,
              y: layout.y,
              zIndex: layout.zIndex,
            }}
          />
        );
      })}
    </div>
  );
}
