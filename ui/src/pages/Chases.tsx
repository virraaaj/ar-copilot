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
import { ChevronRight, ExternalLink } from "lucide-react";
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
import { Button, buttonVariants } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge, type BadgeTone } from "../components/ui/Badge";
import { PageHeader } from "../components/ui/PageHeader";
import { formatMoney, formatTimestamp } from "../utils/format";

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

const STATE_TONE: Record<string, BadgeTone> = {
  blocked: "warning",
  promise_missed: "warning",
  promise_to_pay: "positive",
  paid: "positive",
  escalation_required: "critical",
  escalated_to_human: "critical",
  disputed: "critical",
  paused: "neutral",
  closed: "neutral",
  suppressed: "neutral",
};

const OPEN_STATES = new Set([
  "not_due", "due", "overdue", "outreach_ready", "waiting_for_customer",
  "customer_responded", "blocked", "follow_up_scheduled", "promise_to_pay",
  "promise_missed", "escalation_required", "paused",
]);

function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
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
    <div className="border border-border p-6 md:p-8">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <p className="font-mono text-lg font-semibold text-foreground">
            {chase.invoice_no ?? chase.case_key ?? chase.case_id}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{chase.project_number ?? "No project"}</p>
        </div>
        <Badge tone={STATE_TONE[chase.state] ?? "neutral"} className="shrink-0">
          {stateLabel(chase.state)}
        </Badge>
      </div>

      <div className="mb-5">
        <AgentPhaseRail state={chase.state} target={chase.target} tone="bold" />
      </div>

      <dl className="mb-5 grid grid-cols-2 gap-4 border-y border-border py-4 text-xs sm:grid-cols-3">
        <div>
          <dt className="mb-1"><Eyebrow>Target</Eyebrow></dt>
          <dd className="font-mono text-foreground">
            {chase.target === "pm" ? "PM" : chase.target === "customer" ? "Customer" : chase.target ?? "--"}
          </dd>
        </div>
        <div>
          <dt className="mb-1"><Eyebrow>Promised date</Eyebrow></dt>
          <dd className="font-mono text-foreground">{promisedDate ?? "--"}</dd>
        </div>
        <div>
          <dt className="mb-1"><Eyebrow>Amount</Eyebrow></dt>
          <dd className="font-mono tabular-nums text-foreground">
            {formatMoney(chase.amount != null ? Number(chase.amount) : null)}
          </dd>
        </div>
        <div>
          <dt className="mb-1"><Eyebrow>Open blockers</Eyebrow></dt>
          <dd className="font-mono text-foreground">
            {chase.blockers.filter((b) => b.status === "open").length}
          </dd>
        </div>
        <div>
          <dt className="mb-1"><Eyebrow>Last outreach</Eyebrow></dt>
          <dd className="font-mono text-foreground">{formatTimestamp(chase.last_outreach_at)}</dd>
        </div>
        <div>
          <dt className="mb-1"><Eyebrow>Next action</Eyebrow></dt>
          <dd className="font-mono text-foreground">{formatTimestamp(chase.next_action_at)}</dd>
        </div>
      </dl>

      {error && (
        <p className="mb-4 border border-accent px-4 py-3 text-sm text-accent" role="alert">
          {error}
        </p>
      )}

      <div className="mb-5 flex flex-wrap items-center gap-x-6 gap-y-3">
        <Link to={`/agent/cases/${chase.id}`} className={buttonVariants("primary", "sm")}>
          Open in Trace Studio
          <ExternalLink size={14} strokeWidth={1.5} aria-hidden />
          <span
            aria-hidden
            className="pointer-events-none absolute -bottom-0.5 left-0 h-0.5 w-full origin-left scale-x-100 bg-accent transition-transform duration-150 ease-bold group-hover:scale-x-110"
          />
        </Link>
        {chase.state !== "paused" && !isTerminal && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => pauseAgentCase(token!, chase.id))}>
            Pause
          </Button>
        )}
        {(chase.state === "paused" || chase.state === "escalated_to_human") && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => resumeAgentCase(token!, chase.id))}>
            Resume
          </Button>
        )}
        {(chase.state === "escalated_to_human" || chase.state === "closed") && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => restartAgentCase(token!, chase.id))}>
            Restart from scratch
          </Button>
        )}
        {!isTerminal && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => setShowEditDate((s) => !s)}>
            Set payment date
          </Button>
        )}
        {!isTerminal && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => simulateAgentPayment(token!, chase.id))}>
            Simulate payment
          </Button>
        )}
        {!isTerminal && (
          <Button
            variant="ghost"
            size="sm"
            disabled={closeBusy}
            onClick={() => {
              setCloseBusy(true);
              run(() => closeAgentCase(token!, chase.id)).finally(() => setCloseBusy(false));
            }}
          >
            Close
          </Button>
        )}
      </div>

      {showEditDate && (
        <div className="mb-5 flex items-center gap-3">
          <Input type="date" dense value={newDate} onChange={(e) => setNewDate(e.target.value)} className="w-auto" />
          <Button
            variant="secondary"
            size="sm"
            disabled={busy || !newDate}
            onClick={() =>
              run(async () => {
                await editAgentCommitment(token!, chase.id, newDate);
                setShowEditDate(false);
              })
            }
          >
            Save
          </Button>
        </div>
      )}

      <div>
        <Eyebrow as="p" className="mb-3">History</Eyebrow>
        {!events && <p className="text-sm text-muted-foreground">Loading…</p>}
        {events && events.length === 0 && <p className="text-sm text-muted-foreground">No events yet.</p>}
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
                  tone="bold"
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
      <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
        <p className="border border-accent px-4 py-3 text-sm text-accent" role="alert">{error}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
      <PageHeader
        eyebrow="Outcome agent"
        title="Chases"
        description="Every invoice the agent is actively pursuing, or has escalated for review. Open a case in Trace Studio to send a follow-up or inject a reply."
      />

      <div className="mb-6 flex flex-wrap gap-2" role="tablist" aria-label="Filter chases">
        {(["open", "escalated", "all"] as const).map((f) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            onClick={() => setFilter(f)}
            className={`min-h-11 border px-3.5 py-2 font-mono text-xs font-medium uppercase tracking-wider capitalize transition-colors duration-150 ease-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
              filter === f
                ? "border-accent text-accent"
                : "border-border text-muted-foreground hover:border-muted-foreground hover:text-foreground"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {!chases && <p className="text-sm text-muted-foreground">Loading…</p>}
      {chases && visible.length === 0 && <p className="text-sm text-muted-foreground">No chases here.</p>}

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[5fr_7fr]">
        <div className="space-y-2">
          {projectGroups.map((group) => {
            const key = group.project_number ?? "unknown";
            const isOpen = expanded.has(key);
            const escalatedCount = group.chases.filter((c) => c.state === "escalated_to_human").length;

            return (
              <div key={key} className="border border-border">
                <button
                  onClick={() => toggleExpanded(key)}
                  className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors duration-150 ease-bold hover:bg-muted"
                  aria-expanded={isOpen}
                >
                  <ChevronRight
                    size={14}
                    strokeWidth={1.5}
                    aria-hidden
                    className={`shrink-0 text-muted-foreground transition-transform duration-150 ease-bold ${isOpen ? "rotate-90" : ""}`}
                  />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      {group.project_number ? projectNames.get(group.project_number) ?? group.project_number : "No project"}
                    </p>
                    {group.project_number && <p className="font-mono text-xs text-muted-foreground">{group.project_number}</p>}
                  </div>
                  <span className="ml-auto flex shrink-0 items-center gap-1.5">
                    {escalatedCount > 0 && <Badge tone="critical">{escalatedCount} escalated</Badge>}
                    <Badge tone="neutral">{group.chases.length}</Badge>
                  </span>
                </button>

                {isOpen && (
                  <div className="space-y-1 border-t border-border p-2">
                    {group.chases.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => setSelectedId(c.id)}
                        className={`block w-full border px-3 py-2.5 text-left transition-colors duration-150 ease-bold ${
                          selectedId === c.id ? "border-accent bg-muted" : "border-transparent hover:bg-muted"
                        }`}
                      >
                        <p className="truncate font-mono text-sm font-medium text-foreground">
                          {c.invoice_no ?? c.case_key ?? c.case_id}
                        </p>
                        <div className="mt-1.5">
                          <AgentPhaseRail state={c.state} target={c.target} compact tone="bold" />
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
            <p className="text-sm text-muted-foreground">Select a chase to see its details.</p>
          )}
        </div>
      </div>
    </div>
  );
}
