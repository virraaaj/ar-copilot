import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import {
  listInvoices, getAgingSummary, uploadAgingExcel, listAgentCases,
  type Invoice, type AgingSummary, type AgentCase,
} from "../api";
import { useSession } from "../context/SessionContext";
import { AgentPhaseRail } from "../components/AgentPhaseRail";

const OPEN_AGENT_STATES = new Set([
  "not_due", "due", "overdue", "outreach_ready", "waiting_for_customer", "customer_responded",
  "blocked", "follow_up_scheduled", "promise_to_pay", "promise_missed", "escalation_required",
]);

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  closed_paid: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
  closed_other: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
  snoozed: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
};

function Pill({ label, styleMap }: { label: string | null; styleMap: Record<string, string> }) {
  if (!label) return <span className="text-zinc-300 dark:text-zinc-600">--</span>;
  const cls = styleMap[label] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[12px] font-medium capitalize ${cls}`}>
      {label.replace(/_/g, " ")}
    </span>
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

  const selectClass =
    "rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-[13px] text-zinc-600 dark:text-zinc-300 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10";

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Aging Overview</h1>
          <p className="mt-1 text-[14px] text-zinc-400 dark:text-zinc-500">A live snapshot of open invoices across every project.</p>
        </div>
        <div className="shrink-0 text-right">
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
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="rounded-full bg-green-700 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? "Uploading..." : "Upload aging Excel"}
          </button>
        </div>
      </div>

      {error && <p className="mb-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
      {uploadMessage && (
        <p className="mb-4 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 px-3 py-2 text-[13px] text-emerald-700 dark:text-emerald-300">{uploadMessage}</p>
      )}
      {uploadError && <p className="mb-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{uploadError}</p>}

      {summary && (
        <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">Total Open</p>
            <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
              {money(summary.total_open_amount)}
            </p>
          </div>
          <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">Invoices</p>
            <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
              {summary.total_invoices}
            </p>
          </div>
          {Object.entries(summary.by_stage)
            .slice(0, 2)
            .map(([stage, s]) => (
              <div
                key={stage}
                className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
              >
                <p className="truncate text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                  {stage.replace(/_/g, " ")}
                </p>
                <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                  {s.count} <span className="text-[14px] font-normal text-zinc-400 dark:text-zinc-500">· {money(s.open_amount)}</span>
                </p>
              </div>
            ))}
        </div>
      )}

      <div className="mb-3 flex gap-2">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={selectClass}>
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="closed_paid">Closed (paid)</option>
          <option value="closed_other">Closed (other)</option>
        </select>
      </div>

      {invoices.length === 0 && (
        <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-5 py-12 text-center text-[13px] text-zinc-400 dark:text-zinc-500 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          No invoices match these filters.
        </div>
      )}

      <div className="space-y-4">
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
            <div
              key={key}
              className="overflow-hidden rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
            >
              <button
                onClick={() => toggleExpanded(key)}
                className="flex w-full items-center gap-3 border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50/60 dark:bg-zinc-800/40 px-5 py-3.5 text-left transition-colors hover:bg-zinc-100/60 dark:hover:bg-zinc-800"
              >
                <span
                  className={`shrink-0 text-zinc-400 dark:text-zinc-500 transition-transform ${isOpen ? "rotate-90" : ""}`}
                  aria-hidden
                >
                  &#9656;
                </span>
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-900 dark:bg-zinc-700 text-[12px] font-semibold text-white">
                  {(group.project_name ?? group.project_number ?? "?").slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[14px] font-medium text-zinc-900 dark:text-zinc-100">
                    {group.project_name ?? group.project_number ?? "Unknown project"}
                  </p>
                  {group.project_number && <p className="text-[12px] text-zinc-400 dark:text-zinc-500">{group.project_number}</p>}
                </div>
                <span className="ml-auto flex shrink-0 items-center gap-2">
                  {projectCases.length > 0 && (
                    <span
                      className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
                        escalatedCount > 0
                          ? "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300"
                          : activeCases.length > 0
                            ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300"
                            : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                      }`}
                    >
                      {activeCases.length} active{escalatedCount > 0 ? ` · ${escalatedCount} escalated` : ""}
                    </span>
                  )}
                  {group.invoices.length > 0 && (
                    <span
                      title="Invoices with a tracked payment date"
                      className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
                        trackedCount > 0
                          ? "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300"
                          : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                      }`}
                    >
                      {trackedCount}/{group.invoices.length} date tracked
                    </span>
                  )}
                  <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2.5 py-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                    {group.invoices.length} invoice{group.invoices.length === 1 ? "" : "s"}
                  </span>
                </span>
              </button>

              {isOpen && (
                <>
                  {activeCases.length > 0 && (
                    <div className="space-y-2 border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50/40 dark:bg-zinc-800/40 px-5 py-3.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">Agent activity</p>
                      {activeCases.map((c) => (
                        <Link
                          key={c.id}
                          to={`/agent/cases/${c.id}`}
                          className="flex items-center gap-3 text-[12px] hover:opacity-80"
                        >
                          <span className="w-28 shrink-0 truncate text-zinc-600 dark:text-zinc-300">
                            {c.invoice_no ?? c.case_key ?? c.case_id}
                          </span>
                          <AgentPhaseRail state={c.state} target={c.target} compact />
                        </Link>
                      ))}
                    </div>
                  )}
                  <table className="w-full border-collapse text-[13px]">
                    <thead>
                      <tr className="border-b border-zinc-100 dark:border-zinc-800 text-left text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                        <th className="px-5 py-2.5 font-medium">Invoice #</th>
                        <th className="px-5 py-2.5 font-medium">Status</th>
                        <th className="px-5 py-2.5 font-medium">Due</th>
                        <th className="px-5 py-2.5 text-right font-medium">Open Amount</th>
                        <th className="px-5 py-2.5"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.invoices.map((inv) => (
                        <tr key={inv.invoice_id} className="group border-b border-zinc-50 dark:border-zinc-800 last:border-0 hover:bg-zinc-50/70 dark:hover:bg-zinc-800/60">
                          <td className="px-5 py-3">
                            <Link to={`/invoices/${inv.invoice_id}`} className="font-medium text-zinc-800 dark:text-zinc-200 hover:text-zinc-950 dark:hover:text-zinc-50">
                              {inv.invoice_no ?? inv.case_key ?? "--"}
                            </Link>
                          </td>
                          <td className="px-5 py-3">
                            <Pill label={inv.status} styleMap={STATUS_STYLES} />
                          </td>
                          <td className="px-5 py-3 text-zinc-500 dark:text-zinc-400">{inv.due_date ?? "--"}</td>
                          <td className="px-5 py-3 text-right font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                            {money(inv.open_amount)}
                          </td>
                          <td className="px-5 py-3 text-right">
                            <button
                              onClick={() => askAboutInvoice(inv)}
                              className="rounded-full px-3 py-1 text-[12px] font-medium text-zinc-400 dark:text-zinc-500 opacity-0 transition-all hover:bg-green-700 hover:text-white group-hover:opacity-100"
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
