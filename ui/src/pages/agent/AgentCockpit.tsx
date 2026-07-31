import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getAgentCase,
  getAgentStoreTopology,
  getAgentTraceRun,
  listAgentTraces,
  listMailbox,
  resetAgentDemo,
  runAgentFollowUp,
  submitMailboxReply,
  type AgentCase,
} from "../../api";
import LlmCallPanel from "../../components/agent/LlmCallPanel";
import { backendsFromStep, IoList, SourceBadge } from "../../components/agent/SourceBadge";
import { useSession } from "../../context/SessionContext";

const PHASE_TONE: Record<string, string> = {
  signal: "text-status-amber",
  "memory.read.operational": "text-status-emerald",
  "memory.read.episodic": "text-status-emerald",
  "memory.read.semantic": "text-status-emerald",
  "memory.read.learning": "text-status-cyan",
  "memory.read.document": "text-status-emerald",
  "memory.read.graph": "text-status-emerald",
  "context.built": "text-status-cyan",
  "llm.plan": "text-status-violet",
  "llm.draft": "text-status-violet",
  "llm.judge": "text-status-violet",
  "llm.interpret": "text-status-violet",
  "mail.receive": "text-status-amber",
  "mail.send": "text-status-amber",
  guardrails: "text-warning",
  "memory.write": "text-status-emerald",
  "graph.write": "text-status-emerald",
  "state.transition": "text-primary",
  schedule: "text-muted-foreground",
};

export default function AgentCockpit() {
  const { caseId } = useParams();
  const { token } = useSession();
  const [caseRow, setCaseRow] = useState<AgentCase | null>(null);
  const [runs, setRuns] = useState<Array<Record<string, unknown>>>([]);
  const [activeRun, setActiveRun] = useState<Record<string, unknown> | null>(null);
  const [selectedStep, setSelectedStep] = useState<Record<string, unknown> | null>(null);
  const [mail, setMail] = useState<Array<Record<string, unknown>>>([]);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [topology, setTopology] = useState<{ mode: string; backends: Record<string, string> } | null>(null);

  const reload = useCallback(async () => {
    if (!token || !caseId) return;
    const [c, t, m, topo] = await Promise.all([
      getAgentCase(token, caseId),
      listAgentTraces(token, caseId),
      listMailbox(token, { caseId }),
      getAgentStoreTopology(token).catch(() => null),
    ]);
    setCaseRow(c);
    setRuns(t);
    setMail(m);
    if (topo) setTopology(topo);
    if (t[0]?.id) {
      const full = await getAgentTraceRun(token, String(t[0].id));
      setActiveRun(full);
      const steps = (full.steps as Array<Record<string, unknown>>) || [];
      setSelectedStep(steps[steps.length - 1] || null);
    } else {
      setActiveRun(null);
      setSelectedStep(null);
    }
  }, [token, caseId]);

  useEffect(() => {
    reload().catch((e) => setErr(String(e)));
  }, [reload]);

  async function onFollowUp() {
    if (!token || !caseId) return;
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const r = await runAgentFollowUp(token, caseId);
      setMsg(`Follow-up complete — run ${r.run_id}`);
      await reload();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onReply() {
    if (!token || !caseId || !reply.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const r = await submitMailboxReply(token, caseId, reply.trim());
      setReply("");
      setMsg(`Reply processed — run ${r.run_id}`);
      await reload();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onReset() {
    if (!token) return;
    setBusy(true);
    try {
      await resetAgentDemo(token);
      setMsg("Demo reset to 2026-07-22");
      await reload();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openRun(runId: string) {
    if (!token) return;
    const full = await getAgentTraceRun(token, runId);
    setActiveRun(full);
    const steps = (full.steps as Array<Record<string, unknown>>) || [];
    setSelectedStep(steps[0] || null);
  }

  if (!caseRow) {
    return <main className="lummus-shell mx-auto max-w-6xl px-6 py-8 text-muted-foreground">{err || "Loading Trace Studio…"}</main>;
  }

  const steps = ((activeRun?.steps as Array<Record<string, unknown>>) || []);
  const world = caseRow.world || {};

  return (
    <main className="lummus-shell min-h-[calc(100vh-4rem)]">
      <div className="mx-auto max-w-7xl px-6 py-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Trace Studio</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">
              {caseRow.invoice_no} · {caseRow.customer_name || "Customer"}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              State <span className="text-primary font-medium">{caseRow.state}</span>
              {" · "}
              Balance{" "}
              <span className="tabular-nums">
                ${Number(world.balance_due ?? caseRow.amount ?? 0).toLocaleString()}
              </span>
              {topology && (
                <>
                  {" · "}
                  Store mode <span className="text-primary font-medium">{topology.mode}</span>
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/agent" className="lummus-btn-ghost">
              Dashboard
            </Link>
            <Link to="/agent/mailbox" className="lummus-btn-ghost">
              Mailbox
            </Link>
            <button type="button" disabled={busy} onClick={onReset} className="lummus-btn-ghost">
              Reset Demo
            </button>
            <button type="button" disabled={busy} onClick={onFollowUp} className="lummus-btn-primary">
              {busy ? "Running…" : "Run follow-up"}
            </button>
          </div>
        </div>

        {err && <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{err}</p>}
        {msg && <p className="rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-sm text-primary">{msg}</p>}

        {topology && (
          <section className="lummus-card space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Memory sources (where data is pulled / written)
              </h2>
              <span className="text-[11px] text-muted-foreground font-mono">mode={topology.mode}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(topology.backends)
                .filter(([k]) =>
                  ["operational", "episodic", "semantic", "learning", "document", "graph", "mailbox", "case"].includes(k)
                )
                .map(([layer, backend]) => (
                  <div
                    key={layer}
                    className="flex items-center gap-2 rounded-md border border-border bg-secondary/50 px-2.5 py-1.5 text-xs"
                  >
                    <span className="font-mono text-muted-foreground">{layer}</span>
                    <SourceBadge backend={backend} />
                  </div>
                ))}
            </div>
          </section>
        )}

        <div className="grid gap-4 lg:grid-cols-12">
          {/* Mail thread */}
          <section className="lummus-card lg:col-span-4 space-y-3">
            <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Mailbox thread</h2>
            <div className="max-h-[420px] space-y-2 overflow-y-auto">
              {mail.length === 0 && <p className="text-sm text-muted-foreground">No messages yet. Run follow-up to send.</p>}
              {mail.map((m) => (
                <div
                  key={String(m.id)}
                  className={`rounded-md border border-border p-3 text-sm ${
                    m.direction === "outbound" ? "bg-secondary/60" : "bg-primary/5"
                  }`}
                >
                  <div className="flex justify-between gap-2 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{String(m.direction)}</span>
                    <span className="font-mono">{String(m.created_at || "").slice(0, 19)}</span>
                  </div>
                  <p className="mt-1 font-medium text-foreground">{String(m.subject)}</p>
                  <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{String(m.body)}</p>
                </div>
              ))}
            </div>
            <div className="border-t border-border pt-3 space-y-2">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Customer reply</p>
              <textarea
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                rows={3}
                placeholder="Type customer email reply…"
                className="lummus-input w-full"
              />
              <button type="button" disabled={busy || !reply.trim()} onClick={onReply} className="lummus-btn-primary w-full">
                Send reply & run agent
              </button>
            </div>
          </section>

          {/* Live Trace */}
          <section className="lummus-card lg:col-span-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Live Trace</h2>
              <select
                className="lummus-input text-xs py-1"
                value={String(activeRun?.id || "")}
                onChange={(e) => openRun(e.target.value)}
              >
                {runs.map((r) => (
                  <option key={String(r.id)} value={String(r.id)}>
                    {String(r.trigger)} · {String(r.started_at || "").slice(0, 19)}
                  </option>
                ))}
              </select>
            </div>
            <ol className="max-h-[520px] space-y-1 overflow-y-auto">
              {steps.length === 0 && <li className="text-sm text-muted-foreground">No trace yet — click Run follow-up.</li>}
              {steps.map((step) => {
                const phase = String(step.phase);
                const active = selectedStep?.id === step.id;
                return (
                  <li key={String(step.id)}>
                    <button
                      type="button"
                      onClick={() => setSelectedStep(step)}
                      className={`flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left text-sm transition-colors ${
                        active ? "border-primary/50 bg-primary/10" : "border-transparent hover:bg-muted/40"
                      }`}
                    >
                      <span className="font-mono text-[11px] text-muted-foreground w-5 shrink-0">{String(step.seq)}</span>
                      <div className="min-w-0 flex-1">
                        <p className={`font-medium ${PHASE_TONE[phase] || "text-foreground"}`}>{String(step.title)}</p>
                        <p className="font-mono text-[11px] text-muted-foreground truncate">{phase}</p>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {backendsFromStep(step).map((b) => (
                            <SourceBadge key={b} backend={b} />
                          ))}
                        </div>
                      </div>
                      <span className="ml-auto text-[11px] text-muted-foreground">{step.ms != null ? `${step.ms}ms` : ""}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </section>

          {/* Step detail */}
          <section className="lummus-card lg:col-span-4 space-y-3">
            <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Step detail</h2>
            {!selectedStep && <p className="text-sm text-muted-foreground">Select a trace step.</p>}
            {selectedStep && (
              <div className="space-y-3 text-sm">
                <div>
                  <p className="font-semibold text-foreground">{String(selectedStep.title)}</p>
                  <p className="font-mono text-xs text-muted-foreground">{String(selectedStep.phase)} · {String(selectedStep.status)}</p>
                </div>
                {Boolean((selectedStep.call as any)?.name) && (
                  <div>
                    <p className="text-xs uppercase tracking-wider text-muted-foreground mb-1">LLM call</p>
                    <LlmCallPanel call={selectedStep.call as any} />
                  </div>
                )}
                <IoList title="Reads (pulled from)" items={(selectedStep.reads as Array<Record<string, unknown>>) || []} />
                <IoList title="Writes (persisted to)" items={(selectedStep.writes as Array<Record<string, unknown>>) || []} />
                {selectedStep.error ? (
                  <p className="text-destructive text-xs">{String(selectedStep.error)}</p>
                ) : null}
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
