function Meter({ label, left, max }: { label: string; left: number; max: number }) {
  const used = Math.max(0, max - left);
  const pct = max ? Math.min(100, (used / max) * 100) : 0;
  return (
    <div>
      <div className="mb-0.5 flex justify-between text-[12px]">
        <span className="text-zinc-500">{label}</span>
        <span className="text-zinc-800 dark:text-zinc-200">{left} left / {max}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
        <div className="h-full bg-green-700 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function BudgetMeter({ budget }: { budget: Record<string, unknown> }) {
  return (
    <div className="space-y-2">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        Autonomy budgets <span className="text-sky-600">P3</span>
      </div>
      <Meter label="Nudges" left={Number(budget.unanswered_left ?? 0)} max={Number(budget.max_unanswered ?? 3)} />
      <Meter label="Postponements" left={Number(budget.postponements_left ?? 0)} max={Number(budget.max_postponements ?? 3)} />
      <Meter label="Misses" left={Number(budget.misses_left ?? 0)} max={Number(budget.max_missed_promises ?? 3)} />
      <div className="text-[12px] text-zinc-500">
        relationship risk: {Number(budget.relationship_risk ?? 0).toFixed(2)}
        {budget.exhausted ? " · EXHAUSTED" : ""}
      </div>
    </div>
  );
}
