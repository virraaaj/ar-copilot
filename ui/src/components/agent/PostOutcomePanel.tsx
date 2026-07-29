export default function PostOutcomePanel({
  learning,
  failedAsks,
}: {
  learning: Array<Record<string, unknown>>;
  failedAsks: Array<Record<string, unknown>>;
}) {
  return (
    <div className="space-y-2">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        Post-outcome / learning <span className="text-sky-600">P5 · P10</span>
      </div>
      {!learning?.length && !failedAsks?.length ? (
        <p className="text-[13px] text-zinc-500">No credit assignment yet.</p>
      ) : (
        <ul className="space-y-1 text-[12px]">
          {learning.map((r, i) => (
            <li key={i} className="text-zinc-700 dark:text-zinc-300">
              Ask {String(r.tactic)} → {String(r.outcome)} → Δ{Number(r.weight_delta).toFixed(2)}
            </li>
          ))}
          {failedAsks.slice(-3).map((f, i) => (
            <li key={`f${i}`} className="text-amber-700 dark:text-amber-300">
              Failed ask: {String(f.tactic)} — {String(f.reason)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
