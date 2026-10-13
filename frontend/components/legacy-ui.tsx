"use client";

import { clsx } from "clsx";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

type ButtonVariant = "primary" | "outline" | "ghost";

export function Button({
  variant = "primary",
  className,
  loading,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  loading?: boolean;
}) {
  const base =
    variant === "primary"
      ? "btn-primary"
      : variant === "outline"
        ? "btn-outline"
        : "btn-ghost";
  return (
    <button className={clsx(base, className)} disabled={loading || props.disabled} {...props}>
      {loading && (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={clsx("input", className)} {...props} />;
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("card p-6", className)}>{children}</div>;
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
  children: ReactNode;
}) {
  const tones: Record<string, string> = {
    neutral: "bg-muted text-muted-foreground",
    success: "bg-emerald-100 text-emerald-800",
    warning: "bg-amber-100 text-amber-800",
    danger: "bg-red-100 text-red-700",
    info: "bg-blue-100 text-blue-800",
  };
  return <span className={clsx("badge", tones[tone])}>{children}</span>;
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-all duration-500"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}

export function EmptyState({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-10 text-center">
      {icon}
      <div>
        <p className="font-semibold">{title}</p>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

/* ------------------------------- toasts ---------------------------------- */

type Toast = { id: number; title: string; kind: "success" | "error" | "info" };
const ToastCtx = createContext<{ push: (t: string, kind?: Toast["kind"]) => void }>({
  push: () => undefined,
});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((title: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, title, kind }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={clsx(
              "pointer-events-auto rounded-md border p-3 text-sm shadow-lg backdrop-blur",
              t.kind === "success" && "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
              t.kind === "error" && "border-red-500/40 bg-red-500/10 text-red-200",
              t.kind === "info" && "ll-border bg-card text-foreground",
            )}
          >
            {t.title}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/** Close-on-Escape helper for dialogs. */
export function useEscape(active: boolean, onClose: () => void) {
  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [active, onClose]);
}
