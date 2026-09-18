import { useRef, type MouseEvent } from "react";
import {
  motion,
  useMotionValue,
  useSpring,
  useTransform,
  type MotionStyle,
} from "framer-motion";

import { cn } from "@/lib/utils";

export interface FlipCardProps {
  imageUrl: string;
  stage: string;
  label: string;
  className?: string;
  style?: MotionStyle;
  onClick?: () => void;
}

export function AnimatedFlipCard({
  imageUrl,
  stage,
  label,
  className,
  style,
  onClick,
}: FlipCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);

  const onMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    if (!cardRef.current) return;
    const { left, top, width, height } = cardRef.current.getBoundingClientRect();
    mouseX.set(e.clientX - left - width / 2);
    mouseY.set(e.clientY - top - height / 2);
  };

  const onMouseLeave = () => {
    mouseX.set(0);
    mouseY.set(0);
  };

  const rotateX = useTransform(mouseY, [-90, 90], [10, -10]);
  const rotateY = useTransform(mouseX, [-90, 90], [-10, 10]);
  const springConfig = { stiffness: 300, damping: 20, mass: 0.5 };
  const springRotateX = useSpring(rotateX, springConfig);
  const springRotateY = useSpring(rotateY, springConfig);

  return (
    <motion.div
      layout
      onClick={onClick}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
      ref={cardRef}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      style={{
        rotateX: springRotateX,
        rotateY: springRotateY,
        transformStyle: "preserve-3d",
        ...style,
      }}
      className={cn(
        "group absolute transform-gpu cursor-pointer overflow-hidden rounded-xl bg-card shadow-[0_20px_45px_-20px_rgba(0,0,0,0.85)] transition-shadow duration-300 hover:shadow-[0_25px_55px_-15px_rgba(235,94,53,0.45)]",
        className,
      )}
      tabIndex={0}
      aria-label={label}
    >
      <img
        src={imageUrl}
        alt=""
        style={{ transform: "translateZ(10px)" }}
        className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.08]"
      />
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/35 to-black/5" />
      <div
        style={{ transform: "translateZ(30px)" }}
        className="absolute inset-x-0 bottom-0 p-4"
      >
        <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-ember">
          {stage}
        </p>
        <p className="mt-1 text-sm font-semibold leading-tight text-white">
          {label}
        </p>
      </div>
    </motion.div>
  );
}
