const PHASES = [
  { id: "observe", label: "Observe", kind: "det" },
  { id: "memory", label: "Recall", kind: "det" },
  { id: "plan", label: "Plan", kind: "ai" },
  { id: "simulate", label: "Simulate", kind: "det" },
  { id: "critic", label: "Critic", kind: "det" },
  { id: "act", label: "Act", kind: "det" },
  { id: "judge", label: "Judge", kind: "det" },
  { id: "schedule", label: "Schedule", kind: "det" },
] as const;

export default function AgentLoopStepper({ active = "schedule" }: { active?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" title="P12: blue=deterministic, amber=AI">
      {PHASES.map((p, i) => {
        const isActive = p.id === active;
        const color =
          p.kind === "ai"
            ? isActive
              ? "bg-amber-500 text-white"
              : "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
            : isActive
              ? "bg-sky-600 text-white"
              : "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200";
        return (
          <div key={p.id} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-zinc-300 dark:text-zinc-600">→</span>}
            <span className={`rounded px-2 py-1 text-[11px] font-medium ${color}`}>{p.label}</span>
          </div>
        );
      })}
    </div>
  );
}
