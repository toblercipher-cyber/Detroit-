import * as React from "react";

import { cn } from "@/lib/utils";
import { useTilt } from "@/hooks/use-tilt";

export interface TiltCardProps extends React.HTMLAttributes<HTMLDivElement> {
  imageUrl: string;
  imageAlt?: string;
}

export const TiltCard = React.forwardRef<HTMLDivElement, TiltCardProps>(
  ({ className, imageUrl, imageAlt = "", children, ...props }, forwardedRef) => {
    const { ref, style, onMouseMove, onMouseLeave } = useTilt<HTMLDivElement>();

    return (
      <div
        ref={(node) => {
          ref.current = node;
          if (typeof forwardedRef === "function") forwardedRef(node);
          else if (forwardedRef) forwardedRef.current = node;
        }}
        onMouseMove={onMouseMove}
        onMouseLeave={onMouseLeave}
        style={{ ...style, transformStyle: "preserve-3d" }}
        className={cn(
          "group relative isolate flex overflow-hidden rounded-3xl border border-white/15 bg-card shadow-[0_25px_50px_-25px_rgba(0,0,0,0.85)]",
          className,
        )}
        {...props}
      >
        <img
          src={imageUrl}
          alt={imageAlt}
          loading="lazy"
          style={{ transform: "translateZ(-20px) scale(1.15)" }}
          className="absolute inset-0 h-full w-full object-cover transition-transform duration-500 ease-out group-hover:scale-[1.22]"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-black/10" />
        <div
          style={{ transform: "translateZ(40px)" }}
          className="relative z-10 flex w-full flex-col justify-end"
        >
          {children}
        </div>
      </div>
    );
  },
);
TiltCard.displayName = "TiltCard";
