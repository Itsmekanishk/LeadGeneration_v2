"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";

/**
 * Makes every framer-motion animation in the app honor the OS-level
 * "reduce motion" preference (transform/layout animations become instant).
 * CSS animations are separately gated by the globals.css media query.
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
