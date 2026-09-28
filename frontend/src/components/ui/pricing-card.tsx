import * as React from "react";

import { cn } from "@/lib/utils";

function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "relative flex w-full flex-col rounded-2xl border border-white/15 bg-white/[0.05] p-1.5 shadow-[0_25px_60px_-25px_rgba(0,0,0,0.85)] backdrop-blur-xl",
        className,
      )}
      {...props}
    />
  );
}

function Header({
  className,
  children,
  glassEffect = true,
  ...props
}: React.ComponentProps<"div"> & { glassEffect?: boolean }) {
  return (
    <div
      className={cn(
        "relative mb-4 rounded-xl border border-white/10 bg-black/20 p-5",
        className,
      )}
      {...props}
    >
      {glassEffect && (
        <div
          aria-hidden="true"
          className="absolute inset-x-0 top-0 h-24 rounded-[inherit]"
          style={{
            background:
              "linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.02) 60%, rgba(0,0,0,0) 100%)",
          }}
        />
      )}
      <div className="relative">{children}</div>
    </div>
  );
}

function Plan({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("mb-6 flex items-center justify-between", className)}
      {...props}
    />
  );
}

function PlanName({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 font-mono text-xs uppercase tracking-[0.14em] text-muted-foreground [&_svg:not([class*='size-'])]:size-4 [&_svg]:text-ember",
        className,
      )}
      {...props}
    />
  );
}

function Badge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "rounded-full border border-ember/40 bg-ember/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ember",
        className,
      )}
      {...props}
    />
  );
}

function Description({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      className={cn("text-xs leading-relaxed text-muted-foreground", className)}
      {...props}
    />
  );
}

function Price({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("mb-1 flex items-end gap-1", className)} {...props} />
  );
}

function MainPrice({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "font-display text-4xl font-bold tracking-tight text-foreground",
        className,
      )}
      {...props}
    />
  );
}

function Period({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      className={cn("pb-1.5 text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

function OriginalPrice({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "ml-auto pb-1.5 text-sm text-muted-foreground/60 line-through",
        className,
      )}
      {...props}
    />
  );
}

function Body({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("space-y-5 p-3", className)} {...props} />;
}

function List({ className, ...props }: React.ComponentProps<"ul">) {
  return <ul className={cn("space-y-3", className)} {...props} />;
}

function ListItem({ className, ...props }: React.ComponentProps<"li">) {
  return (
    <li
      className={cn(
        "flex items-start gap-2.5 text-[13px] leading-relaxed text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export {
  Card,
  Header,
  Plan,
  PlanName,
  Badge,
  Description,
  Price,
  MainPrice,
  Period,
  OriginalPrice,
  Body,
  List,
  ListItem,
};
