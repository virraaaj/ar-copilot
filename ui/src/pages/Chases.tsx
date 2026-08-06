// Chases tab (rebuilt 2026-08-06 on the Outcome Agent's oa_cases data model
// -- the original version of this page, on master, was backed by the old
// chase_store.py engine that this branch replaced). This is the browsing +
// lifecycle-control surface: every case, grouped by project, with
// pause/resume/close/restart/set-date actions and event history. Actual
// reply interaction (typing a customer/PM reply and watching the agent
// respond) intentionally lives in Trace Studio, not here -- see the "Open
// in Trace Studio" link on the detail panel.
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  closeAgentCase,
  editAgentCommitment,
  getAgentCaseEvents,
  getAgentDecisionTraces,
  listAgentCases,
  listProjects,
  pauseAgentCase,
  restartAgentCase,
  resumeAgentCase,
  simulateAgentPayment,
  type AgentCase,
} from "../api";
import { useSession } from "../context/SessionContext";
import { AgentPhaseRail } from "../components/AgentPhaseRail";
import { ChaseEventRow } from "../components/ChaseEventRow";

const STATE_LABELS: Record<string, string> = {
  not_due: "Not due",
  due: "Due",
  overdue: "Overdue",
  outreach_ready: "Ready for outreach",
  waiting_for_customer: "Waiting on reply",
  customer_responded: "Customer responded",
  blocked: "Blocked",
  follow_up_scheduled: "Follow-up scheduled",
  promise_to_pay: "Payment date tracked",
  promise_missed: "Promise missed",
  disputed: "Disputed",
  suppressed: "Suppressed",
  escalation_required: "Escalation required",
  escalated_to_human: "Escalated",
  paid: "Paid",
  closed: "Closed",
  paused: "Paused",
};

const STATE_STYLES: Record<string, string> = {
  blocked: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  promise_missed: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  promise_to_pay: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  paid: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  escalation_required: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  escalated_to_human: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  disputed: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  paused: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
  closed: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
  suppressed: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
};

const OPEN_STATES = new Set([
  "not_due", "due", "overdue", "outreach_ready", "waiting_for_customer",
  "customer_responded", "blocked", "follow_up_scheduled", "promise_to_pay",
  "promise_missed", "escalation_required", "paused",
]);

function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function activeCommitmentDate(commitments: Array<Record<string, unknown>>): string | null {
  const active = commitments.find((c) => c.status === "active" && c.type === "payment_date");
  return active ? String(active.date ?? "") : null;
}

function CaseDetail({ chase, onChanged }: { chase: AgentCase; onChanged: () => void }) {
  const { token } = useSession();
  const [events, setEvents] = useState<Array<Record<string, unknown>> | null>(null);
  const [traces, setTraces] = useState<Array<Record<string, unknown>> | null>(null);
  const [closeBusy, setCloseBusy] = useState(false);
  const [newDate, setNewDate] = useState(activeCommitmentDate(chase.commitments) ?? "");
  const [showEditDate, setShowEditDate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setEvents(null);
    setTraces(null);
    getAgentCaseEvents(token, chase.id).then(setEvents).catch((e) => setError(String(e)));
    getAgentDecisionTraces(token, chase.id).then(setTraces).catch(() => setTraces([]));
  }, [token, chase.id]);

  // Poll the open case's own history too -- a reply landing or a new
  // outreach going out doesn't change chase.id, so the effect above
  // alone would never pick it up without a manual reload.
  useEffect(() => {
    if (!token) return;
    const id = setInterval(() => {
      getAgentCaseEvents(token, chase.id).then(setEvents).catch(() => {});
      getAgentDecisionTraces(token, chase.id).then(setTraces).catch(() => {});
    }, 15000);
    return () => clearInterval(id);
  }, [token, chase.id]);

  // Correlate each event to its decision trace: outreach_sent/dry_run_send
  // share mailbox_id with the trace that produced them (traced_loop.py sets
  // mailbox_id: msg.get("id") on both). critic_blocked events don't have a
  // mailbox_id (nothing was sent), so pair blocked traces to critic_blocked
  // events positionally -- both lists are already chronological, and this
  // branch only ever appends one trace per blocked event, so pairing by
  // occurrence order (Nth blocked trace <-> Nth critic_blocked event) is
  // exact, not a heuristic.
  function traceForEvent(event: Record<string, unknown>, precedingBlockedCount: number): Record<string, unknown> | undefined {
    if (!traces || traces.length === 0) return undefined;
    const detail = (event.detail as Record<string, unknown>) || {};
    const mailboxId = detail.mailbox_id;
    if (mailboxId) {
      const byMailbox = traces.find((t) => t.mailbox_id === mailboxId);
      if (byMailbox) return byMailbox;
    }
    if (event.kind === "critic_blocked") {
      const blockedTraces = traces.filter((t) => t.blocked === true);
      return blockedTraces[precedingBlockedCount];
    }
    return undefined;
  }

  async function run(action: () => Promise<unknown>) {
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

  const promisedDate = activeCommitmentDate(chase.commitments);
  const isTerminal = chase.state === "paid" || chase.state === "closed";

  return (
    <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-6 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="font-display text-[16px] font-semibold text-zinc-900 dark:text-zinc-100">
            {chase.invoice_no ?? chase.case_key ?? chase.case_id}
          </p>
          <p className="mt-0.5 text-[12px] text-zinc-400 dark:text-zinc-500">{chase.project_number ?? "No project"}</p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${
            STATE_STYLES[chase.state] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300"
          }`}
        >
          {stateLabel(chase.state)}
        </span>
      </div>

      <div className="mb-4">
        <AgentPhaseRail state={chase.state} target={chase.target} />
      </div>

      <dl className="mb-4 grid grid-cols-2 gap-3 text-[12px]">
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Target</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">
            {chase.target === "pm" ? "PM" : chase.target === "customer" ? "Customer" : chase.target ?? "--"}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Promised date</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{promisedDate ?? "--"}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Amount</dt>
          <dd className="text-zinc-700 dark:text-zinc-300 tabular-nums">
            {chase.amount != null ? `$${Number(chase.amount).toLocaleString()}` : "--"}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Open blockers</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">
            {chase.blockers.filter((b) => b.status === "open").length}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Last outreach</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{formatWhen(chase.last_outreach_at)}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Next action</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{formatWhen(chase.next_action_at)}</dd>
        </div>
      </dl>

      {error && (
        <p className="mb-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[12.5px] text-rose-600 dark:text-rose-400">
          {error}
        </p>
      )}

      <div className="mb-4 flex flex-wrap gap-2">
        <Link
          to={`/agent/cases/${chase.id}`}
          className="rounded-full bg-green-700 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-green-800"
        >
          Open in Trace Studio
        </Link>
        {chase.state !== "paused" && !isTerminal && (
          <button
            disabled={busy}
            onClick={() => run(() => pauseAgentCase(token!, chase.id))}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Pause
          </button>
        )}
        {(chase.state === "paused" || chase.state === "escalated_to_human") && (
          <button
            disabled={busy}
            onClick={() => run(() => resumeAgentCase(token!, chase.id))}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Resume
          </button>
        )}
        {(chase.state === "escalated_to_human" || chase.state === "closed") && (
          <button
            disabled={busy}
            onClick={() => run(() => restartAgentCase(token!, chase.id))}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Restart from scratch
          </button>
        )}
        {!isTerminal && (
          <button
            disabled={busy}
            onClick={() => setShowEditDate((s) => !s)}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Set payment date
          </button>
        )}
        {!isTerminal && (
          <button
            disabled={busy}
            onClick={() => run(() => simulateAgentPayment(token!, chase.id))}
            className="rounded-full border border-dashed border-zinc-300 dark:border-zinc-600 px-3 py-1.5 text-[12.5px] font-medium text-zinc-500 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Simulate payment
          </button>
        )}
        {!isTerminal && (
          <button
            disabled={closeBusy}
            onClick={() => {
              setCloseBusy(true);
              run(() => closeAgentCase(token!, chase.id)).finally(() => setCloseBusy(false));
            }}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Close
          </button>
        )}
      </div>

      {showEditDate && (
        <div className="mb-4 flex items-center gap-2">
          <input
            type="date"
            value={newDate}
            onChange={(e) => setNewDate(e.target.value)}
            className="rounded-lg border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[13px]"
          />
          <button
            disabled={busy || !newDate}
            onClick={() =>
              run(async () => {
                await editAgentCommitment(token!, chase.id, newDate);
                setShowEditDate(false);
              })
            }
            className="rounded-full bg-green-700 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-green-800 disabled:opacity-40"
          >
            Save
          </button>
        </div>
      )}

      <div>
        <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">History</h3>
        {!events && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>}
        {events && events.length === 0 && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">No events yet.</p>}
        <div>
          {(() => {
            let blockedSeen = 0;
            return events?.map((e, i) => {
              const trace = traceForEvent(e, blockedSeen);
              if (e.kind === "critic_blocked") blockedSeen += 1;
              return (
                <ChaseEventRow
                  key={String(e.id ?? i)}
                  event={e}
                  trace={trace}
                  isLast={i === events.length - 1}
                />
              );
            });
          })()}
        </div>
      </div>
    </div>
  );
}

export default function Chases() {
  const { token } = useSession();
  const [chases, setChases] = useState<AgentCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("open");
  const [searchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("case_id"));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const autoExpanded = useRef(false);
  const [projectNames, setProjectNames] = useState<Map<string, string>>(new Map());

  function refresh() {
    if (!token) return;
    listAgentCases(token).then(setChases).catch((e) => setError(String(e)));
  }

  useEffect(refresh, [token]);

  // Poll for real-world activity (a reply lands, the poller sends an
  // outreach, a payment gets tracked) -- added 2026-08-06 (user request):
  // this page previously only ever refreshed on a manual reload, so a
  // reply that arrived a minute ago wasn't visible until you hit F5.
  useEffect(() => {
    if (!token) return;
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [token]);

  useEffect(() => {
    if (!token) return;
    listProjects(token)
      .then((projects) => setProjectNames(new Map(projects.map((p) => [p.project_number, p.project_name ?? p.project_number]))))
      .catch(() => setProjectNames(new Map()));
  }, [token]);

  const visible =
    chases?.filter((c) =>
      filter === "open" ? OPEN_STATES.has(c.state) : filter === "escalated" ? c.state === "escalated_to_human" : true
    ) ?? [];
  const selected = chases?.find((c) => c.id === selectedId) ?? null;

  useEffect(() => {
    if (autoExpanded.current || !selected?.project_number) return;
    autoExpanded.current = true;
    setExpanded((prev) => new Set(prev).add(selected.project_number!));
  }, [selected]);

  function toggleExpanded(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const projectGroups = (() => {
    const byProject = new Map<string, { project_number: string | null; chases: AgentCase[] }>();
    for (const c of visible) {
      const key = c.project_number ?? "unknown";
      if (!byProject.has(key)) byProject.set(key, { project_number: c.project_number, chases: [] });
      byProject.get(key)!.chases.push(c);
    }
    return [...byProject.values()].sort((a, b) => (a.project_number ?? "").localeCompare(b.project_number ?? ""));
  })();

  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Chases</h1>
        <p className="mt-1 text-[14px] text-zinc-400 dark:text-zinc-500">
          Every invoice the agent is actively pursuing, or has escalated for review. Open a case in Trace Studio to
          send a follow-up or inject a reply.
        </p>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {(["open", "escalated", "all"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium capitalize transition-colors ${
              filter === f
                ? "bg-green-700 text-white"
                : "border border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {!chases && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>}
      {chases && visible.length === 0 && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">No chases here.</p>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.3fr]">
        <div className="space-y-2">
          {projectGroups.map((group) => {
            const key = group.project_number ?? "unknown";
            const isOpen = expanded.has(key);
            const escalatedCount = group.chases.filter((c) => c.state === "escalated_to_human").length;

            return (
              <div key={key} className="overflow-hidden rounded-xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900">
                <button
                  onClick={() => toggleExpanded(key)}
                  className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                >
                  <span className={`shrink-0 text-zinc-400 dark:text-zinc-500 transition-transform ${isOpen ? "rotate-90" : ""}`} aria-hidden>
                    &#9656;
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-200">
                      {group.project_number ? projectNames.get(group.project_number) ?? group.project_number : "No project"}
                    </p>
                    {group.project_number && <p className="text-[11px] text-zinc-400 dark:text-zinc-500">{group.project_number}</p>}
                  </div>
                  <span className="ml-auto flex shrink-0 items-center gap-1.5">
                    {escalatedCount > 0 && (
                      <span className="rounded-full bg-rose-50 dark:bg-rose-950/40 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:text-rose-300">
                        {escalatedCount} escalated
                      </span>
                    )}
                    <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                      {group.chases.length}
                    </span>
                  </span>
                </button>

                {isOpen && (
                  <div className="space-y-1 border-t border-zinc-100 dark:border-zinc-800 p-2">
                    {group.chases.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => setSelectedId(c.id)}
                        className={`block w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                          selectedId === c.id ? "border-green-700 bg-zinc-50 dark:bg-zinc-800/40" : "border-transparent hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                        }`}
                      >
                        <p className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-200">
                          {c.invoice_no ?? c.case_key ?? c.case_id}
                        </p>
                        <div className="mt-1">
                          <AgentPhaseRail state={c.state} target={c.target} compact />
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div>
          {selected ? (
            <CaseDetail chase={selected} onChanged={refresh} />
          ) : (
            <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Select a chase to see its details.</p>
          )}
        </div>
      </div>
    </div>
  );
}
