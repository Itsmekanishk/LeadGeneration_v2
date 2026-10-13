"use client";

import { useEffect, useRef, useState } from "react";
import type { Job } from "@/types";

interface JobEvent {
  job_id: string;
  status?: Job["status"];
  stage_detail?: string;
  progress?: number;
  total?: number;
  completed?: number;
  failed?: number;
  emails_found?: number;
  phones_found?: number;
  websites_found?: number;
}

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

/**
 * Subscribe to the SSE progress stream for a job.
 * Merges live events onto the initial job snapshot and falls back to
 * polling the status endpoint when SSE is unavailable (e.g. some proxies).
 */
export function useJobStream(jobId: string | null, initial?: Job, retryKey = 0) {
  const [job, setJob] = useState<Job | undefined>(initial);
  const [done, setDone] = useState(false);
  /** True when the live SSE fetch-stream failed (polling fallback may engage). */
  const [streamFailed, setStreamFailed] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    setDone(false);
    const defaultStats = {
      total: 0,
      completed: 0,
      failed: 0,
      emails_found: 0,
      phones_found: 0,
      websites_found: 0,
    };

    // Immediately fetch initial snapshot so UI renders instantly
    import("@/lib/api").then(({ api }) => {
      api.get<Job>(`/search/${jobId}`).then((snapshot) => {
        if (!cancelled && snapshot) {
          setJob((prev) => (prev ? { ...snapshot, ...prev } : snapshot));
          if (TERMINAL.has(snapshot.status)) setDone(true);
        }
      }).catch(() => {});
    });

    const apply = (e: JobEvent) => {
      if (cancelled || e.job_id !== jobId) return;
      setJob((prev) => {
        const prevStats = prev?.progress_stats ?? defaultStats;
        return {
          ...(prev ?? ({ id: jobId } as Job)),
          status: (e.status as Job["status"]) ?? prev?.status ?? "queued",
          stage_detail: e.stage_detail ?? prev?.stage_detail ?? "",
          progress: e.progress ?? prev?.progress ?? 0,
          progress_stats: {
            total: e.total ?? prevStats.total ?? 0,
            completed: e.completed ?? prevStats.completed ?? 0,
            failed: e.failed ?? prevStats.failed ?? 0,
            emails_found: e.emails_found ?? prevStats.emails_found ?? 0,
            phones_found: e.phones_found ?? prevStats.phones_found ?? 0,
            websites_found: e.websites_found ?? prevStats.websites_found ?? 0,
          },
        };
      });
      if (e.status && TERMINAL.has(e.status)) setDone(true);
    };

    const apiBase = process.env.NEXT_PUBLIC_API_URL
      ? `${process.env.NEXT_PUBLIC_API_URL}/api`
      : "/backend/api";

    // authenticated SSE via fetch-stream (EventSource cannot send headers)
    const controller = new AbortController();
    import("@/lib/api")
      .then(({ getAccessToken }) => {
        const token = getAccessToken();
        return fetch(`${apiBase}/search/${jobId}/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        });
      })
      .then(async (res) => {
        if (!res.ok || !res.body) throw new Error("stream unavailable");
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() ?? "";
          for (const chunk of parts) {
            const line = chunk
              .split("\n")
              .find((l) => l.startsWith("data: "));
            if (line) {
              try {
                apply(JSON.parse(line.slice(6)));
              } catch {
                /* ignore malformed chunk */
              }
            }
          }
        }
        setDone(true);
      })
      .catch(() => {
        // fallback: poll (interval tracked in a ref so cleanup always runs)
        if (cancelled) return;
        setStreamFailed(true);
        pollRef.current = setInterval(async () => {
          try {
            const { api } = await import("@/lib/api");
            const fresh = await api.get<Job>(`/search/${jobId}`);
            if (cancelled) return;
            setJob(fresh);
            if (TERMINAL.has(fresh.status)) {
              setDone(true);
              if (pollRef.current) clearInterval(pollRef.current);
            }
          } catch {
            /* keep polling */
          }
        }, 2500);
      });

    return () => {
      cancelled = true;
      controller.abort();
      esRef.current?.close();
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [jobId, retryKey]);

  return { job, done, streamFailed };
}
