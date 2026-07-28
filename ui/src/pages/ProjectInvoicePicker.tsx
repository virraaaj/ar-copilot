// Reached via a magic link when someone asks to snooze/comment in a Teams
// project chat without naming a specific invoice (added 2026-07-16). Shows
// only the invoices pooled into that chat's project; picking one continues
// to that invoice's snooze/comment form.
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { listProjectInvoices, type Invoice } from "../api";
import { useSession } from "../context/SessionContext";

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export default function ProjectInvoicePicker() {
  const { projectNumber } = useParams<{ projectNumber: string }>();
  const [searchParams] = useSearchParams();
  const action = searchParams.get("action") === "snooze" ? "snooze" : "comment";
  const { token } = useSession();
  const navigate = useNavigate();
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !projectNumber) return;
    listProjectInvoices(token, projectNumber)
      .then(setInvoices)
      .catch((e) => setError(String(e)));
  }, [token, projectNumber]);

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="font-display text-[20px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
        Which invoice would you like to {action}?
      </h1>
      <p className="mt-1 text-[13px] text-zinc-400 dark:text-zinc-500">
        Showing invoices for {projectNumber} -- the same project pooled into this Teams chat.
      </p>

      {error && <p className="mt-6 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
      {!error && invoices === null && <p className="mt-6 text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>}
      {invoices !== null && invoices.length === 0 && (
        <p className="mt-6 text-[13px] text-zinc-400 dark:text-zinc-500">No invoices found for this project.</p>
      )}

      <div className="mt-6 space-y-2">
        {invoices?.map((inv) => (
          <button
            key={inv.invoice_id}
            onClick={() => navigate(`/invoices/${inv.invoice_id}?action=${action}`)}
            className="flex w-full items-center justify-between rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-5 py-4 text-left shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-colors hover:border-zinc-300 dark:hover:border-zinc-600"
          >
            <div>
              <div className="text-[14px] font-medium text-zinc-900 dark:text-zinc-100">{inv.case_key}</div>
              <div className="mt-0.5 text-[12px] text-zinc-400 dark:text-zinc-500">
                {inv.stage ?? "unknown stage"} &middot; {inv.aging_status ?? "unknown aging"}
              </div>
            </div>
            <div className="text-[14px] font-medium text-zinc-800 dark:text-zinc-200">{money(inv.open_amount)}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
