import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { listInvoices, getAgingSummary, type Invoice, type AgingSummary } from "../api";
import { useSession } from "../context/SessionContext";

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
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
    // The click-to-chat handoff (PLAN.md §5 Phase 2): the user only ever
    // sees this human-readable label. The raw invoice_id rides along
    // silently once Chat sends its next message.
    const label = `${inv.project_name ?? inv.project_number ?? "Unknown project"} -- ${money(inv.open_amount)}, ${
      inv.aging_status ?? "unknown aging"
    }`;
    setPinnedInvoice({ invoice_id: inv.invoice_id, label });
    navigate("/chat");
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <h1 className="mb-4 text-xl font-semibold text-slate-800">AR Aging Overview</h1>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {summary && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <p className="text-xs text-slate-500">Total Open</p>
            <p className="text-lg font-semibold text-slate-800">{money(summary.total_open_amount)}</p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-3">
            <p className="text-xs text-slate-500">Invoices</p>
            <p className="text-lg font-semibold text-slate-800">{summary.total_invoices}</p>
          </div>
          {Object.entries(summary.by_stage)
            .slice(0, 2)
            .map(([stage, s]) => (
              <div key={stage} className="rounded-lg border border-slate-200 bg-white p-3">
                <p className="text-xs text-slate-500">{stage}</p>
                <p className="text-lg font-semibold text-slate-800">
                  {s.count} · {money(s.open_amount)}
                </p>
              </div>
            ))}
        </div>
      )}

      <div className="mb-3 flex gap-2">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="closed_paid">Closed (paid)</option>
          <option value="closed_other">Closed (other)</option>
        </select>
        <select
          value={stageFilter}
          onChange={(e) => setStageFilter(e.target.value)}
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">All stages</option>
          <option value="reminder">Reminder</option>
          <option value="first_notice">First Notice</option>
          <option value="second_notice">Second Notice</option>
          <option value="escalation">Escalation</option>
          <option value="final_notice">Final Notice</option>
        </select>
      </div>

      <table className="w-full border-collapse overflow-hidden rounded-lg border border-slate-200 bg-white text-sm">
        <thead className="bg-slate-50 text-left text-slate-500">
          <tr>
            <th className="px-3 py-2">Project</th>
            <th className="px-3 py-2">Stage</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Due</th>
            <th className="px-3 py-2 text-right">Open Amount</th>
            <th className="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((inv) => (
            <tr key={inv.invoice_id} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="px-3 py-2">
                <Link to={`/invoices/${inv.invoice_id}`} className="text-slate-800 hover:underline">
                  {inv.project_name ?? inv.project_number ?? "--"}
                </Link>
              </td>
              <td className="px-3 py-2 text-slate-600">{inv.stage ?? "--"}</td>
              <td className="px-3 py-2 text-slate-600">{inv.status ?? "--"}</td>
              <td className="px-3 py-2 text-slate-600">{inv.due_date ?? "--"}</td>
              <td className="px-3 py-2 text-right text-slate-800">{money(inv.open_amount)}</td>
              <td className="px-3 py-2 text-right">
                <button
                  onClick={() => askAboutInvoice(inv)}
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
                >
                  Ask about this
                </button>
              </td>
            </tr>
          ))}
          {invoices.length === 0 && (
            <tr>
              <td colSpan={6} className="px-3 py-6 text-center text-slate-400">
                No invoices match these filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
