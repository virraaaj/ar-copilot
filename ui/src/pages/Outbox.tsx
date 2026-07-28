// Outbox screen (spec §6.18, added 2026-07-28) -- every message the chase
// agent has generated (real send or dry-run preview), newest first, with
// policy/evaluation status per row. Pure read view over GET /api/outbox.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import { getOutbox, type OutboxEntry } from "../api";

function formatWhen(iso: string): string {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const CHANNEL_STYLES: Record<string, string> = {
  email: "bg-emerald-50 text-emerald-700",
  teams: "bg-emerald-50 text-emerald-700",
  dry_run: "bg-sky-50 text-sky-700",
  email_failed: "bg-rose-50 text-rose-700",
  blocked: "bg-amber-50 text-amber-700",
};

export default function Outbox() {
  const { token } = useSession();
  const [entries, setEntries] = useState<OutboxEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getOutbox(token).then(setEntries).catch((e) => setError(String(e)));
  }, [token]);

  if (error) return <p className="px-6 py-8 text-[13px] text-rose-600">{error}</p>;
  if (!entries) return <p className="px-6 py-8 text-[13px] text-zinc-400">Loading...</p>;

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="mb-1 font-display text-[20px] font-semibold text-zinc-900">Outbox</h1>
      <p className="mb-6 text-[13px] text-zinc-400">Every message the agent has generated, newest first.</p>

      {entries.length === 0 && <p className="text-[13px] text-zinc-400">Nothing sent yet.</p>}

      <div className="space-y-2">
        {entries.map((e) => (
          <div key={e.id} className="rounded-2xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[13px]">
                <Link to={`/invoices/${e.case_id}`} className="font-medium text-zinc-900 hover:underline">
                  {e.invoice_no ?? e.case_key ?? e.case_id}
                </Link>
                <span className="text-zinc-300">·</span>
                <span className="text-zinc-500">{e.project_number ?? "No project"}</span>
                {e.dry_run && (
                  <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700">dry run</span>
                )}
              </div>
              <span className="text-[11.5px] text-zinc-400">{formatWhen(e.at)}</span>
            </div>

            <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[12px]">
              <span className={`rounded-full px-2 py-0.5 font-medium ${CHANNEL_STYLES[e.channel ?? ""] ?? "bg-zinc-100 text-zinc-500"}`}>
                {e.channel ?? "unknown"}
              </span>
              <span className="text-zinc-400">
                → {e.target === "pm" ? "PM" : e.target === "customer" ? "Customer" : e.target ?? "?"}
                {e.recipient ? ` (${e.recipient})` : ""}
              </span>
              {e.composed && (
                <span className="rounded-full bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">AI-composed</span>
              )}
              {e.requires_human_review && (
                <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">needs review</span>
              )}
              {e.policy_blocked && (
                <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">blocked by policy</span>
              )}
            </div>

            {e.subject && <p className="mb-0.5 text-[12.5px] font-medium text-zinc-700">{e.subject}</p>}
            {e.body && <p className="text-[13px] text-zinc-600">{e.body}</p>}
            {e.policy_reason && <p className="mt-1 text-[12px] text-rose-600">{e.policy_reason}</p>}
            {e.evaluation_failures.length > 0 && (
              <p className="mt-1 text-[12px] text-amber-700">Evaluation flags: {e.evaluation_failures.join(", ")}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
