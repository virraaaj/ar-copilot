// Outcome Agent phase indicator — mirrors CaseState from app/outcome_agent.
//
// Ships two visual tones behind an explicit `tone` prop:
//  - "legacy" (default): the original rainbow palette (rose/emerald/green-
//    700/zinc). Used only by Trace Studio (AgentCockpit.tsx, inside
//    .lummus-shell).
//  - "bold": the restrained Bold Typography palette (see ui/src/index.css
//    @theme + ui/src/components/ui/Badge.tsx). All in-scope callers
//    (Chases.tsx, Dashboard.tsx, GuidedDemo.tsx, InvoiceDetail.tsx)
//    pass tone="bold".
import { AlertTriangle, Check, EyeOff } from "lucide-react";
import { Badge, BADGE_TONE_CLASSES, type BadgeTone } from "./ui/Badge";
import { Eyebrow } from "./ui/Eyebrow";

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

// Bold-tone colour policy: the accent (vermillion, BadgeTone "critical")
// is reserved for the states that genuinely need a human to look --
// escalation and dispute. A missed promise or a blocked case is a real
// problem but not (yet) an escalation, so it gets the same amber
// "warning" tone Chases.tsx already uses for those exact states
// (STATE_TONE in pages/Chases.tsx). A tracked commitment or a paid
// invoice is good news, so it gets "positive". A merely-in-progress or
// wound-down case (suppressed/closed/paused) stays neutral grey. Every
// tone is always paired with a mono uppercase text label (and, for the
// alert row, an icon), so colour is never the only signal.
function stateTone(state: string): BadgeTone {
  if (state === "escalated_to_human" || state === "escalation_required" || state === "disputed") return "critical";
  if (state === "blocked" || state === "promise_missed") return "warning";
  if (state === "promise_to_pay" || state === "paid") return "positive";
  return "neutral";
}

export function AgentPhaseRail({
  state,
  target,
  compact = false,
  tone = "legacy",
}: {
  state: string;
  target?: string | null;
  compact?: boolean;
  tone?: "legacy" | "bold";
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

  if (tone === "bold") {
    return (
      <BoldPhaseRail
        state={state}
        target={target}
        compact={compact}
        isAlert={isAlert}
        currentIndex={currentIndex}
        isDone={isDone}
        alertLabel={alertLabel}
      />
    );
  }

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

function BoldPhaseRail({
  state,
  target,
  compact,
  isAlert,
  currentIndex,
  isDone,
  alertLabel,
}: {
  state: string;
  target: string | null | undefined;
  compact: boolean;
  isAlert: boolean;
  currentIndex: number;
  isDone: boolean;
  alertLabel: string;
}) {
  const tone = stateTone(state);

  if (isAlert) {
    const suppressed = state === "suppressed";
    return (
      <Badge tone={suppressed ? "neutral" : "critical"} icon={suppressed ? <EyeOff size={14} strokeWidth={1.5} /> : <AlertTriangle size={14} strokeWidth={1.5} />}>
        {alertLabel}
      </Badge>
    );
  }

  if (compact) {
    const currentLabel = (STEPS[currentIndex] && stepLabel(STEPS[currentIndex], target)) ?? state;
    return (
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1">
          {STEPS.map((step, i) => {
            const done = currentIndex !== -1 && i < currentIndex;
            const current = i === currentIndex;
            const stepFilled = done || (isDone && i === STEPS.length - 1);
            const stepTone = current ? tone : "neutral";
            return (
              <span
                key={step.key}
                title={stepLabel(step, target)}
                className={`h-1.5 w-4 border ${
                  stepFilled
                    ? "border-foreground bg-foreground/70"
                    : current
                      ? `${BADGE_TONE_CLASSES[stepTone]} bg-current/20`
                      : "border-border bg-transparent"
                }`}
              />
            );
          })}
        </div>
        <Eyebrow tone={tone === "critical" ? "accent" : tone === "neutral" ? "muted" : tone}>
          {isDone ? "Done" : currentLabel}
        </Eyebrow>
      </div>
    );
  }

  return (
    <div className="flex items-start">
      {STEPS.map((step, i) => {
        const done = currentIndex !== -1 && i < currentIndex;
        const current = i === currentIndex && !isDone;
        const filled = done || isDone;
        const stepTone = current ? tone : "neutral";
        return (
          <div key={step.key} className="flex flex-1 flex-col items-center last:flex-none last:items-end">
            <div className="flex w-full items-center">
              <div
                className={`flex h-7 w-7 shrink-0 items-center justify-center border text-[11px] font-mono font-semibold ${
                  current
                    ? `${BADGE_TONE_CLASSES[stepTone]} bg-current/10`
                    : filled
                      ? "border-foreground/60 text-foreground"
                      : "border-border text-muted-foreground"
                }`}
              >
                {filled ? <Check size={14} strokeWidth={1.5} /> : i + 1}
              </div>
              {i < STEPS.length - 1 && (
                <div className={`h-px flex-1 ${filled ? "bg-foreground/60" : "bg-border"}`} />
              )}
            </div>
            <div className="mt-2 max-w-[110px] text-center">
              <Eyebrow
                tone={
                  !current
                    ? "muted"
                    : stepTone === "neutral"
                      ? "foreground"
                      : stepTone === "critical"
                        ? "accent"
                        : stepTone
                }
              >
                {stepLabel(step, target)}
              </Eyebrow>
            </div>
          </div>
        );
      })}
    </div>
  );
}
