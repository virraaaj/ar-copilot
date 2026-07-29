export default function WorldVsDialoguePanel({
  world,
  dialogue,
  conflict,
}: {
  world: Record<string, unknown>;
  dialogue: Record<string, unknown>;
  conflict?: boolean;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <section>
        <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
          World Truth <span className="text-sky-600">P1</span>
        </h3>
        <dl className="space-y-1 text-[13px] text-zinc-800 dark:text-zinc-200">
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Balance</dt><dd>${Number(world.balance_due ?? 0).toLocaleString()}</dd></div>
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Status</dt><dd>{String(world.status ?? "—")}</dd></div>
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Due</dt><dd>{String(world.due_date ?? "—")}</dd></div>
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Consent</dt><dd>{world.consent_ok === false ? "opted out" : "ok"}</dd></div>
        </dl>
      </section>
      <section>
        <h3 className="mb-1 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
          Conversation Beliefs <span className="text-sky-600">P1</span>
          {conflict && (
            <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700 dark:bg-rose-950/50 dark:text-rose-300">
              conflict: said paid / world unpaid
            </span>
          )}
        </h3>
        <dl className="space-y-1 text-[13px] text-zinc-800 dark:text-zinc-200">
          <div><dt className="text-zinc-500">Inbound</dt><dd className="mt-0.5 line-clamp-3">{String(dialogue.latest_inbound ?? "—")}</dd></div>
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Reply type</dt><dd>{String(dialogue.reply_type ?? "—")}</dd></div>
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Claimed paid</dt><dd>{dialogue.customer_claimed_paid ? "yes" : "no"}</dd></div>
          <div className="flex justify-between gap-4"><dt className="text-zinc-500">Confidence</dt><dd>{Number(dialogue.interpretation_confidence ?? 1).toFixed(2)}</dd></div>
        </dl>
      </section>
    </div>
  );
}
