"use client";

/**
 * The live "crawling" view, shared by the search panel and the results page.
 *
 * Single source of truth for the pipeline stage order and labels, so both
 * surfaces describe the same job identically instead of drifting apart.
 */
import {
  Building2, CheckCircle2, Globe, Mail, Phone, Settings2,
} from "lucide-react";
import type { Job } from "@/types";
import { AnimatedCounter } from "@/components/ui";
import { Progress } from "@/components/legacy-ui";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

/** Jobs in one of these states will not change again. */
export function isTerminal(status?: string): boolean {
  return !!status && TERMINAL.has(status);
}

// Pipeline stages exactly as emitted by the worker (backend-verified order).
export const STAGES = [
  "queued", "discovering", "enriching", "crawling", "extracting", "deduplicating",
];

export const STAGE_LABEL: Record<string, string> = {
  queued: "Job queued",
  discovering: "Business discovery",
  enriching: "Record enrichment",
  crawling: "Website crawling",
  extracting: "Contact extraction",
  deduplicating: "Deduplication",
};

export const STATUS_LABEL: Record<string, string> = {
  queued: "Queued…",
  discovering: "Searching businesses…",
  enriching: "Normalizing records…",
  crawling: "Scanning websites…",
  extracting: "Extracting contacts…",
  deduplicating: "Removing duplicates…",
  generating_report: "Generating report…",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Progress bar, stage checklist and live counters for a running job. */
export function CrawlProgress({ job }: { job: Job }) {
  const runningStage = STAGES.indexOf(job.status);
  const stats = job.progress_stats;
  return (
    <>
      <Progress value={job.progress} />
      <p className="mt-2 text-sm text-muted-foreground">
        {job.stage_detail || "Working…"}
      </p>
      <ol className="mt-3 space-y-1.5 text-sm" aria-label="Pipeline stages">
        {STAGES.map((stage, i) => {
          const done =
            job.status === "completed" || (runningStage > i && runningStage !== -1);
          const active = job.status === stage;
          const failedStage = job.status === "failed" && runningStage === i;
          return (
            <li key={stage} className="flex items-center gap-2">
              <span
                aria-hidden
                className={
                  done ? "text-ll-confidence-high"
                    : active ? "text-ll-indigo"
                      : failedStage ? "text-ll-confidence-low"
                        : "text-muted-foreground"
                }
              >
                {done ? "✓" : failedStage ? "✗" : active ? "●" : "○"}
              </span>
              <span className={done || active ? "font-medium" : "text-muted-foreground"}>
                {STAGE_LABEL[stage]}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Stat icon={<Building2 className="h-4 w-4" />} label="Found" value={stats?.total ?? 0} />
        <Stat icon={<Globe className="h-4 w-4" />} label="Websites" value={stats?.websites_found ?? 0} />
        <Stat icon={<Mail className="h-4 w-4" />} label="Emails" value={stats?.emails_found ?? 0} />
        <Stat icon={<Phone className="h-4 w-4" />} label="Phones" value={stats?.phones_found ?? 0} />
        <Stat icon={<CheckCircle2 className="h-4 w-4" />} label="Processed" value={stats?.completed ?? 0} />
        <Stat icon={<Settings2 className="h-4 w-4" />} label="Failed scans" value={stats?.failed ?? 0} />
      </div>
    </>
  );
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="rounded-lg border ll-border p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <AnimatedCounter className="mt-1 block text-xl font-bold" value={value} />
    </div>
  );
}
