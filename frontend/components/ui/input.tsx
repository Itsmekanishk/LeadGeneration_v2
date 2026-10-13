"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/* ---------- Text input ---------- */

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  icon?: React.ReactNode;
  mono?: boolean;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, icon, mono, ...props }, ref) => {
    return (
      <div className="relative">
        {icon && (
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">
            {icon}
          </span>
        )}
        <input
          ref={ref}
          className={cn(
            "h-10 w-full rounded-lg border ll-border bg-white/[0.03] px-3 text-sm text-slate-100",
            "placeholder:text-slate-500 outline-none transition-colors",
            "focus:border-ll-indigo/60 focus:bg-white/[0.05]",
            icon && "pl-9",
            mono && "font-mono tabular-nums",
            className
          )}
          {...props}
        />
      </div>
    );
  }
);
Input.displayName = "Input";

/* ---------- Slider (e.g. search radius 500m–50,000m) ---------- */

interface SliderProps {
  min: number;
  max: number;
  step?: number;
  value: number;
  onChange: (value: number) => void;
  formatValue?: (value: number) => string;
  label?: string;
}

export function Slider({ min, max, step = 1, value, onChange, formatValue, label }: SliderProps) {
  const percent = ((value - min) / (max - min)) * 100;
  return (
    <div className="w-full">
      {label && (
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="text-slate-400">{label}</span>
          <span className="font-mono tabular-nums text-slate-200">
            {formatValue ? formatValue(value) : value}
          </span>
        </div>
      )}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-ll-indigo
          [&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
          [&::-webkit-slider-thumb]:bg-ll-indigo [&::-webkit-slider-thumb]:shadow-glow [&::-webkit-slider-thumb]:cursor-pointer"
        style={{
          background: `linear-gradient(to right, #6366F1 ${percent}%, rgba(255,255,255,0.1) ${percent}%)`,
        }}
      />
    </div>
  );
}

/* ---------- Switch (e.g. respect robots.txt, disposable-email filter) ---------- */

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  description?: string;
  id?: string;
}

export function Switch({ checked, onChange, label, description, id }: SwitchProps) {
  const inputId = id ?? React.useId();
  return (
    <label htmlFor={inputId} className="flex cursor-pointer items-start justify-between gap-4 py-1">
      {(label || description) && (
        <span className="flex flex-col">
          {label && <span className="text-sm text-slate-200">{label}</span>}
          {description && <span className="text-xs text-slate-500">{description}</span>}
        </span>
      )}
      <span className="relative inline-flex h-6 w-11 shrink-0 items-center">
        <input
          id={inputId}
          type="checkbox"
          role="switch"
          aria-checked={checked}
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span
          className={cn(
            "absolute inset-0 rounded-full transition-colors",
            checked ? "bg-ll-indigo" : "bg-white/10"
          )}
        />
        <span
          className={cn(
            "absolute left-0.5 size-5 rounded-full bg-white transition-transform",
            checked && "translate-x-5"
          )}
        />
      </span>
    </label>
  );
}
