// Chase-engine phase indicator (added 2026-07-23). Deliberately a
// separate rail from InvoiceDetail's StageRail: StageRail reflects the
// dunning/escalation policy's stage config, this reflects the agentic
// chase engine's own state machine (chase_store.py's `state` column).
// No animation library exists in this app (ui/package.json has none) --
// the "in progress" affordance is a plain Tailwind animate-pulse dot.
type Step = { key: string; label: string; states: string[] };

// Linear happy-path steps. escalated/paused are handled separately below
// as an off-rail alert state instead of being forced onto this line --
// forcing them into a numbered step would misrepresent what actually
// happened (an escalation isn't step 5 of a plan, it's a detour from it).
const STEPS: Step[] = [
  { key: "pending", label: "Starting", states: ["pending"] },
  { key: "outreach", label: "Outreach sent, waiting for reply", states: ["awaiting_pm", "awaiting_customer"] },
  { key: "commitment", label: "Commitment tracked", states: ["commitment_tracked"] },
  { key: "verifying", label: "Verifying payment", states: ["verifying_payment"] },
  { key: "done", label: "Done", states: ["closed_paid", "closed_manual"] },
];

function stepIndex(state: string): number {
  return STEPS.findIndex((s) => s.states.includes(state));
}

export function AgentPhaseRail({ state, compact = false }: { state: string; compact?: boolean }) {
  const isAlert = state === "escalated" || state === "paused";
  const currentIndex = isAlert ? -1 : stepIndex(state);
  const isDone = state === "closed_paid" || state === "closed_manual";

  if (compact) {
    return (
      <div className="flex items-center gap-1.5">
        {isAlert ? (
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${state === "escalated" ? "bg-rose-500" : "bg-zinc-400"}`}
          />
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
                      ? "bg-zinc-900 animate-pulse"
                      : "bg-zinc-200"
                }`}
              />
            );
          })
        )}
        <span className={`text-[11px] font-medium ${isAlert ? (state === "escalated" ? "text-rose-600" : "text-zinc-500") : "text-zinc-500"}`}>
          {isAlert ? (state === "escalated" ? "Escalated" : "Paused") : STEPS[currentIndex]?.label ?? state}
        </span>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start">
        {STEPS.map((step, i) => {
          const done = currentIndex !== -1 && i < currentIndex;
          const current = i === currentIndex && !isDone;
          const filled = done || isDone;
          return (
            <div key={step.key} className="flex flex-1 flex-col items-center last:flex-none last:items-end">
              <div className="flex w-full items-center">
                <div
                  className={
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold " +
                    (current
                      ? "bg-zinc-900 text-white ring-4 ring-zinc-900/10 animate-pulse"
                      : filled
                        ? "bg-emerald-500 text-white"
                        : "bg-zinc-100 text-zinc-400")
                  }
                >
                  {filled ? "✓" : i + 1}
                </div>
                {i < STEPS.length - 1 && (
                  <div className={"h-0.5 flex-1 transition-colors " + (filled ? "bg-emerald-500" : "bg-zinc-100")} />
                )}
              </div>
              <div className="mt-2 max-w-[100px] text-center text-[11px] leading-tight">
                <span className={current ? "font-semibold text-zinc-900" : "text-zinc-400"}>{step.label}</span>
              </div>
            </div>
          );
        })}
      </div>
      {isAlert && (
        <div
          className={`mt-4 rounded-lg px-3 py-2 text-[12.5px] font-medium ${
            state === "escalated" ? "bg-rose-50 text-rose-700" : "bg-zinc-100 text-zinc-600"
          }`}
        >
          {state === "escalated" ? "⚠ Escalated for human review" : "⏸ Paused"}
        </div>
      )}
    </div>
  );
}
