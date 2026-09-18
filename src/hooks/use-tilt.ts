import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";

export function useTilt<T extends HTMLElement>(maxDeg = 14) {
  const ref = useRef<T | null>(null);
  const [scrollRotateX, setScrollRotateX] = useState(0);
  const [hover, setHover] = useState({ rotateY: 0, active: false });

  // Scroll-linked tilt: the card leans back as it enters from below the
  // fold and leans forward as it exits past the top, settling flat near
  // the middle of the viewport. Works with no mouse at all (touch, trackpad).
  useEffect(() => {
    let ticking = false;
    const measure = () => {
      ticking = false;
      const node = ref.current;
      if (!node) return;
      const rect = node.getBoundingClientRect();
      const viewportCenter = window.innerHeight / 2;
      const cardCenter = rect.top + rect.height / 2;
      const progress = Math.max(
        -1,
        Math.min(1, (cardCenter - viewportCenter) / viewportCenter),
      );
      setScrollRotateX(progress * -maxDeg);
    };
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(measure);
    };

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [maxDeg]);

  const onMouseMove = (e: MouseEvent<T>) => {
    if (!ref.current) return;
    const { left, width } = ref.current.getBoundingClientRect();
    const x = e.clientX - left;
    const rotateY = ((x - width / 2) / (width / 2)) * maxDeg;
    setHover({ rotateY, active: true });
  };

  const onMouseLeave = () => {
    setHover({ rotateY: 0, active: false });
  };

  const scale = hover.active ? 1.05 : 1;
  const style: CSSProperties = {
    transform: `perspective(1200px) rotateX(${scrollRotateX}deg) rotateY(${hover.rotateY}deg) scale3d(${scale}, ${scale}, ${scale})`,
    transition: hover.active
      ? "transform 0.15s ease-out"
      : "transform 0.5s cubic-bezier(0.16, 1, 0.3, 1)",
  };

  return { ref, style, onMouseMove, onMouseLeave };
}
