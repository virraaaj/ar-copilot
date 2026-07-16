import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { listInvoices, getAgingSummary, type Invoice, type AgingSummary } from "../api";
import { useSession } from "../context/SessionContext";

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-emerald-50 text-emerald-700",
  closed_paid: "bg-zinc-100 text-zinc-500",
  closed_other: "bg-zinc-100 text-zinc-500",
  snoozed: "bg-amber-50 text-amber-700",
};

const STAGE_STYLES: Record<string, string> = {
  escalation: "bg-orange-50 text-orange-700",
  final_notice: "bg-rose-50 text-rose-700",
  reminder: "bg-sky-50 text-sky-700",
};

function Pill({ label, styleMap }: { label: string | null; styleMap: Record<string, string> }) {
  if (!label) return <span className="text-zinc-300">--</span>;
  const cls = styleMap[label] ?? "bg-zinc-100 text-zinc-600";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[12px] font-medium capitalize ${cls}`}>
      {label.replace(/_/g, " ")}
    </span>
  );
}

export default function Dashboard() {
  const { token, setPinnedInvoice } = useSession();
  const navigate = useNavigate();
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [summary, setSummary] = useState<AgingSummary | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [stageFilter, setStageFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getAgingSummary(token).then(setSummary).catch((e) => setError(String(e)));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    listInvoices(token, {
      status: statusFilter || undefined,
      stage: stageFilter || undefined,
    })
      .then(setInvoices)
      .catch((e) => setError(String(e)));
  }, [token, statusFilter, stageFilter]);

  function askAboutInvoice(inv: Invoice) {
    const label = `${inv.project_name ?? inv.project_number ?? "Unknown project"} -- ${money(inv.open_amount)}, ${
      inv.aging_status ?? "unknown aging"
    }`;
    setPinnedInvoice({ invoice_id: inv.invoice_id, label });
    navigate("/chat");
  }

  const selectClass =
    "rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[13px] text-zinc-600 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5";

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">Aging Overview</h1>
        <p className="mt-1 text-[14px] text-zinc-400">A live snapshot of open invoices across every project.</p>
      </div>

      {error && <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>}

      {summary && (
        <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">Total Open</p>
            <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900">
              {money(summary.total_open_amount)}
            </p>
          </div>
          <div className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">Invoices</p>
            <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900">
              {summary.total_invoices}
            </p>
          </div>
          {Object.entries(summary.by_stage)
            .slice(0, 2)
            .map(([stage, s]) => (
              <div
                key={stage}
                className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
              >
                <p className="truncate text-[12px] font-medium uppercase tracking-wide text-zinc-400">
                  {stage.replace(/_/g, " ")}
                </p>
                <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900">
                  {s.count} <span className="text-[14px] font-normal text-zinc-400">· {money(s.open_amount)}</span>
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
        <select value={stageFilter} onChange={(e) => setStageFilter(e.target.value)} className={selectClass}>
          <option value="">All stages</option>
          <option value="reminder">Reminder</option>
          <option value="first_notice">First Notice</option>
          <option value="second_notice">Second Notice</option>
          <option value="escalation">Escalation</option>
          <option value="final_notice">Final Notice</option>
        </select>
      </div>

      <div className="overflow-hidden rounded-2xl border border-zinc-200/70 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-zinc-100 text-left text-[12px] font-medium uppercase tracking-wide text-zinc-400">
              <th className="px-5 py-3 font-medium">Project</th>
              <th className="px-5 py-3 font-medium">Stage</th>
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium">Due</th>
              <th className="px-5 py-3 text-right font-medium">Open Amount</th>
              <th className="px-5 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((inv) => (
              <tr key={inv.invoice_id} className="group border-b border-zinc-50 last:border-0 hover:bg-zinc-50/70">
                <td className="px-5 py-3">
                  <Link to={`/invoices/${inv.invoice_id}`} className="font-medium text-zinc-800 hover:text-zinc-950">
                    {inv.project_name ?? inv.project_number ?? "--"}
                  </Link>
                </td>
                <td className="px-5 py-3">
                  <Pill label={inv.stage} styleMap={STAGE_STYLES} />
                </td>
                <td className="px-5 py-3">
                  <Pill label={inv.status} styleMap={STATUS_STYLES} />
                </td>
                <td className="px-5 py-3 text-zinc-500">{inv.due_date ?? "--"}</td>
                <td className="px-5 py-3 text-right font-medium tabular-nums text-zinc-800">
                  {money(inv.open_amount)}
                </td>
                <td className="px-5 py-3 text-right">
                  <button
                    onClick={() => askAboutInvoice(inv)}
                    className="rounded-full px-3 py-1 text-[12px] font-medium text-zinc-400 opacity-0 transition-all hover:bg-zinc-900 hover:text-white group-hover:opacity-100"
                  >
                    Ask about this
                  </button>
                </td>
              </tr>
            ))}
            {invoices.length === 0 && (
              <tr>
                <td colSpan={6} className="px-5 py-12 text-center text-[13px] text-zinc-400">
                  No invoices match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
