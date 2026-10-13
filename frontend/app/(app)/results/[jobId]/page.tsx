"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle, Building2, CheckCircle2, ChevronDown, Copy, Download,
  FileSpreadsheet, FileText, Globe, Info, LayoutGrid, Mail, Phone, ShieldCheck,
  Table2, Timer,
} from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useJobStream } from "@/hooks/useJobStream";
import type { Business, BusinessDetail, Job, PaginatedBusinesses, Summary } from "@/types";
import {
  AnimatedCounter, Badge, Button, Card, ConfidenceBadge, CopyButton, DedupeBadge,
  Drawer, EmptyState, ErrorBanner, Input, LeadRowSkeleton, ProviderBadge, RadarPulse,
  Skeleton, Switch, Tabs,
} from "@/components/ui";
import { useToast } from "@/components/legacy-ui";
import { CrawlProgress, STATUS_LABEL, isTerminal } from "@/components/crawl-progress";

type ViewMode = "table" | "grid";

export default function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const router = useRouter();
  const { push } = useToast();

  const [job, setJob] = useState<Job | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [data, setData] = useState<PaginatedBusinesses | null>(null);
  const [loading, setLoading] = useState(true);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [streamRetry, setStreamRetry] = useState(0);

  // filters (client-side; mapped onto backend query params)
  const [page, setPage] = useState(1);
  const [pageSize] = useState(25);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [minRating, setMinRating] = useState("");
  const [sort, setSort] =
    useState<"name" | "rating" | "review_count" | "contacts">("name");
  const [hasEmail, setHasEmail] = useState(false);
  const [hasPhone, setHasPhone] = useState(false);
  const [highConfOnly, setHighConfOnly] = useState(false);
  const [uniqueOnly, setUniqueOnly] = useState(true);

  // view + audit drawer
  const [view, setView] = useState<ViewMode>("table");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<BusinessDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // exports
  const [exporting, setExporting] = useState<string | null>(null);

  /** Reload the job + summary; `silent` skips the full-page skeleton flash. */
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [jobRes, sumRes] = await Promise.all([
        api.get<Job>(`/search/${jobId}`),
        api.get<{ job: Job; summary: Summary }>(`/search/${jobId}/summary`),
      ]);
      setJob(jobRes);
      setSummary(sumRes.summary);
      setResultsError(null);
    } catch (err) {
      setResultsError(err instanceof Error ? err.message : "Failed to load search");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live progress via the resilient SSE hook (fetch-stream + poll fallback).
  const { job: liveJob, streamFailed } = useLiveJob(jobId, streamRetry, job, (fresh) => {
    setJob((prev) => prev ?? fresh);
  });

  const shownJob = liveJob ?? job;
  const running = !!shownJob && !isTerminal(shownJob.status);

  useEffect(() => {
    // When the stream reports a terminal state, refresh summary + rows once.
    if (isTerminal(shownJob?.status)) {
      void load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownJob?.status]);

  const loadResults = useCallback(async () => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
      sort: sort === "name" ? "name" : sort,
    });
    if (q) params.set("q", q);
    if (category) params.set("category", category);
    if (minRating) params.set("min_rating", minRating);
    if (hasEmail) params.set("has_email", "true");
    if (hasPhone) params.set("has_phone", "true");
    if (highConfOnly) params.set("high_confidence_email", "true");
    params.set("dedup", uniqueOnly ? "unique" : "all");
    try {
      setData(await api.get<PaginatedBusinesses>(`/search/${jobId}/results?${params}`));
      setResultsError(null);
    } catch (err) {
      setResultsError(err instanceof Error ? err.message : "Failed to load results");
    }
  }, [jobId, page, pageSize, q, category, minRating, hasEmail, hasPhone, highConfOnly, uniqueOnly, sort]);

  useEffect(() => {
    void loadResults();
  }, [loadResults]);

  // While the crawl runs, pull fresh counters and rows on a timer so this page
  // fills in live instead of sitting empty until the job finishes.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      void load(true);
      void loadResults();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [running, load, loadResults]);

  async function openDetail(b: Business) {
    setDrawerOpen(true);
    setDetailLoading(true);
    setDetail(null);
    try {
      setDetail(await api.get<BusinessDetail>(`/business/${b.id}`));
    } catch (err) {
      push(err instanceof Error ? err.message : "Failed to load business", "error");
      setDrawerOpen(false);
    } finally {
      setDetailLoading(false);
    }
  }

  async function doExport(fmt: "pdf" | "csv" | "xlsx") {
    setExporting(fmt);
    try {
      const res = await api.post<{ filename: string }>(`/search/${jobId}/export/${fmt}`);
      await downloadFile(jobId, fmt, res.filename);
      push(`${fmt.toUpperCase()} downloaded`, "success");
    } catch (err) {
      push(err instanceof Error ? err.message : "Export failed", "error");
    } finally {
      setExporting(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-72" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <Card className="p-0">
          {Array.from({ length: 6 }).map((_, i) => (
            <LeadRowSkeleton key={i} />
          ))}
        </Card>
      </div>
    );
  }

  if (!job) {
    return (
      <ErrorBanner
        title="Search not found"
        description="It may belong to another account or has been removed."
        onRetry={() => void load()}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <button
            className="mb-1 text-sm text-muted-foreground hover:underline"
            onClick={() => router.push("/history")}
          >
            ← All searches
          </button>
          <h1 className="text-xl font-bold">{job.query_raw}</h1>
          <p className="text-sm text-muted-foreground">
            {job.parsed_category || "All industries"} · {job.parsed_location || "—"} ·{" "}
            {job.status}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" loading={exporting === "pdf"} onClick={() => doExport("pdf")}>
            <FileText className="h-4 w-4" /> PDF
          </Button>
          <Button variant="secondary" loading={exporting === "xlsx"} onClick={() => doExport("xlsx")}>
            <FileSpreadsheet className="h-4 w-4" /> Excel
          </Button>
          <Button variant="secondary" loading={exporting === "csv"} onClick={() => doExport("csv")}>
            <Download className="h-4 w-4" /> CSV
          </Button>
        </div>
      </div>

      {/* live radar while the pipeline is still running */}
      {running && (
        <RadarPulse
          phase={shownJob!.stage_detail || shownJob!.status}
          active
        />
      )}

      {/* SSE transport failure — visible, with real reconnect */}
      {streamFailed && running && (
        <ErrorBanner
          title="Live stream interrupted"
          description="Updates now arrive by polling. Retry to reconnect the live stream."
          onRetry={() => setStreamRetry((n) => n + 1)}
        />
      )}

      {/* results fetch failure */}
      {resultsError && (
        <ErrorBanner
          title="Couldn't load results"
          description={resultsError}
          onRetry={() => {
            void load();
            void loadResults();
          }}
        />
      )}

      {/* summary stat tiles — contact totals first: that is what you came for.
          Hidden while a crawl is still producing its first rows, so the page
          doesn't open on a wall of zeros above the progress bar. */}
      {summary && !(running && summary.total === 0) && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile icon={<Mail className="h-4 w-4" />} label="Emails collected"
            value={summary.emails_total} hint="unique addresses found" accent />
          <StatTile icon={<Phone className="h-4 w-4" />} label="Phones collected"
            value={summary.phones_total} hint="unique numbers found" accent />
          <StatTile icon={<Building2 className="h-4 w-4" />} label="Businesses"
            value={summary.unique} hint={`${summary.duplicates_removed} duplicates merged`} />
          <StatTile icon={<Globe className="h-4 w-4" />} label="Websites"
            value={summary.with_website} />
          <StatTile icon={<Mail className="h-4 w-4" />} label="Leads with email"
            value={summary.with_email} />
          <StatTile icon={<Phone className="h-4 w-4" />} label="Leads with phone"
            value={summary.with_phone} />
          <StatTile icon={<ShieldCheck className="h-4 w-4" />} label="High-confidence emails"
            value={summary.high_confidence_emails} />
          <StatTile icon={<AlertTriangle className="h-4 w-4" />} label="Sites not scanned"
            value={summary.failed_websites} />
        </div>
      )}

      {/* provider fallback transparency */}
      {shownJob?.stage_detail?.toLowerCase().includes("fallback") && (
        <div className="flex items-start gap-2 rounded-lg border ll-border bg-white/[0.03] px-3 py-2 text-sm text-slate-300">
          <Info className="mt-0.5 h-4 w-4 text-ll-cyan" />
          <span>{shownJob.stage_detail}</span>
        </div>
      )}

      {/* requested vs. available honesty notice */}
      {summary && job.status === "completed" && (
        <AvailabilityNotice
          requested={data?.requested_results || job.requested_results || job.max_results}
          available={summary.unique}
          platformLimit={job.max_results}
        />
      )}

      {/* honest completion status */}
      {summary && <DataQualityBanner summary={summary} jobStatus={job.status} />}

      {/* filters + view switch — hidden while a crawl has produced nothing to
          filter yet, so the progress panel is the only thing on screen */}
      {!(running && (!data || data.items.length === 0)) && (
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="Search name, address, email…"
            className="max-w-xs"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
          <Input
            placeholder="Category"
            className="max-w-[140px]"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(1);
            }}
          />
          <Input
            placeholder="Min rating"
            type="number" min={0} max={5} step={0.1}
            className="max-w-[110px]"
            value={minRating}
            onChange={(e) => {
              setMinRating(e.target.value);
              setPage(1);
            }}
          />
          <select
            className="input max-w-[160px]"
            value={sort}
            onChange={(e) => setSort(e.target.value as typeof sort)}
          >
            <option value="name">Sort: Name</option>
            <option value="rating">Sort: Rating</option>
            <option value="review_count">Sort: Reviews</option>
            <option value="contacts">Sort: Most contacts</option>
          </select>
          <div className="ml-auto">
            <Tabs
              layoutGroupId="results-view"
              value={view}
              onChange={setView}
              options={[
                { value: "table", label: "Table", icon: <Table2 className="h-3.5 w-3.5" /> },
                { value: "grid", label: "Grid", icon: <LayoutGrid className="h-3.5 w-3.5" /> },
              ]}
            />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 border-t ll-border pt-3">
          <Switch id="f-email" checked={hasEmail} onChange={(v) => { setHasEmail(v); setPage(1); }} label="Has email" />
          <Switch id="f-phone" checked={hasPhone} onChange={(v) => { setHasPhone(v); setPage(1); }} label="Has phone" />
          <Switch id="f-high" checked={highConfOnly} onChange={(v) => { setHighConfOnly(v); setPage(1); }} label="High confidence only" />
          <Switch id="f-unique" checked={uniqueOnly} onChange={(v) => { setUniqueOnly(v); setPage(1); }} label="Unique leads only" />
          {/* Drives the same min_rating filter as the number box, so the two
              controls can never disagree about what is being applied. */}
          <Switch id="f-stars" checked={minRating === "4"}
            onChange={(v) => { setMinRating(v ? "4" : ""); setPage(1); }}
            label="Min 4.0★" />
          <div className="ml-auto">
            <BulkCopyActions items={data?.items ?? []} onCopied={push} />
          </div>
        </div>
      </Card>
      )}

      {/* Still crawling with nothing to show yet: report live progress rather
          than a misleading "no results match your filters" empty state. */}
      {running && (!data || data.items.length === 0) ? (
        <Card>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="font-semibold">
              {STATUS_LABEL[shownJob!.status] ?? shownJob!.status}
            </h2>
            <Badge tone="ll-cyan">{shownJob!.progress}%</Badge>
          </div>
          <CrawlProgress job={shownJob!} />
          <p className="mt-3 text-xs text-muted-foreground">
            Contacts appear here as each business is enriched — this page refreshes itself.
          </p>
        </Card>
      ) : data && data.items.length > 0 ? (
        view === "table" ? (
          <Card className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b ll-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="p-3">Business</th>
                  <th className="p-3">Category</th>
                  <th className="p-3">Phone</th>
                  <th className="p-3">Email</th>
                  <th className="p-3">Website</th>
                  <th className="p-3 text-right">Rating</th>
                  <th className="p-3 text-right">Reviews</th>
                  <th className="p-3">Confidence</th>
                  <th className="p-3">Origin</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((b) => (
                  <tr
                    key={b.id}
                    tabIndex={0}
                    aria-label={`Open audit trail for ${b.name}`}
                    className="cursor-pointer border-b ll-border transition hover:bg-white/[0.04] focus-visible:bg-white/[0.04]"
                    onClick={() => openDetail(b)}
                    onKeyDown={(e) => e.key === "Enter" && openDetail(b)}
                  >
                    <td className="p-3 font-medium">
                      {b.name}
                      <span className="ml-2"><DedupeBadge unique={b.dedup_status === "unique"} /></span>
                      {b.contact_count > 1 && (
                        <span className="mt-0.5 block text-[11px] font-normal text-ll-cyan">
                          {b.contact_count} contact values
                        </span>
                      )}
                    </td>
                    <td className="p-3">{b.category || "—"}</td>
                    <td className="p-3 min-w-[150px]">
                      <ContactList values={b.phones} mono label="phone" />
                    </td>
                    <td className="p-3 min-w-[200px]">
                      <ContactList values={b.emails} label="email" />
                    </td>
                    <td className="p-3 max-w-[220px] truncate">{b.website || "—"}</td>
                    <td className="p-3 text-right font-mono tabular-nums">{b.rating ?? "—"}</td>
                    <td className="p-3 text-right font-mono tabular-nums">{b.review_count ?? "—"}</td>
                    <td className="p-3">
                      {b.email ? (
                        <ConfidenceBadge level={b.email_confidence} />
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="p-3">
                      {b.phone_source ? <ProviderBadge provider={b.phone_source} /> : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination data={data} page={page} setPage={setPage} />
          </Card>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {data.items.map((b) => (
                <Card
                  key={b.id}
                  interactive
                  className="cursor-pointer space-y-2"
                  onClick={() => openDetail(b)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium leading-snug">{b.name}</p>
                    <DedupeBadge unique={b.dedup_status === "unique"} />
                  </div>
                  <p className="text-xs text-muted-foreground">{b.category || "—"} · {b.address || "—"}</p>
                  <div className="flex items-start gap-1.5 text-sm">
                    <Phone className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <ContactList values={b.phones} mono label="phone" className="min-w-0" />
                  </div>
                  <div className="flex items-start gap-1.5 text-sm">
                    <Mail className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <ContactList values={b.emails} label="email" className="min-w-0" />
                  </div>
                  <div className="flex items-center justify-between pt-1">
                    <ConfidenceBadge level={b.email ? b.email_confidence : ""} />
                    <span className="font-mono text-xs tabular-nums text-muted-foreground">
                      {b.rating != null ? `${b.rating}★` : "—"}
                      {b.review_count != null ? ` (${b.review_count})` : ""}
                    </span>
                  </div>
                </Card>
              ))}
            </div>
            <Card className="p-0">
              <Pagination data={data} page={page} setPage={setPage} />
            </Card>
          </>
        )
      ) : (
        <EmptyState
          title="No results match your filters"
          description="Try clearing a filter or running a new search."
          icon={<Building2 className="h-8 w-8 text-muted-foreground" />}
          action={{
            label: "Clear filters",
            onClick: () => {
              setQ(""); setCategory(""); setMinRating("");
              setHasEmail(false); setHasPhone(false); setHighConfOnly(false);
              setUniqueOnly(true); setSort("name"); setPage(1);
            },
          }}
        />
      )}

      {/* crawl audit trail drawer */}
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="Crawl audit trail"
        description={detail ? detail.name : "Loading…"}
      >
        {detailLoading || !detail ? (
          <div className="space-y-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        ) : (
          <div className="space-y-5">
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Record
              </h3>
              <dl className="grid gap-2 text-sm">
                <Field icon={<Phone className="h-4 w-4" />}
                  label={detail.phones.length > 1 ? `Phones (${detail.phones.length})` : "Phone"}>
                  <ContactList values={detail.phones} mono label="phone" />
                  {detail.phone_source && <ProviderBadge provider={detail.phone_source} />}
                </Field>
                <Field icon={<Mail className="h-4 w-4" />}
                  label={detail.emails.length > 1 ? `Emails (${detail.emails.length})` : "Email"}>
                  <ContactList values={detail.emails} label="email" />
                  {detail.email && <ConfidenceBadge level={detail.email_confidence} />}
                </Field>
                <Field icon={<Globe className="h-4 w-4" />} label="Website">
                  {detail.website || "—"}
                </Field>
                <Field icon={<Building2 className="h-4 w-4" />} label="Rating">
                  {detail.rating ?? "—"} {detail.review_count ? `(${detail.review_count} reviews)` : ""}
                </Field>
              </dl>
            </section>

            {detail.contacts.length > 0 && (
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Contacts &amp; sources
                </h3>
                <ul className="space-y-2">
                  {detail.contacts.map((c) => (
                    <li key={c.id} className="rounded-lg border ll-border bg-white/[0.02] p-3 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium">{c.email || c.phone_normalized || c.phone}</span>
                        <ConfidenceBadge level={c.confidence} />
                      </div>
                      {c.source_url && (
                        <p className="mt-1 truncate text-xs text-muted-foreground">
                          Source:{" "}
                          <a className="text-ll-cyan hover:underline" href={c.source_url} target="_blank" rel="noreferrer">
                            {c.source_url}
                          </a>
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground">via {c.source}</p>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {detail.crawl_pages.length > 0 && (
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Crawled pages
                </h3>
                <ul className="space-y-2">
                  {detail.crawl_pages.map((p) => {
                    const ok = p.status === "ok" && p.http_status != null && p.http_status < 400;
                    return (
                      <li key={p.id} className="rounded-lg border ll-border bg-white/[0.02] p-3 text-sm">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="truncate font-mono text-xs text-slate-300">{p.url}</span>
                          <Badge tone={ok ? "success" : "danger"}>
                            {p.http_status ?? "—"} · {p.status}
                          </Badge>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                          <Badge tone={p.status === "robots_disallowed" ? "danger" : "success"}>
                            robots.txt {p.status === "robots_disallowed" ? "disallowed" : "allowed"}
                          </Badge>
                          <span className="flex items-center gap-1">
                            <Timer className="h-3 w-3" />
                            <span className="font-mono tabular-nums">{p.duration_ms} ms</span>
                          </span>
                          <span>{p.emails_found} emails · {p.phones_found} phones</span>
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <ShieldCheck className="h-3.5 w-3.5 text-ll-confidence-high" />
                  SSRF guard: private, loopback and metadata IP ranges blocked before every fetch.
                </p>
              </section>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

/* ------------------------- hooks & shared pieces -------------------------- */

// Thin wrapper so the page can merge the stream onto the loaded snapshot.
function useLiveJob(
  jobId: string,
  retryKey: number,
  initial: Job | null,
  onFirst: (job: Job) => void,
) {
  const stream = useJobStream(jobId, initial ?? undefined, retryKey);
  useEffect(() => {
    if (stream.job) onFirst(stream.job);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.job?.id]);
  return stream;
}

function StatTile({
  icon, label, value, hint, accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  hint?: string;
  /** Highlights the headline "how much did we collect" tiles. */
  accent?: boolean;
}) {
  return (
    <Card className={cn(accent && "border-ll-cyan/25 bg-ll-cyan/[0.06]")}>
      <div className={cn("flex items-center gap-1.5", accent ? "text-ll-cyan" : "text-muted-foreground")}>
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <AnimatedCounter className="mt-1 block text-2xl font-bold" value={value} />
      {hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>}
    </Card>
  );
}

function Pagination({ data, page, setPage }: { data: PaginatedBusinesses; page: number; setPage: (fn: (p: number) => number) => void }) {
  return (
    <div className="flex items-center justify-between p-3 text-sm">
      <span className="text-muted-foreground">
        {data.total} results · page {data.page} / {Math.max(data.pages, 1)}
      </span>
      <div className="flex gap-2">
        <Button variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          Previous
        </Button>
        <Button variant="ghost" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}

function Field({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border ll-border p-3">
      <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {icon} {label}
      </dt>
      <dd className="mt-1 flex flex-wrap items-center gap-2 break-all font-medium">{children}</dd>
    </div>
  );
}

/**
 * Renders every discovered contact value for a lead: the primary value, then a
 * "+N more" chip that expands the rest. Each value gets its own copy button,
 * and the block swallows row clicks so copying never opens the audit drawer.
 */
function ContactList({
  values, mono, label = "value", className,
}: {
  values: string[];
  mono?: boolean;
  label?: string;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const clean = (values ?? []).map((v) => (v || "").trim()).filter(Boolean);

  if (clean.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  const [primary, ...rest] = clean;
  const shown = expanded ? clean : [primary];
  return (
    <div
      className={cn("flex flex-col gap-1", className)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {shown.map((value) => (
        <span key={value} className="flex items-center gap-1">
          <span className={cn("break-all", mono && "font-mono text-xs")}>{value}</span>
          <CopyButton value={value} label={`Copy ${label}`} />
        </span>
      ))}
      {rest.length > 0 && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex w-fit items-center gap-0.5 rounded text-[11px] font-medium text-ll-cyan transition-colors hover:underline"
        >
          {expanded ? "Show less" : `+${rest.length} more`}
          <ChevronDown className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")} />
        </button>
      )}
    </div>
  );
}

/** Distinct, non-empty, case-insensitively unique contact values. */
function dedupeValues(values: (string | undefined)[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of values) {
    const value = (raw || "").trim();
    if (!value) continue;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}

/**
 * One click copies every contact on the current page — emails comma-separated
 * (paste into a mail merge / CRM import) and phones newline-separated (paste
 * into a dialer). Falls back to the visible plain values when the API omits
 * the contact arrays (legacy payloads).
 */
function BulkCopyActions({
  items, onCopied,
}: {
  items: Business[];
  onCopied: (message: string, tone?: "success" | "error") => void;
}) {
  const emails = dedupeValues(
    items.flatMap((b) => (b.emails?.length ? b.emails : [b.email]))
  );
  const phones = dedupeValues(
    items.flatMap((b) => (b.phones?.length ? b.phones : [b.phone_normalized || b.phone]))
  );

  async function copy(values: string[], separator: string, what: string) {
    if (values.length === 0) {
      onCopied(`No ${what} on this page`, "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(values.join(separator));
      onCopied(`Copied ${values.length} ${what}`, "success");
    } catch {
      onCopied("Clipboard unavailable — check browser permissions", "error");
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="ghost" size="sm" disabled={emails.length === 0}
        onClick={() => void copy(emails, ", ", "emails")}>
        <Copy className="h-3.5 w-3.5" /> Copy page emails ({emails.length})
      </Button>
      <Button variant="ghost" size="sm" disabled={phones.length === 0}
        onClick={() => void copy(phones, "\n", "phones")}>
        <Copy className="h-3.5 w-3.5" /> Copy page phones ({phones.length})
      </Button>
    </div>
  );
}

/**
 * Honest accounting of "what the user asked for" vs "what the data source had".
 * Backend sets stage_detail on the job; the frontend computes the same message
 * from requested_results + total so it also works for legacy jobs.
 */
function AvailabilityNotice({
  requested, available, platformLimit,
}: { requested: number; available: number; platformLimit: number }) {
  if (available >= requested) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-ll-confidence-high/30 bg-ll-confidence-high-soft px-3 py-2 text-sm text-ll-confidence-high">
        <CheckCircle2 className="h-4 w-4" />
        Showing all {available} available results — your full request of {requested} was delivered.
      </div>
    );
  }
  if (requested > platformLimit) {
    return (
      <div className="flex items-start gap-2 rounded-lg border ll-border bg-white/[0.03] px-3 py-2 text-sm text-slate-300">
        <Info className="mt-0.5 h-4 w-4 text-ll-cyan" />
        <span>
          Requested {requested} leads; the platform limit is {platformLimit} per search — showing all{" "}
          {available} available results.
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border border-ll-confidence-medium/30 bg-ll-confidence-medium-soft px-3 py-2 text-sm text-ll-confidence-medium">
      <AlertTriangle className="mt-0.5 h-4 w-4" />
      <span>
        Requested {requested} leads, but only {available} are available for this search — showing
        all available data. Try broadening the location or industry to find more.
      </span>
    </div>
  );
}

function DataQualityBanner({ summary, jobStatus }: { summary: Summary; jobStatus: string }) {
  const partial = summary.failed_websites > 0 || summary.duplicates_removed > 0;
  // Only a finished search can be judged: mid-crawl this is premature, and a
  // failed/cancelled job never completed its scans.
  if (jobStatus !== "completed") return null;
  if (!partial) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-ll-confidence-high/30 bg-ll-confidence-high-soft px-3 py-2 text-sm text-ll-confidence-high">
        <CheckCircle2 className="h-4 w-4" />
        Completed — all website scans succeeded and no duplicates needed removal.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-ll-confidence-medium/30 bg-ll-confidence-medium-soft px-3 py-2 text-sm text-ll-confidence-medium">
      <p className="flex items-center gap-2 font-medium">
        <AlertTriangle className="h-4 w-4" />
        Completed with partial data
      </p>
      <p className="mt-0.5 pl-6 text-xs">
        {summary.failed_websites > 0 && (
          <>
            {summary.failed_websites} website{summary.failed_websites === 1 ? "" : "s"} could not be scanned (offline or blocked);{" "}
          </>
        )}
        {summary.duplicates_removed > 0 && (
          <>
            {summary.duplicates_removed} duplicate{summary.duplicates_removed === 1 ? "" : "s"} merged.{" "}
          </>
        )}
        Other results remain fully usable.
      </p>
    </div>
  );
}
