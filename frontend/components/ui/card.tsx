"use client";

import * as React from "react";
import { motion, type HTMLMotionProps } from "framer-motion";
import { cn } from "@/lib/utils";

type CardVariant = "flat" | "elevated" | "glass";

interface CardProps extends Omit<HTMLMotionProps<"div">, "children"> {
  variant?: CardVariant;
  /** Lifts and brightens the border on hover — use for clickable/interactive cards only. */
  interactive?: boolean;
  children?: React.ReactNode;
}

const variantStyles: Record<CardVariant, string> = {
  flat: "bg-ll-surface border ll-border",
  elevated: "bg-ll-surface-elevated border ll-border shadow-card",
  glass: "bg-ll-surface-elevated/60 backdrop-blur-xl border border-white/10",
};

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = "elevated", interactive, ...props }, ref) => {
    return (
      <motion.div
        ref={ref}
        className={cn("rounded-xl p-4", variantStyles[variant], className)}
        whileHover={interactive ? { y: -2, borderColor: "rgba(255,255,255,0.18)" } : undefined}
        transition={{ type: "spring", stiffness: 300, damping: 24 }}
        {...props}
      />
    );
  }
);
Card.displayName = "Card";

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mb-3 flex items-start justify-between gap-3", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-sm font-medium text-slate-100", className)} {...props} />;
}

export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-xs text-slate-400", className)} {...props} />;
}
