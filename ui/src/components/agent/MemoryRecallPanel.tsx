export default function MemoryRecallPanel({ facts }: { facts: Array<Record<string, unknown>> }) {
  return (
    <div>
      <div className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        Memory recall <span className="text-sky-600">P6</span>
      </div>
      {!facts?.length ? (
        <p className="text-[13px] text-zinc-500">No facts recalled yet.</p>
      ) : (
        <ul className="max-h-40 space-y-1 overflow-y-auto text-[12px]">
          {facts.slice(0, 12).map((f, i) => (
            <li key={i} className="border-l-2 border-zinc-200 pl-2 dark:border-zinc-700">
              <span className="text-zinc-400">[{String(f.layer)}]</span>{" "}
              <span className="font-medium text-zinc-700 dark:text-zinc-200">{String(f.key)}</span>
              {f.superseded ? <span className="ml-1 text-amber-600">superseded</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
