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

  if (error)
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
      </div>
    );
  if (!invoice)
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="text-[13px] text-zinc-400">Loading...</p>
      </div>
    );

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
    <div className="mx-auto max-w-2xl px-6 py-10">
      <Link to="/" className="text-[13px] font-medium text-zinc-400 transition-colors hover:text-zinc-900">
        &larr; Back to dashboard
      </Link>

      <div className="mt-4 rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-[20px] font-semibold tracking-tight text-zinc-900">
            {invoice.project_name ?? invoice.project_number ?? "Invoice"}
          </h1>
          <button
            onClick={askAboutThis}
            className="rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800"
          >
            Ask about this
          </button>
        </div>
        <dl className="grid grid-cols-2 gap-y-4 text-[14px]">
          {fields.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">{label}</dt>
              <dd className="text-right font-medium text-zinc-800">{value ?? "--"}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
