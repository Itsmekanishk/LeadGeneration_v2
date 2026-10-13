import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-md bg-white/[0.06]",
        "before:absolute before:inset-0 before:-translate-x-full before:animate-shimmer",
        "before:bg-gradient-to-r before:from-transparent before:via-white/10 before:to-transparent",
        className
      )}
    />
  );
}

/** Placeholder for a single lead row/card while the table is loading. */
export function LeadRowSkeleton() {
  return (
    <div className="flex items-center gap-4 border-b ll-border px-4 py-3">
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-4 w-28" />
      <Skeleton className="h-4 w-24" />
      <Skeleton className="ml-auto h-4 w-16" />
    </div>
  );
}
