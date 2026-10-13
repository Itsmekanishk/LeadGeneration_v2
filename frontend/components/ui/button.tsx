"use client";

import * as React from "react";
import { motion, type HTMLMotionProps } from "framer-motion";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends Omit<HTMLMotionProps<"button">, "ref" | "children"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /** Adds the animated gradient sweep used for primary calls to action, e.g. "Run search". */
  glow?: boolean;
  children?: React.ReactNode;
}

const sizeStyles: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
  lg: "h-12 px-6 text-base gap-2.5",
};

const variantStyles: Record<ButtonVariant, string> = {
  primary: "bg-ll-indigo text-white hover:bg-ll-indigo/90 disabled:bg-ll-indigo/40",
  secondary:
    "bg-white/[0.06] text-slate-100 border ll-border-strong hover:bg-white/[0.1] disabled:opacity-40",
  ghost: "bg-transparent text-slate-300 hover:bg-white/[0.06] disabled:opacity-40",
  danger: "bg-ll-confidence-low/90 text-white hover:bg-ll-confidence-low disabled:opacity-40",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", loading, glow, disabled, children, ...props }, ref) => {
    return (
      <motion.button
        ref={ref}
        whileTap={{ scale: 0.97 }}
        disabled={disabled || loading}
        className={cn(
          "relative inline-flex items-center justify-center overflow-hidden rounded-lg font-medium",
          "transition-colors disabled:cursor-not-allowed",
          sizeStyles[size],
          variantStyles[variant],
          glow && variant === "primary" && "shadow-glow",
          className
        )}
        {...props}
      >
        {glow && variant === "primary" && !disabled && (
          <motion.span
            aria-hidden
            className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/25 to-transparent"
            animate={{ translateX: ["-100%", "200%"] }}
            transition={{ duration: 2.2, repeat: Infinity, ease: "linear", repeatDelay: 0.6 }}
          />
        )}
        {loading ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : null}
        <span className="relative">{children}</span>
      </motion.button>
    );
  }
);
Button.displayName = "Button";
