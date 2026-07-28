// Chases tab (PLAN_AGENTIC_CHASE.md Phase C4, added 2026-07-20): the
// agentic chase engine's control surface -- every invoice it's actively
// pursuing (or has escalated), the full event history, and the human
// actions available (pause/resume/close/restart/edit the tracked
// commitment). All the actual chase-progression logic lives in
// app/services/chase_engine.py/chase_machine.py; this is purely a
// window onto ChaseStore plus a few write actions.
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  listChases,
  listChaseEvents,
  listProjects,
  pauseChase,
  resumeChase,
  closeChase,
  restartChase,
  editChaseCommitment,
  injectChaseReply,
  simulateChasePayment,
  type Chase,
  type ChaseEvent,
} from "../api";
import { useSession } from "../context/SessionContext";
import { ChaseEventRow } from "../components/ChaseEventRow";
import { AgentPhaseRail } from "../components/AgentPhaseRail";
import { MemoryGraphPanel } from "../components/MemoryGraphPanel";

const STATE_STYLES: Record<string, string> = {
  pending: "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300",
  awaiting_pm: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300",
  awaiting_customer: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300",
  awaiting_contact: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300",
  blocked: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  commitment_tracked: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  verifying_payment: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  escalated: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
  paused: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
  closed_paid: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  closed_manual: "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400",
};

const STATE_LABELS: Record<string, string> = {
  pending: "Starting",
  awaiting_pm: "Waiting on PM",
  awaiting_customer: "Waiting on customer",
  awaiting_contact: "Waiting on contact",
  blocked: "Blocked",
  commitment_tracked: "Payment date tracked",
  verifying_payment: "Verifying payment",
  escalated: "Escalated",
  paused: "Paused",
  closed_paid: "Paid",
  closed_manual: "Closed",
};

const OPEN_STATES = new Set(["pending", "awaiting_pm", "awaiting_customer", "awaiting_contact", "blocked", "commitment_tracked", "verifying_payment"]);

function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// Tokens consumed per invoice (added 2026-07-22) -- the AI-composed
// messages and smart-escalation trajectory assessments both spend real
// tokens; ChaseStore.total_tokens_used is the running total charged
// against this specific chase. 0 means neither AI feature has fired for
// it yet (either they're off, or nothing's happened that needed them).
function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

// Azure OpenAI gpt-5-mini pricing (added 2026-07-28) -- $0.125/1M input,
// $1.00/1M output tokens. Estimate only: displayed for cost visibility,
// not billed anywhere in this app; check the Azure pricing page for the
// current live rate before budgeting off this number.
const GPT5_MINI_INPUT_PER_TOKEN = 0.125 / 1_000_000;
const GPT5_MINI_OUTPUT_PER_TOKEN = 1.0 / 1_000_000;

function estimateCost(promptTokens: number, completionTokens: number): number {
  return promptTokens * GPT5_MINI_INPUT_PER_TOKEN + completionTokens * GPT5_MINI_OUTPUT_PER_TOKEN;
}

function formatCost(usd: number): string {
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
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
  const [showSimulate, setShowSimulate] = useState(false);
  const [replyText, setReplyText] = useState("");

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
    <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-6 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="font-display text-[16px] font-semibold text-zinc-900 dark:text-zinc-100">
            {chase.invoice_no ?? chase.case_key ?? chase.case_id}
          </p>
          <p className="mt-0.5 text-[12px] text-zinc-400 dark:text-zinc-500">{chase.project_number ?? "No project"}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${STATE_STYLES[chase.state] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300"}`}>
          {stateLabel(chase.state)}
        </span>
      </div>

      <dl className="mb-4 grid grid-cols-2 gap-3 text-[12px]">
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Target</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">
            {chase.target === "pm"
              ? "PM"
              : chase.target === "customer"
                ? "Customer"
                : chase.target
                  ? chase.target.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
                  : "--"}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Promised date</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{chase.promised_date ?? "--"}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Missed commitments</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{chase.missed_count}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Nudges sent</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{chase.nudge_count}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Last outreach</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{formatWhen(chase.last_outreach_at)}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Next action</dt>
          <dd className="text-zinc-700 dark:text-zinc-300">{formatWhen(chase.next_action_at)}</dd>
        </div>
        <div>
          <dt className="text-zinc-400 dark:text-zinc-500">Tokens used</dt>
          <dd className="text-zinc-700 dark:text-zinc-300 tabular-nums" title={`${chase.total_tokens_used.toLocaleString()} tokens`}>
            {formatTokens(chase.total_tokens_used)}
          </dd>
        </div>
        {chase.total_tokens_used > 0 && (
          <>
            <div>
              <dt className="text-zinc-400 dark:text-zinc-500">Token split (in / out)</dt>
              <dd
                className="text-zinc-700 dark:text-zinc-300 tabular-nums"
                title={`${chase.prompt_tokens_used.toLocaleString()} prompt / ${chase.completion_tokens_used.toLocaleString()} completion tokens`}
              >
                {formatTokens(chase.prompt_tokens_used)} / {formatTokens(chase.completion_tokens_used)}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-400 dark:text-zinc-500">Est. cost (gpt-5-mini)</dt>
              <dd className="text-zinc-700 dark:text-zinc-300 tabular-nums">
                {formatCost(estimateCost(chase.prompt_tokens_used, chase.completion_tokens_used))}
              </dd>
            </div>
          </>
        )}
      </dl>

      <MemoryGraphPanel chaseId={chase.id} />

      {error && <p className="mb-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[12.5px] text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="mb-4 flex flex-wrap gap-2">
        {OPEN_STATES.has(chase.state) && chase.state !== "pending" && (
          <button
            disabled={busy}
            onClick={() => run(() => pauseChase(token!, chase.id))}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Pause
          </button>
        )}
        {(chase.state === "paused" || chase.state === "escalated") && (
          <button
            disabled={busy}
            onClick={() => run(() => resumeChase(token!, chase.id))}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Resume
          </button>
        )}
        {(chase.state === "escalated" || chase.state === "closed_manual") && (
          <button
            disabled={busy}
            onClick={() => run(() => restartChase(token!, chase.id))}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Restart from scratch
          </button>
        )}
        {chase.state !== "closed_paid" && chase.state !== "closed_manual" && (
          <button
            disabled={busy}
            onClick={() => setShowEditDate((s) => !s)}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Set payment date
          </button>
        )}
        {chase.state !== "closed_paid" && chase.state !== "closed_manual" && (
          <button
            disabled={busy}
            onClick={() => setShowClose((s) => !s)}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Close manually
          </button>
        )}
        {chase.state !== "closed_paid" && chase.state !== "closed_manual" && (
          <button
            disabled={busy}
            onClick={() => setShowSimulate((s) => !s)}
            className="rounded-full border border-dashed border-zinc-300 dark:border-zinc-600 px-3 py-1.5 text-[12.5px] font-medium text-zinc-500 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Simulate...
          </button>
        )}
      </div>

      {showSimulate && chase.state !== "closed_paid" && chase.state !== "closed_manual" && (
        <div className="mb-4 rounded-xl border border-dashed border-zinc-300 dark:border-zinc-600 bg-zinc-50/60 dark:bg-zinc-800/40 p-3">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            Simulation controls (for demo/testing -- not a real reply or payment)
          </p>
          <div className="mb-2 flex items-center gap-2">
            <input
              type="text"
              placeholder="Type a reply as if from the PM/customer..."
              value={replyText}
              onChange={(e) => setReplyText(e.target.value)}
              className="flex-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-[13px]"
            />
            <button
              disabled={busy || !replyText.trim()}
              onClick={() =>
                run(async () => {
                  await injectChaseReply(token!, chase.id, replyText);
                  setReplyText("");
                  setShowSimulate(false);
                })
              }
              className="rounded-full bg-green-700 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-green-800 disabled:opacity-40"
            >
              Inject reply
            </button>
          </div>
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await simulateChasePayment(token!, chase.id);
                setShowSimulate(false);
              })
            }
            className="rounded-full border border-zinc-300 dark:border-zinc-600 bg-white dark:bg-zinc-900 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
          >
            Post simulated payment
          </button>
        </div>
      )}

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
            onClick={() => run(async () => { await editChaseCommitment(token!, chase.id, newDate); setShowEditDate(false); })}
            className="rounded-full bg-green-700 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-green-800 disabled:opacity-40"
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
            className="flex-1 rounded-lg border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[13px]"
          />
          <button
            disabled={busy || !closeReason.trim()}
            onClick={() => run(async () => { await closeChase(token!, chase.id, closeReason); setShowClose(false); })}
            className="rounded-full bg-green-700 px-3.5 py-1.5 text-[12.5px] font-medium text-white hover:bg-green-800 disabled:opacity-40"
          >
            Close
          </button>
        </div>
      )}

      <div>
        <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">History</h3>
        {!events && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>}
        {events && events.length === 0 && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">No events yet.</p>}
        <div className="space-y-2">
          {events?.map((e) => (
            <ChaseEventRow key={e.id} chase={chase} event={e} />
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
  // Grouped by project, collapsed by default, same pattern as Dashboard
  // (added 2026-07-23) -- a flat list of every chase in the system stopped
  // scaling once there were more than a handful; grouping mirrors how
  // Dashboard already organizes everything else by project.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const autoExpanded = useRef(false);
  const [projectNames, setProjectNames] = useState<Map<string, string>>(new Map());

  function refresh() {
    if (!token) return;
    listChases(token)
      .then(setChases)
      .catch((e) => setError(String(e)));
  }

  useEffect(refresh, [token]);

  useEffect(() => {
    if (!token) return;
    listProjects(token)
      .then((projects) => setProjectNames(new Map(projects.map((p) => [p.project_number, p.project_name ?? p.project_number]))))
      .catch(() => setProjectNames(new Map()));
  }, [token]);

  const visible = chases?.filter((c) => (filter === "open" ? OPEN_STATES.has(c.state) : filter === "all" ? true : c.state === filter)) ?? [];
  const selected = chases?.find((c) => c.id === selectedId) ?? null;

  // Deep links from the escalation Teams card carry ?chase_id= -- expand
  // that chase's project group once, the first time its data shows up.
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
    const byProject = new Map<string, { project_number: string | null; chases: Chase[] }>();
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
          Every invoice the agentic chase engine is actively pursuing, or has escalated for review.
        </p>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {(["open", "escalated", "all"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium capitalize transition-colors ${
              filter === f ? "bg-green-700 text-white" : "border border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
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
            const escalatedCount = group.chases.filter((c) => c.state === "escalated").length;

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
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-200">
                            {c.invoice_no ?? c.case_key ?? c.case_id}
                          </p>
                          {c.total_tokens_used > 0 && (
                            <span className="shrink-0 text-[11px] tabular-nums text-zinc-400 dark:text-zinc-500">{formatTokens(c.total_tokens_used)} tok</span>
                          )}
                        </div>
                        <div className="mt-1">
                          <AgentPhaseRail state={c.state} compact />
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
            <ChaseDetail chase={selected} onChanged={refresh} />
          ) : (
            <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Select a chase to see its details.</p>
          )}
        </div>
      </div>
    </div>
  );
}
