// AR Health dashboard tab (added 2026-07-17): visual version of the
// weekly Teams digest card -- AR health + customer payment-pattern
// projections for a selected project, on demand instead of once a week.
// See app/services/digest_engine.py for the underlying computation this
// mirrors (same numbers, same projection methodology and its stated
// limitations).
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  listProjectContacts,
  getProjectDigest,
  type ProjectContactGroup,
  type ProjectDigest,
  type CustomerProjection,
} from "../api";
import { useSession } from "../context/SessionContext";

function money(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

const STAGE_COLORS: Record<string, string> = {
  reminder: "bg-sky-400",
  first_notice: "bg-sky-500",
  second_notice: "bg-amber-400",
  escalation: "bg-orange-500",
  final_notice: "bg-rose-500",
};

const RISK_STYLES: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700",
  medium: "bg-amber-50 text-amber-700",
  high: "bg-rose-50 text-rose-700",
  unknown: "bg-zinc-100 text-zinc-500",
};

function StageBar({ byStage }: { byStage: Record<string, number> }) {
  const total = Object.values(byStage).reduce((a, b) => a + b, 0);
  if (total === 0) return <p className="text-[13px] text-zinc-400">No active invoices.</p>;

  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-zinc-100">
        {Object.entries(byStage).map(([stage, count]) => (
          <div
            key={stage}
            className={STAGE_COLORS[stage] ?? "bg-zinc-400"}
            style={{ width: `${(count / total) * 100}%` }}
            title={`${stage}: ${count}`}
          />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {Object.entries(byStage).map(([stage, count]) => (
          <div key={stage} className="flex items-center gap-1.5 text-[12px] text-zinc-500">
            <span className={`h-2 w-2 rounded-full ${STAGE_COLORS[stage] ?? "bg-zinc-400"}`} />
            {stage.replace(/_/g, " ")} ({count})
          </div>
        ))}
      </div>
    </div>
  );
}

function projectionLabel(p: CustomerProjection): string {
  if (p.sample_size === 0 || p.avg_days_relative_to_due === null) return "Not enough closed-invoice history yet.";
  const avg = p.avg_days_relative_to_due;
  if (avg > 0) return `Typically pays ${avg.toFixed(0)} day${avg === 1 ? "" : "s"} late`;
  if (avg < 0) return `Typically pays ${Math.abs(avg).toFixed(0)} day${avg === -1 ? "" : "s"} early`;
  return "Typically pays on time";
}

export default function ARHealth() {
  const { token, setPinnedInvoice } = useSession();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectContactGroup[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [digest, setDigest] = useState<ProjectDigest | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listProjectContacts(token)
      .then((groups) => {
        setProjects(groups);
        if (groups.length > 0) setSelected(groups[0].project_number);
      })
      .catch((e) => setError(String(e)));
  }, [token]);

  useEffect(() => {
    if (!token || !selected) return;
    setDigest(null);
    getProjectDigest(token, selected)
      .then(setDigest)
      .catch((e) => setError(String(e)));
  }, [token, selected]);

  function askAboutThis() {
    if (!digest?.found) return;
    // No hidden ID needed here (unlike the invoice pin) -- project_number
    // is already a human-visible identifier shown throughout this app, so
    // the agent can resolve it itself via get_project_digest.
    setPinnedInvoice(null);
    navigate("/chat", {
      state: { initialQuestion: `Tell me about the AR health and payment projections for ${digest.project_name} (${digest.project_number}).` },
    });
  }

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
      </div>
    );
  }
  if (!projects) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-[13px] text-zinc-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">AR Health</h1>
          <p className="mt-1 text-[14px] text-zinc-400">
            Open exposure, overdue risk, and payment-pattern projections by project.
          </p>
        </div>
        {digest?.found && (
          <button
            onClick={askAboutThis}
            className="shrink-0 rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800"
          >
            Ask about this
          </button>
        )}
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {projects.map((p) => (
          <button
            key={p.project_number}
            onClick={() => setSelected(p.project_number)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors ${
              selected === p.project_number ? "bg-zinc-900 text-white" : "border border-zinc-200 text-zinc-600 hover:bg-zinc-50"
            }`}
          >
            {p.project_name ?? p.project_number}
          </button>
        ))}
      </div>

      {!digest && <p className="text-[13px] text-zinc-400">Loading digest...</p>}

      {digest && !digest.found && (
        <p className="text-[13px] text-zinc-400">No invoices found for this project.</p>
      )}

      {digest?.found && digest.health && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
              <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">Open invoices</p>
              <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900">
                {digest.health.open_invoice_count}
              </p>
              <p className="mt-0.5 text-[12px] text-zinc-400">{money(digest.health.total_open_amount)} total</p>
            </div>
            <div className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
              <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">Overdue</p>
              <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-rose-600">
                {digest.health.overdue_count}
              </p>
              <p className="mt-0.5 text-[12px] text-zinc-400">{money(digest.health.overdue_amount)} at risk</p>
            </div>
            <div className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
              <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">Customers tracked</p>
              <p className="mt-1 font-display text-[22px] font-semibold tabular-nums text-zinc-900">
                {digest.customer_projections?.length ?? 0}
              </p>
              <p className="mt-0.5 text-[12px] text-zinc-400">with payment-pattern history</p>
            </div>
          </div>

          <div className="mb-6 rounded-2xl border border-zinc-200/70 bg-white p-6 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <h2 className="mb-4 font-display text-[15px] font-semibold tracking-tight text-zinc-900">Stage mix</h2>
            <StageBar byStage={digest.health.by_stage} />
          </div>

          <div className="rounded-2xl border border-zinc-200/70 bg-white p-6 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <h2 className="mb-1 font-display text-[15px] font-semibold tracking-tight text-zinc-900">
              Customer payment patterns
            </h2>
            <p className="mb-4 text-[12px] text-zinc-400">
              Estimated from each customer's own closed-invoice history -- treat a low sample size as low confidence.
            </p>
            {(!digest.customer_projections || digest.customer_projections.length === 0) && (
              <p className="text-[13px] text-zinc-400">No customers with open invoices right now.</p>
            )}
            <div className="space-y-3">
              {digest.customer_projections?.map((p) => (
                <div key={p.customer_id} className="flex items-center justify-between gap-3 rounded-xl border border-zinc-100 px-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-[13px] font-medium text-zinc-800">{p.customer_id}</p>
                    <p className="text-[12px] text-zinc-400">
                      {projectionLabel(p)}
                      {p.sample_size > 0 ? ` (${p.sample_size} past invoice${p.sample_size === 1 ? "" : "s"})` : ""}
                    </p>
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium capitalize ${RISK_STYLES[p.risk]}`}>
                    {p.risk} risk
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
