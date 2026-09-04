// Guided product demo (added 2026-08-06) -- three presentable scenarios
// that step through the REAL agent loop one beat at a time (run_agent_tick /
// advance_case_with_reply / jump_sim_clock / simulate_payment_for_case),
// so every draft, classification, and decision shown here is the actual
// model doing the work live, not a canned replay.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import {
  listGuidedDemoScenarios,
  resetGuidedDemo,
  runGuidedDemoStep,
  type AgentCase,
  type GuidedDemoScenario,
  type GuidedDemoStepResult,
} from "../api";
import { useSession } from "../context/SessionContext";
import { AgentPhaseRail } from "../components/AgentPhaseRail";
import { ChaseEventRow } from "../components/ChaseEventRow";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { PageHeader } from "../components/ui/PageHeader";

type ScenarioRuntime = {
  nextStep: number;
  busy: boolean;
  error: string | null;
  history: GuidedDemoStepResult[];
  case: AgentCase | null;
};

function opLabel(op: string): string {
  switch (op) {
    case "run_tick":
      return "Agent runs";
    case "inject_reply":
      return "Reply arrives";
    case "jump":
      return "Time advances";
    case "simulate_payment":
      return "Payment posted";
    default:
      return op;
  }
}

// Turns this step's case.last_decision into the shape ChaseEventRow's
// ReasoningToggle ("why?") expects, so every event rendered below can show
// which tactic was picked, why, and whether the critic passed/failed and
// why -- not just that something happened. Added 2026-08-18: this data was
// already being returned by the API on every step, just never wired to the
// one component built to display it (the same component the Chases tab
// uses "so it doesn't need Trace Studio open to see why a step happened" --
// the guided demo had exactly that same need and wasn't getting it either).
function buildTrace(lastDecision: Record<string, unknown> | null | undefined): Record<string, unknown> | undefined {
  if (!lastDecision) return undefined;
  const selected = (lastDecision.selected_action as Record<string, unknown>) || {};
  const critic = (lastDecision.critic_result as Record<string, unknown>) || {};
  const candidates = lastDecision.candidates_scored as Array<Record<string, unknown>> | undefined;
  return {
    plan: {
      selected_tactic: selected.tactic,
      rationale: selected.rationale,
    },
    judgment: {
      passed: critic.passed,
      failures: critic.failures,
      notes: critic.notes,
    },
    candidates: candidates,
  };
}

// A one-line, plain-English summary of what a reply actually meant --
// interpretation.summary already exists on every inject_reply step's
// result but was being discarded; only reply_type + confidence rendered.
function replySummary(h: GuidedDemoStepResult): string | null {
  if (h.op !== "inject_reply") return null;
  const interp = h.result?.interpretation as Record<string, unknown> | undefined;
  return interp?.summary ? String(interp.summary) : null;
}

export default function GuidedDemo() {
  const { token } = useSession();
  const [scenarios, setScenarios] = useState<GuidedDemoScenario[] | null>(null);
  const [started, setStarted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<Record<string, ScenarioRuntime>>({});

  useEffect(() => {
    if (!token) return;
    listGuidedDemoScenarios(token).then(setScenarios).catch((e) => setStartError(String(e)));
  }, [token]);

  // The three scenarios run against three independent case rows -- there's
  // no reason their steps can't execute concurrently, and they now do.
  // The one operation that genuinely can't overlap with anything else is
  // "Start Demo"/"Restart demo": it wipes the entire store, which any
  // in-flight step is still reading/writing. So only the *reset* is
  // exclusive -- it's blocked while any step is running, and no step can
  // start while a reset is in flight. Two different scenarios' steps
  // running at the same time are not blocked from each other.
  // (2026-08-06: the earlier version of this lock serialized every step
  // on the whole page, which papered over the real bug -- an unguarded
  // reset-vs-in-flight-step race -- instead of fixing it. That race is
  // now fixed at the API layer too (scheduler.py/executor.py None-guards
  // on a case that disappeared mid-request), so this lock only needs to
  // cover the one operation that's actually destructive.)
  const anyStepRunning = Object.values(runtime).some((r) => r.busy);
  const resetBusy = starting;

  async function start() {
    if (!token || !scenarios || resetBusy || anyStepRunning) return;
    setStarting(true);
    setStartError(null);
    try {
      await resetGuidedDemo(token);
      const initial: Record<string, ScenarioRuntime> = {};
      for (const s of scenarios) {
        initial[s.id] = { nextStep: 0, busy: false, error: null, history: [], case: null };
      }
      setRuntime(initial);
      setStarted(true);
    } catch (e) {
      setStartError(String(e));
    } finally {
      setStarting(false);
    }
  }

  async function runNextStep(scenario: GuidedDemoScenario) {
    if (!token || resetBusy) return;
    const rt = runtime[scenario.id];
    if (!rt || rt.busy || rt.nextStep >= scenario.steps.length) return;
    setRuntime((prev) => ({ ...prev, [scenario.id]: { ...rt, busy: true, error: null } }));
    try {
      const result = await runGuidedDemoStep(token, scenario.id, rt.nextStep);
      setRuntime((prev) => {
        const cur = prev[scenario.id];
        return {
          ...prev,
          [scenario.id]: {
            ...cur,
            busy: false,
            nextStep: cur.nextStep + 1,
            history: [...cur.history, result],
            case: result.case,
          },
        };
      });
    } catch (e) {
      setRuntime((prev) => ({ ...prev, [scenario.id]: { ...prev[scenario.id], busy: false, error: String(e) } }));
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
      <PageHeader
        eyebrow="Live walkthrough"
        title={
          <span className="inline-flex items-center gap-3">
            <Sparkles size={30} strokeWidth={1.5} className="text-accent" aria-hidden />
            Guided Demo
          </span>
        }
        description="Three scenarios, stepped through live against the real agent -- every draft, classification, and decision below is the actual model, not a script."
        actions={
          started ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={resetBusy || anyStepRunning}
              onClick={start}
              title={anyStepRunning ? "Wait for the running step to finish before restarting" : "Reset the store and start all three scenarios over from step 1"}
            >
              Restart demo
            </Button>
          ) : undefined
        }
      />

      {!started && (
        <div className="border border-border p-6 md:p-8">
          <p className="mb-5 max-w-2xl text-sm leading-normal text-muted-foreground">
            Starting the demo resets the outcome-agent store and loads three fresh invoices, one per scenario
            below. This clears any other test data currently in Chases.
          </p>
          {startError && (
            <p className="mb-4 border border-accent px-4 py-3 text-sm text-accent" role="alert">
              {startError}
            </p>
          )}
          <Button variant="primary" size="md" disabled={resetBusy || !scenarios} onClick={start}>
            {starting ? "Starting…" : "Start demo"}
          </Button>
        </div>
      )}

      {started && scenarios && (
        <div className="grid gap-6 lg:grid-cols-3">
          {scenarios.map((s) => {
            const rt = runtime[s.id];
            if (!rt) return null;
            const done = rt.nextStep >= s.steps.length;
            const nextBeat = s.steps[rt.nextStep];
            return (
              <div key={s.id} className="flex flex-col border border-border">
                <div className="border-b border-border px-5 py-4">
                  <p className="font-mono text-sm font-semibold text-foreground">{s.title}</p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    {s.project_name} &middot; {s.invoice_no}
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <div className="h-1.5 flex-1 border border-border">
                      <div
                        className="h-full bg-accent transition-all duration-150 ease-bold"
                        style={{ width: `${(rt.nextStep / s.steps.length) * 100}%` }}
                      />
                    </div>
                    <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                      {rt.nextStep}/{s.steps.length}
                    </span>
                  </div>
                </div>

                {rt.case && (
                  <div className="border-b border-border px-5 py-3">
                    <AgentPhaseRail state={rt.case.state} target={rt.case.target} compact tone="bold" />
                  </div>
                )}

                <div className="flex-1 space-y-0 overflow-y-auto px-5 py-4" style={{ maxHeight: 440 }}>
                  {rt.history.length === 0 && (
                    <p className="text-sm text-muted-foreground">Click &ldquo;Run next step&rdquo; to begin.</p>
                  )}
                  {rt.history.map((h, i) => (
                    <div key={i} className="mb-4">
                      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                        <Badge tone="accent">{opLabel(h.op)}</Badge>
                        {h.reply_as && <Badge tone="neutral">as {h.reply_as}</Badge>}
                      </div>
                      <p className="text-sm leading-normal text-foreground">{h.narration}</p>
                      {replySummary(h) && (
                        <p className="mt-1 text-xs italic text-muted-foreground">
                          Understood as: {replySummary(h)}
                        </p>
                      )}
                      {/* Show every ledger event this step actually produced --
                          e.g. a run_tick appends both the outreach (what the
                          agent sent) and a decision entry, and an inject_reply
                          appends the reply itself (what the PM/customer sent) --
                          not just a rationale badge with no message content.
                          Each also gets this step's decision trace (added
                          2026-08-18) so "why?" actually shows the tactic
                          chosen, the alternatives considered and their
                          scores, and the critic's verdict -- not just that
                          something happened. */}
                      {(h.new_events && h.new_events.length > 0 ? h.new_events : h.latest_events?.slice(-1) || []).map(
                        (ev, j, arr) => (
                          <div key={j} className="mt-2">
                            <ChaseEventRow event={ev} trace={buildTrace(h.case?.last_decision)} isLast={j === arr.length - 1} tone="bold" />
                          </div>
                        )
                      )}
                    </div>
                  ))}
                </div>

                {rt.error && (
                  <p className="mx-5 mb-3 border border-accent px-3 py-2 text-xs text-accent" role="alert">
                    {rt.error}
                  </p>
                )}

                <div className="border-t border-border px-5 py-4">
                  {!done ? (
                    <Button variant="secondary" size="sm" disabled={rt.busy || resetBusy} onClick={() => runNextStep(s)} className="w-full">
                      {rt.busy ? "Running…" : `Run next step: ${opLabel(nextBeat.op)}`}
                    </Button>
                  ) : (
                    <div className="flex items-center justify-between gap-3">
                      <Badge tone="positive">Scenario complete</Badge>
                      {rt.case && (
                        <Link
                          to={`/chases?case_id=${rt.case.id}`}
                          className="font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground underline decoration-dotted transition-colors duration-150 ease-bold hover:text-foreground"
                        >
                          View in Chases &rarr;
                        </Link>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
