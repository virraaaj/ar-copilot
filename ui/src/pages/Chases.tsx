// Chases tab (PLAN_AGENTIC_CHASE.md Phase C4, added 2026-07-20): the
// agentic chase engine's control surface -- every invoice it's actively
// pursuing (or has escalated), the full event history, and the human
// actions available (pause/resume/close/restart/edit the tracked
// commitment). All the actual chase-progression logic lives in
// app/services/chase_engine.py/chase_machine.py; this is purely a
// window onto ChaseStore plus a few write actions.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  listChases,
  listChaseEvents,
  pauseChase,
  resumeChase,
  closeChase,
  restartChase,
  editChaseCommitment,
  type Chase,
  type ChaseEvent,
} from "../api";
import { useSession } from "../context/SessionContext";

const STATE_STYLES: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-600",
  awaiting_pm: "bg-sky-50 text-sky-700",
  awaiting_customer: "bg-sky-50 text-sky-700",
  commitment_tracked: "bg-emerald-50 text-emerald-700",
  verifying_payment: "bg-amber-50 text-amber-700",
  escalated: "bg-rose-50 text-rose-700",
  paused: "bg-zinc-100 text-zinc-500",
  closed_paid: "bg-emerald-50 text-emerald-700",
  closed_manual: "bg-zinc-100 text-zinc-500",
};

const STATE_LABELS: Record<string, string> = {
  pending: "Starting",
  awaiting_pm: "Waiting on PM",
  awaiting_customer: "Waiting on customer",
  commitment_tracked: "Payment date tracked",
  verifying_payment: "Verifying payment",
  escalated: "Escalated",
  paused: "Paused",
  closed_paid: "Paid",
  closed_manual: "Closed",
};

const OPEN_STATES = new Set(["pending", "awaiting_pm", "awaiting_customer", "commitment_tracked", "verifying_payment"]);

function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function ChaseDetail({ chase, onChanged }: { chase: Chase; onChanged: () => void }) {
  const { token } = useSession();
  const [events, setEvents] = useState<ChaseEvent[] | null>(null);
  const [closeReason, setCloseReason] = useState("");
  const [showClose, setShowClose] = useState(false);
  const [newDate, setNewDate] = useState(chase.promised_date ?? "");
  const [showEditDate, setShowEditDate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setEvents(null);
    listChaseEvents(token, chase.id).then(setEvents).catch((e) => setError(String(e)));
  }, [token, chase.id]);

  async function run(action: () => Promise<void>) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-2xl border border-zinc-200/70 bg-white p-6 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="font-display text-[16px] font-semibold text-zinc-900">
            {chase.invoice_no ?? chase.case_key ?? chase.case_id}
          </p>
          <p className="mt-0.5 text-[12px] text-zinc-400">{chase.project_number ?? "No project"}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${STATE_STYLES[chase.state] ?? "bg-zinc-100 text-zinc-600"}`}>
          {stateLabel(chase.state)}
        </span>
      </div>

      <dl className="mb-4 grid grid-cols-2 gap-3 text-[12px]">
        <div>
          <dt className="text-zinc-400">Target</dt>
          <dd className="text-zinc-700">{chase.target === "pm" ? "PM" : chase.target === "customer" ? "Customer" : "--"}</dd>
        </div>
        <div>
          <dt className="text-zinc-400">Promised date</dt>
          <dd className="text-zinc-700">{chase.promised_date ?? "--"}</dd>
        </div>
        <div>
          <dt className="text-zinc-400">Missed commitments</dt>
          <dd className="text-zinc-700">{chase.missed_count}</dd>
        </div>
        <div>
          <dt className="text-zinc-400">Nudges sent</dt>
          <dd className="text-zinc-700">{chase.nudge_count}</dd>
        </div>
        <div>
          <dt className="text-zinc-400">Last outreach</dt>
          <dd className="text-zinc-700">{formatWhen(chase.last_outreach_at)}</dd>
        </div>
        <div>
          <dt className="text-zinc-400">Next action</dt>
          <dd className="text-zinc-700">{formatWhen(chase.next_action_at)}</dd>
        </div>
      </dl>

      {error && <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[12.5px] text-rose-600">{error}</p>}

      <div className="mb-4 flex flex-wrap gap-2">
        {OPEN_STATES.has(chase.state) && chase.state !== "pending" && (
          <button
            disabled={busy}
            onClick={() => run(() => pauseChase(token!, chase.id))}
            className="rounded-full border border-zinc-200 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
          >
            Pause
          </button>
        )}
        {(chase.state === "paused" || chase.state === "escalated") && (
          <button
            disabled={busy}
            onClick={() => run(() => resumeChase(token!, chase.id))}
            className="rounded-full border border-zinc-200 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
          >
            Resume
          </button>
        )}
        {(chase.state === "escalated" || chase.state === "closed_manual") && (
          <button
            disabled={busy}
            onClick={() => run(() => restartChase(token!, chase.id))}
            className="rounded-full border border-zinc-200 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
          >
            Restart from scratch
          </button>
        )}
        {chase.state !== "closed_paid" && chase.state !== "closed_manual" && (
          <button
            disabled={busy}
            onClick={() => setShowEditDate((s) => !s)}
            className="rounded-full border border-zinc-200 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
          >
            Set payment date
          </button>
        )}
        {chase.state !== "closed_paid" && chase.state !== "closed_manual" && (
          <button
            disabled={busy}
            onClick={() => setShowClose((s) => !s)}
            className="rounded-full border border-zinc-200 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
          >
            Close manually
          </button>
        )}
      </div>

      {showEditDate && (
        <div className="mb-4 flex items-center gap-2">
          <input
            type="date"
            value={newDate}
            onChange={(e) => setNewDate(e.target.value)}
            className="rounded-lg border border-zinc-200 px-3 py-1.5 text-[13px]"
          />
          <button
            disabled={busy || !newDate}
            onClick={() => run(async () => { await editChaseCommitment(token!, chase.id, newDate); setShowEditDate(false); })}
            className="rounded-full bg-zinc-900 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-zinc-800 disabled:opacity-40"
          >
            Save
          </button>
        </div>
      )}

      {showClose && (
        <div className="mb-4 flex items-center gap-2">
          <input
            type="text"
            placeholder="Reason for closing"
            value={closeReason}
            onChange={(e) => setCloseReason(e.target.value)}
            className="flex-1 rounded-lg border border-zinc-200 px-3 py-1.5 text-[13px]"
          />
          <button
            disabled={busy || !closeReason.trim()}
            onClick={() => run(async () => { await closeChase(token!, chase.id, closeReason); setShowClose(false); })}
            className="rounded-full bg-zinc-900 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-zinc-800 disabled:opacity-40"
          >
            Close
          </button>
        </div>
      )}

      <div>
        <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-400">History</h3>
        {!events && <p className="text-[13px] text-zinc-400">Loading...</p>}
        {events && events.length === 0 && <p className="text-[13px] text-zinc-400">No events yet.</p>}
        <div className="space-y-2">
          {events?.map((e) => (
            <div key={e.id} className="border-l-2 border-zinc-100 pl-3 text-[12.5px]">
              <p className="text-zinc-700">
                <span className="font-medium">{e.kind.replace(/_/g, " ")}</span>
                {e.detail?.text ? `: ${String(e.detail.text)}` : ""}
                {e.detail?.reason ? `: ${String(e.detail.reason)}` : ""}
              </p>
              <p className="text-zinc-400">{formatWhen(e.at)}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Chases() {
  const { token } = useSession();
  const [chases, setChases] = useState<Chase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("open");
  const [searchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("chase_id"));

  function refresh() {
    if (!token) return;
    listChases(token)
      .then(setChases)
      .catch((e) => setError(String(e)));
  }

  useEffect(refresh, [token]);

  const visible = chases?.filter((c) => (filter === "open" ? OPEN_STATES.has(c.state) : filter === "all" ? true : c.state === filter)) ?? [];
  const selected = chases?.find((c) => c.id === selectedId) ?? null;

  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">Chases</h1>
        <p className="mt-1 text-[14px] text-zinc-400">
          Every invoice the agentic chase engine is actively pursuing, or has escalated for review.
        </p>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {(["open", "escalated", "all"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium capitalize transition-colors ${
              filter === f ? "bg-zinc-900 text-white" : "border border-zinc-200 text-zinc-600 hover:bg-zinc-50"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {!chases && <p className="text-[13px] text-zinc-400">Loading...</p>}
      {chases && visible.length === 0 && <p className="text-[13px] text-zinc-400">No chases here.</p>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.3fr]">
        <div className="space-y-2">
          {visible.map((c) => (
            <button
              key={c.id}
              onClick={() => setSelectedId(c.id)}
              className={`block w-full rounded-xl border px-4 py-3 text-left transition-colors ${
                selectedId === c.id ? "border-zinc-900 bg-zinc-50" : "border-zinc-200/70 bg-white hover:bg-zinc-50"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="truncate text-[13px] font-medium text-zinc-800">
                  {c.invoice_no ?? c.case_key ?? c.case_id}
                </p>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${STATE_STYLES[c.state] ?? "bg-zinc-100 text-zinc-600"}`}>
                  {stateLabel(c.state)}
                </span>
              </div>
              <p className="mt-0.5 truncate text-[12px] text-zinc-400">{c.project_number ?? "No project"}</p>
            </button>
          ))}
        </div>

        <div>
          {selected ? (
            <ChaseDetail chase={selected} onChanged={refresh} />
          ) : (
            <p className="text-[13px] text-zinc-400">Select a chase to see its details.</p>
          )}
        </div>
      </div>
    </div>
  );
}
