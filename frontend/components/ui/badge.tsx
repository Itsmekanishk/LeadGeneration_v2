import * as React from "react";
import { Circle, CircleCheck, CircleDot, CircleX, Clock, Globe, MapPin, Sparkles, TestTube2 } from "lucide-react";
import { cn } from "@/lib/utils";

/* ---------- Generic badge primitive ---------- */

type BadgeTone = "neutral" | "ll-indigo" | "ll-cyan" | "success" | "warning" | "danger";

const toneStyles: Record<BadgeTone, string> = {
  neutral: "bg-white/[0.06] text-slate-300 border-white/10",
  "ll-indigo": "bg-ll-indigo-soft text-ll-indigo border-ll-indigo/30",
  "ll-cyan": "bg-ll-cyan-soft text-ll-cyan border-ll-cyan/30",
  success: "bg-ll-confidence-high-soft text-ll-confidence-high border-ll-confidence-high/30",
  warning: "bg-ll-confidence-medium-soft text-ll-confidence-medium border-ll-confidence-medium/30",
  danger: "bg-ll-confidence-low-soft text-ll-confidence-low border-ll-confidence-low/30",
};

export function Badge({
  tone = "neutral",
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-tight",
        toneStyles[tone],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}

/* ---------- Confidence badge (email/phone verification quality) ---------- */

/** Matches Business.email_confidence / Contact.confidence from the backend
 *  (uppercase strings, "" = unknown). Unknown falls back to Low. */
export type Confidence = "HIGH" | "MEDIUM" | "LOW" | "";

const confidenceConfig: Record<string, { label: string; tone: BadgeTone; icon: React.ElementType }> = {
  HIGH: { label: "High", tone: "success", icon: CircleCheck },
  MEDIUM: { label: "Medium", tone: "warning", icon: CircleDot },
  LOW: { label: "Low", tone: "danger", icon: CircleX },
  "": { label: "Unknown", tone: "neutral", icon: Circle },
};

export function ConfidenceBadge({ level }: { level: Confidence | string }) {
  const { label, tone, icon: Icon } = confidenceConfig[level] ?? confidenceConfig.LOW;
  return (
    <Badge tone={tone}>
      <Icon className="size-3" aria-hidden />
      {label}
    </Badge>
  );
}

/* ---------- Job status badge (search history) ---------- */

/** Mirrors SearchJob.status in the backend (models.JobStatus + worker stages).
 *  Extra lowercase values are tolerated and rendered neutrally. */
export type JobStatus =
  | "completed"
  | "partial"
  | "failed"
  | "cancelled"
  | "in_progress"
  | "queued"
  | "discovering"
  | "enriching"
  | "crawling"
  | "extracting"
  | "deduplicating"
  | "generating_report";

const statusConfig: Record<string, { label: string; tone: BadgeTone; icon: React.ElementType; spin?: boolean }> = {
  completed: { label: "Completed", tone: "success", icon: CircleCheck },
  partial: { label: "Partial data", tone: "warning", icon: CircleDot },
  failed: { label: "Failed", tone: "danger", icon: CircleX },
  cancelled: { label: "Cancelled", tone: "neutral", icon: CircleX },
  in_progress: { label: "In progress", tone: "ll-cyan", icon: Clock, spin: true },
  queued: { label: "Queued", tone: "ll-cyan", icon: Clock, spin: true },
  discovering: { label: "Discovering", tone: "ll-cyan", icon: Clock, spin: true },
  enriching: { label: "Enriching", tone: "ll-cyan", icon: Clock, spin: true },
  crawling: { label: "Crawling", tone: "ll-cyan", icon: Clock, spin: true },
  extracting: { label: "Extracting", tone: "ll-cyan", icon: Clock, spin: true },
  deduplicating: { label: "Deduplicating", tone: "ll-cyan", icon: Clock, spin: true },
  generating_report: { label: "Generating report", tone: "ll-cyan", icon: Clock, spin: true },
};

export function StatusBadge({ status }: { status: JobStatus | string }) {
  const { label, tone, icon: Icon, spin } = statusConfig[status] ?? {
    label: status,
    tone: "neutral" as BadgeTone,
    icon: Circle,
  };
  return (
    <Badge tone={tone}>
      <Icon className={cn("size-3", spin && "animate-spin")} aria-hidden />
      {label}
    </Badge>
  );
}

/* ---------- Data provider badge (search source) ---------- */

export type Provider = "google" | "google_places" | "serper" | "nominatim" | "geoapify" | "mock" | "auto";

const providerConfig: Record<string, { label: string; icon: React.ElementType }> = {
  geoapify: { label: "Geoapify", icon: MapPin },
  google: { label: "Google Places", icon: Sparkles },
  google_places: { label: "Google Places", icon: Sparkles },
  serper: { label: "Serper.dev", icon: Circle },
  nominatim: { label: "OpenStreetMap", icon: Globe },
  mock: { label: "Mock mode", icon: TestTube2 },
  auto: { label: "Auto (fallback chain)", icon: Sparkles },
};

export function ProviderBadge({ provider }: { provider: Provider | string }) {
  const { label, icon: Icon } = providerConfig[provider] ?? { label: provider, icon: Circle };
  return (
    <Badge tone="neutral">
      <Icon className="size-3" aria-hidden />
      {label}
    </Badge>
  );
}

/* ---------- Dedupe tag ---------- */

export function DedupeBadge({ unique }: { unique: boolean }) {
  return (
    <Badge tone={unique ? "ll-indigo" : "neutral"} className="font-mono">
      {unique ? "Unique" : "Duplicate"}
    </Badge>
  );
}
