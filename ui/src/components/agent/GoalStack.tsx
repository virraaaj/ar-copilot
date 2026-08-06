export default function GoalStack({ goals }: { goals: Record<string, unknown> }) {
  const rows = [
    { label: "Outcome", value: String(goals.primary_outcome ?? "—") },
    { label: "Objective", value: String(goals.current_objective ?? "—") },
    { label: "Tactic", value: String(goals.selected_tactic ?? "—") },
  ];
  return (
    <div>
      <div className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        Goal stack <span className="text-sky-600">P8</span>
      </div>
      <ol className="space-y-1.5 text-[13px]">
        {rows.map((r, i) => (
          <li key={r.label} className="flex gap-2">
            <span className="w-16 shrink-0 text-zinc-400">{i + 1}. {r.label}</span>
            <span className="text-zinc-800 dark:text-zinc-100">{r.value}</span>
          </li>
        ))}
      </ol>
      {goals.objective_rationale ? (
        <p className="mt-1 text-[12px] text-zinc-500">{String(goals.objective_rationale)}</p>
      ) : null}
    </div>
  );
}
