"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface TabsProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; icon?: React.ReactNode }[];
  /** Unique per Tabs instance on the page so layoutId animations don't collide. */
  layoutGroupId: string;
  className?: string;
}

export function Tabs<T extends string>({ value, onChange, options, layoutGroupId, className }: TabsProps<T>) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border ll-border bg-white/[0.03] p-1",
        className
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              "relative flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
              active ? "text-white" : "text-slate-400 hover:text-slate-200"
            )}
          >
            {active && (
              <motion.span
                layoutId={`${layoutGroupId}-active-pill`}
                className="absolute inset-0 rounded-md bg-ll-indigo"
                transition={{ type: "spring", stiffness: 400, damping: 32 }}
              />
            )}
            <span className="relative flex items-center gap-1.5">
              {opt.icon}
              {opt.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}
