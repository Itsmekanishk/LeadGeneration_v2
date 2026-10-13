"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Clock, Download, RotateCcw, Trash2 } from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import type { Job } from "@/types";
import {
  Button, Card, EmptyState, ErrorBanner, Skeleton, StatusBadge,
} from "@/components/ui";
import { useToast } from "@/components/legacy-ui";

export default function HistoryPage() {
  const router = useRouter();
  const { push } = useToast();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setJobs(await api.get<Job[]>("/search"));
    } catch (err) {
      setJobs([]);
      setError(err instanceof Error ? err.message : "Failed to load history");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function doDownload(job: Job) {
    setBusyId(job.id);
    try {
      const res = await api.post<{ filename: string }>(`/search/${job.id}/export/csv`);
      await downloadFile(job.id, "csv", res.filename);
      push("CSV downloaded", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Download failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function doDelete(job: Job) {
    setBusyId(job.id);
    try {
      await api.del(`/search/${job.id}`);
      setJobs((prev) => prev?.filter((j) => j.id !== job.id) ?? null);
      push("Search deleted", "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setBusyId(null);
      setConfirmDeleteId(null);
    }
  }

  if (!jobs && !error) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorBanner title="Couldn't load your history" description={error} onRetry={() => void load()} />;
  }

  if (jobs && jobs.length === 0) {
    return (
      <EmptyState
        title="No searches yet"
        description="Your search history will appear here."
        icon={<Clock className="h-8 w-8 text-muted-foreground" />}
        action={{ label: "Run your first search", onClick: () => router.push("/dashboard") }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Search history</h1>
        <p className="text-sm text-muted-foreground">Re-run, download or delete past searches.</p>
      </div>
      <div className="space-y-2">
        {jobs?.map((job) => (
          <Card key={job.id} interactive className="flex flex-wrap items-center gap-4">
            <div
              className="min-w-0 flex-1 cursor-pointer"
              onClick={() => router.push(`/results/${job.id}`)}
            >
              <p className="truncate font-medium">{job.query_raw}</p>
              <p className="text-xs text-muted-foreground">
                {job.parsed_category || "—"} · {job.parsed_location || "—"} ·{" "}
                {new Date(job.created_at ?? "").toLocaleString()}
              </p>
            </div>
            <div className="text-right font-mono text-xs tabular-nums text-muted-foreground">
              {job.progress_stats.emails_found} emails · {job.progress_stats.phones_found} phones
              <br />
              {job.progress_stats.total} businesses
            </div>
            <StatusBadge status={job.status} />
            <div className="flex gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                title="View results"
                onClick={() => router.push(`/results/${job.id}`)}
              >
                View
              </Button>
              <Button
                variant="ghost"
                size="sm"
                title="Download CSV"
                loading={busyId === job.id}
                disabled={job.status !== "completed"}
                onClick={() => void doDownload(job)}
              >
                <Download className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                title="Re-run this search"
                onClick={() => router.push(`/dashboard?q=${encodeURIComponent(job.query_raw)}`)}
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </Button>
              {confirmDeleteId === job.id ? (
                <span className="flex items-center gap-1">
                  <Button variant="danger" size="sm" loading={busyId === job.id} onClick={() => void doDelete(job)}>
                    Confirm
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setConfirmDeleteId(null)}>
                    Cancel
                  </Button>
                </span>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  title="Delete search"
                  onClick={() => setConfirmDeleteId(job.id)}
                >
                  <Trash2 className="h-3.5 w-3.5 text-ll-confidence-low" />
                </Button>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
