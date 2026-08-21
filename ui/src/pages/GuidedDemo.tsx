// Guided product demo (added 2026-08-06) -- three presentable scenarios
// that step through the REAL agent loop one beat at a time (run_agent_tick /
// advance_case_with_reply / jump_sim_clock / simulate_payment_for_case),
// so every draft, classification, and decision shown here is the actual
// model doing the work live, not a canned replay.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
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
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-8 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            ✨ Guided Demo
          </h1>
          <p className="mt-1 text-[14px] text-zinc-400 dark:text-zinc-500">
            Three scenarios, stepped through live against the real agent -- every draft, classification, and
            decision below is the actual model, not a script.
          </p>
        </div>
        {started && (
          <button
            disabled={resetBusy || anyStepRunning}
            onClick={start}
            title={anyStepRunning ? "Wait for the running step to finish before restarting" : "Reset the store and start all three scenarios over from step 1"}
            className="shrink-0 rounded-full border border-zinc-200 dark:border-zinc-700 px-3.5 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-50"
          >
            Restart demo
          </button>
        )}
      </div>

      {!started && (
        <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-6 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <p className="mb-4 text-[13px] text-zinc-500 dark:text-zinc-400">
            Starting the demo resets the outcome-agent store and loads three fresh invoices, one per scenario
            below. This clears any other test data currently in Chases.
          </p>
          {startError && (
            <p className="mb-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[12.5px] text-rose-600 dark:text-rose-400">
              {startError}
            </p>
          )}
          <button
            disabled={resetBusy || !scenarios}
            onClick={start}
            className="rounded-full bg-gradient-to-r from-violet-600 to-indigo-600 px-5 py-2 text-[13.5px] font-semibold text-white shadow-sm transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {starting ? "Starting…" : "Start Demo"}
          </button>
        </div>
      )}

      {started && scenarios && (
        <div className="grid gap-5 lg:grid-cols-3">
          {scenarios.map((s) => {
            const rt = runtime[s.id];
            if (!rt) return null;
            const done = rt.nextStep >= s.steps.length;
            const nextBeat = s.steps[rt.nextStep];
            return (
              <div
                key={s.id}
                className="flex flex-col overflow-hidden rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
              >
                <div className="border-b border-zinc-100 dark:border-zinc-800 px-4 py-3.5">
                  <p className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-100">{s.title}</p>
                  <p className="mt-0.5 text-[11.5px] text-zinc-400 dark:text-zinc-500">
                    {s.project_name} · {s.invoice_no}
                  </p>
                  <div className="mt-2 flex items-center gap-1.5">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-violet-600 to-indigo-600 transition-all"
                        style={{ width: `${(rt.nextStep / s.steps.length) * 100}%` }}
                      />
                    </div>
                    <span className="shrink-0 text-[11px] tabular-nums text-zinc-400 dark:text-zinc-500">
                      {rt.nextStep}/{s.steps.length}
                    </span>
                  </div>
                </div>

                {rt.case && (
                  <div className="border-b border-zinc-100 dark:border-zinc-800 px-4 py-3">
                    <AgentPhaseRail state={rt.case.state} target={rt.case.target} compact />
                  </div>
                )}

                <div className="flex-1 space-y-0 overflow-y-auto px-4 py-3" style={{ maxHeight: 420 }}>
                  {rt.history.length === 0 && (
                    <p className="text-[12.5px] text-zinc-400 dark:text-zinc-500">
                      Click "Run next step" to begin.
                    </p>
                  )}
                  {rt.history.map((h, i) => (
                    <div key={i} className="mb-3">
                      <div className="mb-1 flex items-center gap-1.5">
                        <span className="rounded-full bg-violet-50 dark:bg-violet-950/40 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">
                          {opLabel(h.op)}
                        </span>
                        {h.reply_as && (
                          <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                            as {h.reply_as}
                          </span>
                        )}
                      </div>
                      <p className="text-[12.5px] text-zinc-600 dark:text-zinc-300">{h.narration}</p>
                      {replySummary(h) && (
                        <p className="mt-0.5 text-[11.5px] italic text-zinc-400 dark:text-zinc-500">
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
                          <div key={j} className="mt-1.5">
                            <ChaseEventRow event={ev} trace={buildTrace(h.case?.last_decision)} isLast={j === arr.length - 1} />
                          </div>
                        )
                      )}
                    </div>
                  ))}
                </div>

                {rt.error && (
                  <p className="mx-4 mb-2 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[12px] text-rose-600 dark:text-rose-400">
                    {rt.error}
                  </p>
                )}

                <div className="border-t border-zinc-100 dark:border-zinc-800 px-4 py-3">
                  {!done ? (
                    <button
                      disabled={rt.busy || resetBusy}
                      onClick={() => runNextStep(s)}
                      className="w-full rounded-full bg-zinc-900 dark:bg-zinc-100 px-3.5 py-1.5 text-[12.5px] font-medium text-white dark:text-zinc-900 hover:opacity-90 disabled:opacity-50"
                    >
                      {rt.busy ? "Running…" : `Run next step: ${opLabel(nextBeat.op)}`}
                    </button>
                  ) : (
                    <div className="flex items-center justify-between">
                      <span className="rounded-full bg-emerald-50 dark:bg-emerald-950/40 px-2.5 py-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
                        Scenario complete
                      </span>
                      {rt.case && (
                        <Link
                          to={`/chases?case_id=${rt.case.id}`}
                          className="text-[12px] font-medium text-zinc-600 dark:text-zinc-300 hover:underline"
                        >
                          View in Chases →
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
    </main>
  );
}
