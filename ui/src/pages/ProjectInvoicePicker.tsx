// Reached via a magic link when someone asks to snooze/comment in a Teams
// project chat without naming a specific invoice (added 2026-07-16). Shows
// only the invoices pooled into that chat's project; picking one continues
// to that invoice's snooze/comment form.
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { listProjectInvoices, type Invoice } from "../api";
import { useSession } from "../context/SessionContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Eyebrow } from "../components/ui/Eyebrow";

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
    <div className="mx-auto max-w-4xl px-6 py-10 sm:px-12">
      <PageHeader
        eyebrow="Pick an invoice"
        title={`Which invoice would you like to ${action}?`}
        description={`Showing invoices for ${projectNumber} -- the same project pooled into this Teams chat.`}
      />

      {error && (
        <p className="border border-accent px-4 py-3 text-sm text-accent" role="alert">
          {error}
        </p>
      )}
      {!error && invoices === null && <p className="text-sm text-muted-foreground">Loading…</p>}
      {invoices !== null && invoices.length === 0 && (
        <div className="border border-border px-5 py-16 text-center">
          <p className="font-display text-2xl font-semibold tracking-tight text-foreground">No invoices found for this project.</p>
        </div>
      )}

      <div className="space-y-2">
        {invoices?.map((inv) => (
          <button
            key={inv.invoice_id}
            onClick={() => navigate(`/invoices/${inv.invoice_id}?action=${action}`)}
            className="flex w-full items-center justify-between border border-border px-5 py-4 text-left transition-colors duration-150 ease-bold hover:border-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <div className="min-w-0">
              <p className="truncate font-mono text-sm font-medium text-foreground">{inv.case_key}</p>
              <div className="mt-1.5">
                <Eyebrow>
                  {inv.stage ?? "unknown stage"} &middot; {inv.aging_status ?? "unknown aging"}
                </Eyebrow>
              </div>
            </div>
            <div className="shrink-0 pl-4 font-mono text-sm font-medium tabular-nums text-foreground">{money(inv.open_amount)}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
