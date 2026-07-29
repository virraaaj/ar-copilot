export default function CriticPanel({
  critic,
  reflexion,
}: {
  critic: Record<string, unknown> | null | undefined;
  reflexion?: string;
}) {
  const checks = (critic?.checks as Array<Record<string, unknown>>) || [];
  return (
    <div className="space-y-2">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        Critic <span className="text-sky-600">P4</span>
        {critic?.regenerated ? <span className="ml-2 text-amber-600">regenerated once</span> : null}
        {critic?.requires_human_review ? <span className="ml-2 text-rose-600">human review</span> : null}
      </div>
      <div className="flex flex-wrap gap-1">
        {checks.map((c) => (
          <span
            key={String(c.name)}
            className={`rounded px-1.5 py-0.5 text-[11px] ${
              c.pass
                ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                : "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300"
            }`}
          >
            {String(c.name)}
          </span>
        ))}
        {!checks.length && <span className="text-[13px] text-zinc-500">No critic run yet.</span>}
      </div>
      {reflexion ? (
        <p className="text-[12px] text-amber-800 dark:text-amber-200">
          <span className="font-semibold">Reflexion P13:</span> {reflexion}
        </p>
      ) : null}
    </div>
  );
}
