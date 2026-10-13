"use client";

import { AnimatePresence, motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface RadarPulseProps {
  /** Current phase label streamed over SSE, e.g. "Scanning https://domain.com/contact-us" */
  phase: string;
  active?: boolean;
  className?: string;
}

export function RadarPulse({ phase, active = true, className }: RadarPulseProps) {
  return (
    <div className={cn("flex items-center gap-3 rounded-lg border ll-border bg-white/[0.03] px-4 py-3", className)}>
      <span className="relative flex size-2.5 shrink-0">
        {active && (
          <span className="absolute inset-0 rounded-full bg-ll-cyan animate-ping-slow" aria-hidden />
        )}
        <span className={cn("relative size-2.5 rounded-full", active ? "bg-ll-cyan" : "bg-slate-600")} />
      </span>
      <AnimatePresence mode="wait">
        <motion.span
          key={phase}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2 }}
          className="truncate font-mono text-xs text-slate-300"
        >
          {phase}
        </motion.span>
      </AnimatePresence>
    </div>
  );
}
