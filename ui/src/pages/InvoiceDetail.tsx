import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { getInvoice, type Invoice } from "../api";
import { useSession } from "../context/SessionContext";

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export default function InvoiceDetail() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const { token, setPinnedInvoice } = useSession();
  const navigate = useNavigate();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !invoiceId) return;
    getInvoice(token, invoiceId).then(setInvoice).catch((e) => setError(String(e)));
  }, [token, invoiceId]);

  function askAboutThis() {
    if (!invoice) return;
    const label = `${invoice.project_name ?? invoice.project_number ?? "Unknown project"} -- ${money(
      invoice.open_amount
    )}, ${invoice.aging_status ?? "unknown aging"}`;
    setPinnedInvoice({ invoice_id: invoice.invoice_id, label });
    navigate("/chat");
  }

  if (error) return <p className="p-6 text-sm text-red-600">{error}</p>;
  if (!invoice) return <p className="p-6 text-sm text-slate-400">Loading...</p>;

  const fields: [string, string | number | null][] = [
    ["Case key", invoice.case_key],
    ["Business unit", invoice.business_unit_id],
    ["Stage", invoice.stage],
    ["Status", invoice.status],
    ["Due date", invoice.due_date],
    ["Aging status", invoice.aging_status],
    ["Open amount", money(invoice.open_amount)],
  ];

  return (
    <div className="mx-auto max-w-2xl p-6">
      <Link to="/" className="text-sm text-slate-500 hover:underline">
        &larr; Back to dashboard
      </Link>
      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-5">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-lg font-semibold text-slate-800">
            {invoice.project_name ?? invoice.project_number ?? "Invoice"}
          </h1>
          <button
            onClick={askAboutThis}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            Ask about this
          </button>
        </div>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          {fields.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-slate-500">{label}</dt>
              <dd className="text-slate-800">{value ?? "--"}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
