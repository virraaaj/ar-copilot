// Outcome Agent phase indicator — mirrors CaseState from app/outcome_agent.
type Step = { key: string; label: string; states: string[] };

const STEPS: Step[] = [
  { key: "start", label: "Due / ready", states: ["not_due", "due", "overdue", "outreach_ready"] },
  {
    key: "waiting",
    label: "Waiting on contact",
    states: ["waiting_for_customer", "customer_responded", "blocked", "follow_up_scheduled"],
  },
  { key: "commitment", label: "Commitment tracked", states: ["promise_to_pay"] },
  { key: "missed", label: "Promise missed", states: ["promise_missed"] },
  { key: "done", label: "Done", states: ["paid", "closed"] },
];

function stepIndex(state: string): number {
  return STEPS.findIndex((s) => s.states.includes(state));
}

// The case's current target (case.target: "pm" | "customer") determines
// who the agent is actually waiting on -- PM-first outreach means the
// "waiting" step can mean either, and a flat "Waiting on customer" label
// was misleading for the (common) PM-directed case. Only the "waiting"
// step is target-dependent; every other step name is target-agnostic.
function targetLabel(target: string | null | undefined): string {
  if (target === "pm") return "PM";
  if (target === "customer") return "customer";
  return "contact";
}

function stepLabel(step: Step, target: string | null | undefined): string {
  if (step.key === "waiting") return `Waiting on ${targetLabel(target)}`;
  return step.label;
}

export function AgentPhaseRail({
  state,
  target,
  compact = false,
}: {
  state: string;
  target?: string | null;
  compact?: boolean;
}) {
  const isAlert =
    state === "escalated_to_human" ||
    state === "escalation_required" ||
    state === "disputed" ||
    state === "suppressed";
  const currentIndex = isAlert ? -1 : stepIndex(state);
  const isDone = state === "paid" || state === "closed";

  const alertLabel =
    state === "escalated_to_human" || state === "escalation_required"
      ? "Escalated"
      : state === "disputed"
        ? "Disputed"
        : state === "suppressed"
          ? "Suppressed"
          : state;

  if (compact) {
    return (
      <div className="flex items-center gap-1.5">
        {isAlert ? (
          <span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" />
        ) : (
          STEPS.map((step, i) => {
            const done = currentIndex !== -1 && i < currentIndex;
            const current = i === currentIndex;
            return (
              <span
                key={step.key}
                title={stepLabel(step, target)}
                className={`h-1.5 w-4 rounded-full ${
                  done || (isDone && i === STEPS.length - 1)
                    ? "bg-emerald-500"
                    : current
                      ? "bg-green-700 animate-pulse"
                      : "bg-zinc-200 dark:bg-zinc-700"
                }`}
              />
            );
          })
        )}
        <span
          className={`text-[11px] font-medium ${
            isAlert ? "text-rose-600 dark:text-rose-400" : "text-zinc-500 dark:text-zinc-400"
          }`}
        >
          {isAlert ? alertLabel : (STEPS[currentIndex] && stepLabel(STEPS[currentIndex], target)) ?? state}
        </span>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start">
        {STEPS.map((step, i) => {
          const done = !isAlert && currentIndex !== -1 && i < currentIndex;
          const current = !isAlert && i === currentIndex && !isDone;
          const filled = done || isDone;
          return (
            <div key={step.key} className="flex flex-1 flex-col items-center last:flex-none last:items-end">
              <div className="flex w-full items-center">
                <div
                  className={
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold " +
                    (current
                      ? "bg-green-700 text-white ring-4 ring-green-700/10 animate-pulse"
                      : filled
                        ? "bg-emerald-500 text-white"
                        : "bg-zinc-100 dark:bg-zinc-800 text-zinc-400 dark:text-zinc-500")
                  }
                >
                  {filled ? "✓" : i + 1}
                </div>
                {i < STEPS.length - 1 && (
                  <div className={"h-0.5 flex-1 transition-colors " + (filled ? "bg-emerald-500" : "bg-zinc-100 dark:bg-zinc-800")} />
                )}
              </div>
              <div className="mt-2 max-w-[100px] text-center text-[11px] leading-tight">
                <span className={current ? "font-semibold text-zinc-900 dark:text-zinc-100" : "text-zinc-400 dark:text-zinc-500"}>
                  {stepLabel(step, target)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {isAlert && (
        <div className="mt-4 rounded-lg px-3 py-2 text-[12.5px] font-medium bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300">
          ⚠ {alertLabel}
        </div>
      )}
    </div>
  );
}
