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
  email: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  teams: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  dry_run: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300",
  email_failed: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  blocked: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
};

export default function Outbox() {
  const { token } = useSession();
  const [entries, setEntries] = useState<OutboxEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getOutbox(token).then(setEntries).catch((e) => setError(String(e)));
  }, [token]);

  if (error) return <p className="px-6 py-8 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>;
  if (!entries) return <p className="px-6 py-8 text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>;

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="mb-1 font-display text-[20px] font-semibold text-zinc-900 dark:text-zinc-100">Outbox</h1>
      <p className="mb-6 text-[13px] text-zinc-400 dark:text-zinc-500">Every message the agent has generated, newest first.</p>

      {entries.length === 0 && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Nothing sent yet.</p>}

      <div className="space-y-2">
        {entries.map((e) => (
          <div key={e.id} className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[13px]">
                <Link to={`/invoices/${e.case_id}`} className="font-medium text-zinc-900 dark:text-zinc-100 hover:underline">
                  {e.invoice_no ?? e.case_key ?? e.case_id}
                </Link>
                <span className="text-zinc-300 dark:text-zinc-600">·</span>
                <span className="text-zinc-500 dark:text-zinc-400">{e.project_number ?? "No project"}</span>
                {e.dry_run && (
                  <span className="rounded-full bg-sky-50 dark:bg-sky-950/40 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:text-sky-300">dry run</span>
                )}
              </div>
              <span className="text-[11.5px] text-zinc-400 dark:text-zinc-500">{formatWhen(e.at)}</span>
            </div>

            <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[12px]">
              <span className={`rounded-full px-2 py-0.5 font-medium ${CHANNEL_STYLES[e.channel ?? ""] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"}`}>
                {e.channel ?? "unknown"}
              </span>
              <span className="text-zinc-400 dark:text-zinc-500">
                → {e.target === "pm" ? "PM" : e.target === "customer" ? "Customer" : e.target ?? "?"}
                {e.recipient ? ` (${e.recipient})` : ""}
              </span>
              {e.composed && (
                <span className="rounded-full bg-violet-50 dark:bg-violet-950/40 px-2 py-0.5 text-[11px] font-medium text-violet-700 dark:text-violet-300">AI-composed</span>
              )}
              {e.requires_human_review && (
                <span className="rounded-full bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">needs review</span>
              )}
              {e.policy_blocked && (
                <span className="rounded-full bg-rose-50 dark:bg-rose-950/40 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:text-rose-300">blocked by policy</span>
              )}
            </div>

            {e.subject && <p className="mb-0.5 text-[12.5px] font-medium text-zinc-700 dark:text-zinc-300">{e.subject}</p>}
            {e.body && <p className="text-[13px] text-zinc-600 dark:text-zinc-300">{e.body}</p>}
            {e.policy_reason && <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">{e.policy_reason}</p>}
            {e.evaluation_failures.length > 0 && (
              <p className="mt-1 text-[12px] text-amber-700 dark:text-amber-300">Evaluation flags: {e.evaluation_failures.join(", ")}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
