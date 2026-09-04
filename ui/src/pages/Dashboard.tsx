import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { ChevronRight, Upload } from "lucide-react";
import {
  listInvoices, getAgingSummary, uploadAgingExcel, listAgentCases,
  type Invoice, type AgingSummary, type AgentCase,
} from "../api";
import { useSession } from "../context/SessionContext";
import { AgentPhaseRail } from "../components/AgentPhaseRail";
import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Input";
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge, type BadgeTone } from "../components/ui/Badge";
import { Divider } from "../components/ui/Divider";

const OPEN_AGENT_STATES = new Set([
  "not_due", "due", "overdue", "outreach_ready", "waiting_for_customer", "customer_responded",
  "blocked", "follow_up_scheduled", "promise_to_pay", "promise_missed", "escalation_required",
]);

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

const STATUS_TONE: Record<string, BadgeTone> = {
  active: "positive",
  closed_paid: "neutral",
  closed_other: "neutral",
  snoozed: "warning",
};

function StatusBadge({ label }: { label: string | null }) {
  if (!label) return <span className="font-mono text-xs text-muted-foreground">--</span>;
  return (
    <Badge tone={STATUS_TONE[label] ?? "neutral"}>{label.replace(/_/g, " ")}</Badge>
  );
}

export default function Dashboard() {
  const { token, setPinnedInvoice, setCurrentProject } = useSession();
  const navigate = useNavigate();
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [summary, setSummary] = useState<AgingSummary | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [agentCases, setAgentCases] = useState<AgentCase[]>([]);
  // Collapsed by default (added 2026-07-23) -- with every project always
  // expanded this page was a wall of tables; an accordion makes "click a
  // project to see its invoices" the actual navigation model the boss asked
  // for, instead of a flat scroll.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!token) return;
    getAgingSummary(token).then(setSummary).catch((e) => setError(String(e)));
  }, [token, refreshKey]);

  useEffect(() => {
    if (!token) return;
    listAgentCases(token).then(setAgentCases).catch(() => setAgentCases([]));
  }, [token, refreshKey]);

  // Poll so agent activity (a reply, a new outreach, a payment tracked)
  // shows up on the homepage without a manual reload (added 2026-08-06,
  // user request).
  useEffect(() => {
    if (!token) return;
    const id = setInterval(() => setRefreshKey((k) => k + 1), 15000);
    return () => clearInterval(id);
  }, [token]);

  function toggleExpanded(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  useEffect(() => {
    if (!token) return;
    listInvoices(token, {
      status: statusFilter || undefined,
    })
      .then(setInvoices)
      .catch((e) => setError(String(e)));
  }, [token, statusFilter, refreshKey]);

  async function handleUploadFile(file: File) {
    if (!token) return;
    setUploading(true);
    setUploadMessage(null);
    setUploadError(null);
    try {
      const result = await uploadAgingExcel(token, file);
      const s = (result.sync as { summary?: { inserted?: number; updated?: number; marked_as_paid?: number } })?.summary;
      const counts = s
        ? ` (${s.inserted ?? 0} new, ${s.updated ?? 0} updated, ${s.marked_as_paid ?? 0} marked paid)`
        : "";
      setUploadMessage(
        result.tick_error
          ? `Aging data synced${counts}, but the engine tick didn't run: ${result.tick_error}`
          : `Aging data synced${counts} and the engine has been ticked.`
      );
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setUploadError(String(e));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function askAboutInvoice(inv: Invoice) {
    const label = `${inv.project_name ?? inv.project_number ?? "Unknown project"} -- ${money(inv.open_amount)}, ${
      inv.aging_status ?? "unknown aging"
    }`;
    setPinnedInvoice({ invoice_id: inv.invoice_id, label });
    // Project-scoped chat (added 2026-07-23): set the project too, so
    // Dashboard's handoff skips Chat's project picker entirely.
    if (inv.project_number) {
      setCurrentProject({ project_number: inv.project_number, project_name: inv.project_name });
    }
    navigate("/chat");
  }

  // Grouped by project so every project stays visible as its own section
  // (matching Project Contacts' layout) instead of a flat table that
  // repeats the project name on every row -- each invoice is then
  // identified by its own invoice number within that section. Added
  // 2026-07-16.
  const projectGroups = (() => {
    const byProject = new Map<string, { project_number: string | null; project_name: string | null; invoices: Invoice[] }>();
    for (const inv of invoices) {
      const key = inv.project_number ?? inv.project_name ?? "unknown";
      if (!byProject.has(key)) {
        byProject.set(key, { project_number: inv.project_number, project_name: inv.project_name, invoices: [] });
      }
      byProject.get(key)!.invoices.push(inv);
    }
    return [...byProject.values()].sort((a, b) => (a.project_name ?? "").localeCompare(b.project_name ?? ""));
  })();

  function casesForProject(projectNumber: string | null): AgentCase[] {
    if (!projectNumber) return [];
    return agentCases.filter((c) => c.project_number === projectNumber);
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 sm:px-12">
      <div className="mb-10 flex flex-col items-start justify-between gap-6 sm:flex-row sm:items-end">
        <div>
          <Eyebrow as="p" tone="accent" className="mb-3">Live snapshot</Eyebrow>
          <h1 className="font-display text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">Aging Overview</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">Every open invoice across every project, as of the last sync.</p>
        </div>
        <div className="shrink-0">
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleUploadFile(file);
            }}
          />
          <Button variant="secondary" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            <Upload size={16} strokeWidth={1.5} aria-hidden />
            {uploading ? "Uploading…" : "Upload aging Excel"}
          </Button>
        </div>
      </div>

      {error && <p className="mb-4 border border-accent px-4 py-3 text-sm text-accent" role="alert">{error}</p>}
      {uploadMessage && (
        <p className="mb-4 border border-border px-4 py-3 text-sm text-foreground">{uploadMessage}</p>
      )}
      {uploadError && <p className="mb-4 border border-accent px-4 py-3 text-sm text-accent" role="alert">{uploadError}</p>}

      {summary && (
        <div className="mb-10 grid grid-cols-2 divide-x divide-y divide-border border border-border sm:grid-cols-4 sm:divide-y-0">
          <div className="px-5 py-6">
            <Eyebrow as="p" className="mb-2">Total Open</Eyebrow>
            <p className="font-mono text-3xl font-semibold tabular-nums text-foreground sm:text-4xl">
              {money(summary.total_open_amount)}
            </p>
          </div>
          <div className="px-5 py-6">
            <Eyebrow as="p" className="mb-2">Invoices</Eyebrow>
            <p className="font-mono text-3xl font-semibold tabular-nums text-foreground sm:text-4xl">
              {summary.total_invoices}
            </p>
          </div>
          {Object.entries(summary.by_stage)
            .slice(0, 2)
            .map(([stage, s]) => (
              <div key={stage} className="px-5 py-6">
                <Eyebrow as="p" className="mb-2 truncate">{stage.replace(/_/g, " ")}</Eyebrow>
                <p className="font-mono text-3xl font-semibold tabular-nums text-foreground sm:text-4xl">
                  {s.count}
                  <span className="ml-1 text-base font-normal text-muted-foreground">/ {money(s.open_amount)}</span>
                </p>
              </div>
            ))}
        </div>
      )}

      <div className="mb-6 flex items-center gap-3">
        <Eyebrow as="label" htmlFor="dashboard-status-filter">Filter</Eyebrow>
        <Select
          id="dashboard-status-filter"
          dense
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-auto"
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="closed_paid">Closed (paid)</option>
          <option value="closed_other">Closed (other)</option>
        </Select>
      </div>

      {invoices.length === 0 && (
        <div className="border border-border px-5 py-16 text-center">
          <p className="font-display text-2xl font-semibold tracking-tight text-foreground">No invoices match these filters.</p>
        </div>
      )}

      <div className="space-y-3">
        {projectGroups.map((group) => {
          const key = group.project_number ?? group.project_name ?? "unknown";
          const isOpen = expanded.has(key);
          const projectCases = casesForProject(group.project_number);
          const activeCases = projectCases.filter((c) => OPEN_AGENT_STATES.has(c.state));
          const escalatedCount = projectCases.filter(
            (c) => c.state === "escalated_to_human" || c.state === "escalation_required"
          ).length;
          const trackedCount = projectCases.filter((c) => c.state === "promise_to_pay").length;

          return (
            <div key={key} className="border border-border">
              <button
                onClick={() => toggleExpanded(key)}
                className="flex w-full items-center gap-3 px-5 py-4 text-left transition-colors duration-150 ease-bold hover:bg-muted"
                aria-expanded={isOpen}
              >
                <ChevronRight
                  size={16}
                  strokeWidth={1.5}
                  aria-hidden
                  className={`shrink-0 text-muted-foreground transition-transform duration-150 ease-bold ${isOpen ? "rotate-90" : ""}`}
                />
                <span className="flex h-8 w-8 shrink-0 items-center justify-center border border-border font-mono text-xs font-semibold text-foreground">
                  {(group.project_name ?? group.project_number ?? "?").slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">
                    {group.project_name ?? group.project_number ?? "Unknown project"}
                  </p>
                  {group.project_number && (
                    <p className="font-mono text-xs text-muted-foreground">{group.project_number}</p>
                  )}
                </div>
                <span className="ml-auto flex shrink-0 items-center gap-2">
                  {projectCases.length > 0 && (
                    <Badge tone={escalatedCount > 0 ? "critical" : activeCases.length > 0 ? "positive" : "neutral"}>
                      {activeCases.length} active{escalatedCount > 0 ? ` / ${escalatedCount} escalated` : ""}
                    </Badge>
                  )}
                  {group.invoices.length > 0 && (
                    <Badge tone="neutral" title="Invoices with a tracked payment date">
                      {trackedCount}/{group.invoices.length} tracked
                    </Badge>
                  )}
                  <Badge tone="neutral">
                    {group.invoices.length} invoice{group.invoices.length === 1 ? "" : "s"}
                  </Badge>
                </span>
              </button>

              {isOpen && (
                <>
                  {activeCases.length > 0 && (
                    <div className="space-y-2 border-t border-border bg-muted/40 px-5 py-4">
                      <Eyebrow as="p">Agent activity</Eyebrow>
                      {activeCases.map((c) => (
                        <Link
                          key={c.id}
                          to={`/agent/cases/${c.id}`}
                          className="flex items-center gap-3 text-xs transition-opacity hover:opacity-80"
                        >
                          <span className="w-28 shrink-0 truncate font-mono text-foreground">
                            {c.invoice_no ?? c.case_key ?? c.case_id}
                          </span>
                          <AgentPhaseRail state={c.state} target={c.target} compact tone="bold" />
                        </Link>
                      ))}
                    </div>
                  )}
                  <Divider />
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border text-left">
                        <th className="px-5 py-3 font-mono text-xs font-medium uppercase tracking-wide text-muted-foreground">Invoice #</th>
                        <th className="px-5 py-3 font-mono text-xs font-medium uppercase tracking-wide text-muted-foreground">Status</th>
                        <th className="px-5 py-3 font-mono text-xs font-medium uppercase tracking-wide text-muted-foreground">Due</th>
                        <th className="px-5 py-3 text-right font-mono text-xs font-medium uppercase tracking-wide text-muted-foreground">Open Amount</th>
                        <th className="px-5 py-3"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.invoices.map((inv) => (
                        <tr key={inv.invoice_id} className="group border-b border-border last:border-0 transition-colors duration-150 ease-bold hover:bg-muted/60">
                          <td className="px-5 py-3">
                            <Link
                              to={`/invoices/${inv.invoice_id}`}
                              className="font-mono font-medium text-foreground underline decoration-border decoration-1 underline-offset-4 hover:decoration-accent"
                            >
                              {inv.invoice_no ?? inv.case_key ?? "--"}
                            </Link>
                          </td>
                          <td className="px-5 py-3">
                            <StatusBadge label={inv.status} />
                          </td>
                          <td className="px-5 py-3 font-mono text-muted-foreground">{inv.due_date ?? "--"}</td>
                          <td className="px-5 py-3 text-right font-mono font-medium tabular-nums text-foreground">
                            {money(inv.open_amount)}
                          </td>
                          <td className="px-5 py-3 text-right">
                            <button
                              onClick={() => askAboutInvoice(inv)}
                              className="font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground opacity-0 transition-all duration-150 ease-bold hover:text-accent group-hover:opacity-100 focus-visible:opacity-100"
                            >
                              Ask about this
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
