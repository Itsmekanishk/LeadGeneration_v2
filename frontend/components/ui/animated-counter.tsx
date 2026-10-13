"use client";

import * as React from "react";
import { animate, motion, useMotionValue, useTransform } from "framer-motion";

interface AnimatedCounterProps {
  value: number;
  className?: string;
  /** e.g. "ms", "%" — appended after the number without a space. */
  suffix?: string;
}

/**
 * Counts smoothly from its previous value to `value` whenever it changes —
 * used for live SSE-driven stats so numbers never jump-cut or reflow the layout.
 */
export function AnimatedCounter({ value, className, suffix }: AnimatedCounterProps) {
  const motionValue = useMotionValue(value);
  const rounded = useTransform(motionValue, (v) => Math.round(v).toLocaleString());

  React.useEffect(() => {
    const controls = animate(motionValue, value, { duration: 0.6, ease: "easeOut" });
    return controls.stop;
  }, [value, motionValue]);

  return (
    <span className={className}>
      <motion.span className="tabular-nums">{rounded}</motion.span>
      {suffix}
    </span>
  );
}
