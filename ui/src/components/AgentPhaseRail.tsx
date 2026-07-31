// Outcome Agent phase indicator — mirrors CaseState from app/outcome_agent.
type Step = { key: string; label: string; states: string[] };

const STEPS: Step[] = [
  { key: "start", label: "Due / ready", states: ["not_due", "due", "overdue", "outreach_ready"] },
  {
    key: "waiting",
    label: "Waiting on customer",
    states: ["waiting_for_customer", "customer_responded", "blocked", "follow_up_scheduled"],
  },
  { key: "commitment", label: "Commitment tracked", states: ["promise_to_pay"] },
  { key: "missed", label: "Promise missed", states: ["promise_missed"] },
  { key: "done", label: "Done", states: ["paid", "closed"] },
];

function stepIndex(state: string): number {
  return STEPS.findIndex((s) => s.states.includes(state));
}

export function AgentPhaseRail({ state, compact = false }: { state: string; compact?: boolean }) {
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
                title={step.label}
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
          {isAlert ? alertLabel : STEPS[currentIndex]?.label ?? state}
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {isAlert && (
        <p className="text-[12px] font-medium text-rose-600 dark:text-rose-400">{alertLabel}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {STEPS.map((step, i) => {
          const done = !isAlert && currentIndex !== -1 && i < currentIndex;
          const current = !isAlert && i === currentIndex;
          return (
            <div key={step.key} className="flex items-center gap-2">
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold ${
                  done || (isDone && i === STEPS.length - 1)
                    ? "bg-emerald-500 text-white"
                    : current
                      ? "bg-green-700 text-white"
                      : "bg-zinc-100 text-zinc-400 dark:bg-zinc-800 dark:text-zinc-500"
                }`}
              >
                {i + 1}
              </span>
              <span
                className={`text-[12px] ${
                  current ? "font-medium text-zinc-900 dark:text-zinc-100" : "text-zinc-500 dark:text-zinc-400"
                }`}
              >
                {step.label}
              </span>
              {i < STEPS.length - 1 && <span className="text-zinc-300 dark:text-zinc-600">→</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
