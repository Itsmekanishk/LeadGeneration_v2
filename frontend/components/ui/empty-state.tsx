import * as React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "./button";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-xl border border-dashed ll-border py-16 text-center", className)}>
      {icon && <div className="text-slate-600">{icon}</div>}
      <div>
        <p className="text-sm font-medium text-slate-200">{title}</p>
        {description && <p className="mx-auto mt-1 max-w-sm text-xs text-slate-500">{description}</p>}
      </div>
      {action && (
        <Button size="sm" variant="secondary" onClick={action.onClick} className="mt-1">
          {action.label}
        </Button>
      )}
    </div>
  );
}

interface ErrorBannerProps {
  title: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorBanner({ title, description, onRetry, className }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-lg border border-ll-confidence-low/30 bg-ll-confidence-low-soft px-4 py-3",
        className
      )}
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-ll-confidence-low" aria-hidden />
      <div className="flex-1">
        <p className="text-sm font-medium text-slate-100">{title}</p>
        {description && <p className="mt-0.5 text-xs text-slate-400">{description}</p>}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-slate-200 transition-colors hover:bg-white/[0.08]"
        >
          <RefreshCw className="size-3" />
          Retry
        </button>
      )}
    </div>
  );
}
