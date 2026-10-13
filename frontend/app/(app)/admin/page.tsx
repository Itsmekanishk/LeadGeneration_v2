"use client";

import { useEffect, useState } from "react";
import { Activity, Database, Server, Users } from "lucide-react";
import { api } from "@/lib/api";
import type { AdminJobRow, AdminUserRow, UsageSummary } from "@/types";
import {
  AnimatedCounter, Badge, Button, Card, CardDescription, CardTitle, EmptyState, ProviderBadge,
  Skeleton, StatusBadge,
} from "@/components/ui";
import { useToast } from "@/components/legacy-ui";

type Tab = "overview" | "jobs" | "users" | "audit";

interface AuditRow {
  id: string;
  user_id?: string | null;
  action: string;
  entity: string;
  entity_id: string;
  ip_address: string;
  created_at?: string | null;
}

interface HealthResponse {
  status: string;
  app_env: string;
  checks: Record<string, string>;
}

/** Maps backend health-check strings onto badge tones. */
function healthTone(v: string): "success" | "warning" | "danger" | "neutral" {
  if (v === "ok") return "success";
  if (v === "no_workers" || v === "misconfigured" || v === "unknown") return "warning";
  if (v.startsWith("error")) return "danger";
  return "neutral";
}

const CHECK_LABEL: Record<string, { label: string; icon: React.ReactNode }> = {
  database: { label: "Postgres", icon: <Database className="h-3.5 w-3.5" /> },
  redis: { label: "Redis", icon: <Server className="h-3.5 w-3.5" /> },
  workers: { label: "Celery workers", icon: <Activity className="h-3.5 w-3.5" /> },
  provider: { label: "Data provider", icon: <Users className="h-3.5 w-3.5" /> },
};

export default function AdminPage() {
  const { push } = useToast();
  const [tab, setTab] = useState<Tab>("overview");
  const [jobs, setJobs] = useState<AdminJobRow[] | null>(null);
  const [users, setUsers] = useState<AdminUserRow[] | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    api.get<AdminJobRow[]>("/admin/jobs").then(setJobs).catch(() => setJobs([]));
    api.get<AdminUserRow[]>("/admin/users").then(setUsers).catch(() => setUsers([]));
    api.get<UsageSummary>("/admin/usage").then(setUsage).catch(() => undefined);
    api.get<AuditRow[]>("/admin/audit-logs").then(setAudit).catch(() => setAudit([]));
    api.get<HealthResponse>("/health").then(setHealth).catch(() => setHealth(null));
  }, []);

  async function toggleUser(u: AdminUserRow) {
    try {
      await api.patch(`/admin/users/${u.id}`, { is_active: !u.is_active });
      push(`User ${u.is_active ? "disabled" : "enabled"}`, "success");
      setUsers(await api.get<AdminUserRow[]>("/admin/users"));
    } catch (err) {
      push(err instanceof Error ? err.message : "Update failed", "error");
    }
  }

  async function changeRole(u: AdminUserRow, role: string) {
    try {
      await api.patch(`/admin/users/${u.id}`, { role });
      push(`Role updated to ${role}`, "success");
      setUsers(await api.get<AdminUserRow[]>("/admin/users"));
    } catch (err) {
      push(err instanceof Error ? err.message : "Update failed", "error");
    }
  }

  const TABS: { id: Tab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "jobs", label: "All jobs" },
    { id: "users", label: "Users" },
    { id: "audit", label: "Audit log" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Administration</h1>
        <p className="text-sm text-muted-foreground">
          Platform jobs, users, provider usage and audit trail.
        </p>
      </div>

      <div className="flex gap-1 rounded-lg bg-muted p-1 text-sm font-medium w-fit">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`rounded-md px-3 py-1.5 transition ${
              tab === t.id ? "bg-card shadow-sm" : "text-muted-foreground"
            }`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Spend today" value={usage?.today_cost ?? 0} format="usd" loading={usage === null} />
            <Metric label="Spend this month" value={usage?.month_cost ?? 0} format="usd" loading={usage === null} />
            <Metric label="API calls today" value={usage?.today_requests ?? 0} loading={usage === null} />
            <Metric label="Users" value={users?.length ?? 0} loading={users === null} />
          </div>

          <Card>
            <CardTitle>System health</CardTitle>
            <CardDescription>
              Live dependency checks from <code className="font-mono">/api/health</code>.
            </CardDescription>
            <div className="mt-3 flex flex-wrap gap-2">
              {health
                ? Object.entries(CHECK_LABEL).map(([key, meta]) => {
                    const value = health.checks[key] ?? "unknown";
                    return (
                      <Badge key={key} tone={healthTone(value)}>
                        {meta.icon}
                        {meta.label}: {value}
                      </Badge>
                    );
                  })
                : ["database", "redis", "workers", "provider"].map((key) => (
                    <Skeleton key={key} className="h-5 w-40" />
                  ))}
              {health && health.checks.provider && !["ok", "misconfigured"].includes(health.checks.provider) && (
                <Badge tone="danger">provider: {health.checks.provider}</Badge>
              )}
            </div>
          </Card>

          {usage && usage.by_provider.length > 0 && (
            <Card className="overflow-x-auto p-0">
              <div className="border-b ll-border p-4">
                <CardTitle>Provider usage (this month)</CardTitle>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b ll-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="p-3">Provider</th>
                    <th className="p-3 text-right">Requests</th>
                    <th className="p-3 text-right">Estimated cost</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.by_provider.map((p) => (
                    <tr key={p.provider} className="border-b ll-border last:border-0">
                      <td className="p-3 font-medium">
                        <ProviderBadge provider={p.provider} />
                      </td>
                      <td className="p-3 text-right font-mono tabular-nums">{p.requests}</td>
                      <td className="p-3 text-right font-mono tabular-nums">${p.estimated_cost.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      )}

      {tab === "jobs" && (
        <Card className="overflow-x-auto p-0">
          {jobs === null ? (
            <div className="p-4"><Skeleton className="h-24" /></div>
          ) : jobs.length === 0 ? (
            <div className="p-4"><EmptyState title="No jobs yet" description="Jobs from all users will appear here." /></div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b ll-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="p-3">Query</th>
                  <th className="p-3">User</th>
                  <th className="p-3">Provider</th>
                  <th className="p-3">Status</th>
                  <th className="p-3 text-right">Progress</th>
                  <th className="p-3 text-right">Emails</th>
                  <th className="p-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className="border-b ll-border last:border-0">
                    <td className="max-w-[240px] truncate p-3 font-medium">{j.query_raw}</td>
                    <td className="p-3">{j.user_email}</td>
                    <td className="p-3"><ProviderBadge provider={j.provider} /></td>
                    <td className="p-3"><StatusBadge status={j.status} /></td>
                    <td className="p-3 text-right font-mono tabular-nums">{j.progress}%</td>
                    <td className="p-3 text-right font-mono tabular-nums">{j.emails_found}</td>
                    <td className="p-3 text-xs text-muted-foreground">
                      {j.created_at ? new Date(j.created_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {tab === "users" && (
        <Card className="overflow-x-auto p-0">
          {users === null ? (
            <div className="p-4"><Skeleton className="h-24" /></div>
          ) : users.length === 0 ? (
            <div className="p-4"><EmptyState title="No users yet" /></div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b ll-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="p-3">Email</th>
                  <th className="p-3">Name</th>
                  <th className="p-3">Role</th>
                  <th className="p-3 text-right">Jobs</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-b ll-border last:border-0">
                    <td className="p-3 font-medium">{u.email}</td>
                    <td className="p-3">{u.full_name || "—"}</td>
                    <td className="p-3">
                      <select
                        className="input h-8 w-24 text-xs"
                        value={u.role}
                        onChange={(e) => changeRole(u, e.target.value)}
                      >
                        <option value="USER">USER</option>
                        <option value="ADMIN">ADMIN</option>
                      </select>
                    </td>
                    <td className="p-3 text-right font-mono tabular-nums">{u.job_count}</td>
                    <td className="p-3">
                      <Badge tone={u.is_active ? "success" : "danger"}>
                        {u.is_active ? "active" : "disabled"}
                      </Badge>
                    </td>
                    <td className="p-3">
                      <Button variant="ghost" size="sm" onClick={() => toggleUser(u)}>
                        {u.is_active ? "Disable" : "Enable"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {tab === "audit" && (
        <Card className="overflow-x-auto p-0">
          {audit === null ? (
            <div className="p-4"><Skeleton className="h-24" /></div>
          ) : audit.length === 0 ? (
            <div className="p-4"><EmptyState title="No audit entries" description="Admin actions will be recorded here." /></div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b ll-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="p-3">When</th>
                  <th className="p-3">Action</th>
                  <th className="p-3">Entity</th>
                  <th className="p-3">IP</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((a) => (
                  <tr key={a.id} className="border-b ll-border last:border-0">
                    <td className="p-3 font-mono text-xs tabular-nums text-muted-foreground">
                      {a.created_at ? new Date(a.created_at).toLocaleString() : "—"}
                    </td>
                    <td className="p-3 font-medium">{a.action}</td>
                    <td className="p-3 font-mono text-xs">{a.entity} {a.entity_id?.slice(0, 8)}</td>
                    <td className="p-3 font-mono text-xs text-muted-foreground">{a.ip_address || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}

function Metric({
  label, value, loading, format,
}: { label: string; value: number; loading: boolean; format?: "usd" }) {
  return (
    <Card>
      <p className="text-xs text-muted-foreground">{label}</p>
      {loading ? (
        <Skeleton className="mt-1 h-7 w-16" />
      ) : (
        <AnimatedCounter
          className="mt-1 block text-2xl font-bold"
          value={value}
          suffix={format === "usd" ? " USD" : undefined}
        />
      )}
    </Card>
  );
}
