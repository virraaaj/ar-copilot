export default function CommitmentCard({
  commitment,
}: {
  commitment: Record<string, unknown> | null | undefined;
}) {
  if (!commitment) {
    return (
      <div className="text-[13px] text-zinc-500">
        No active commitment <span className="text-sky-600">P2</span>
      </div>
    );
  }
  return (
    <div className="text-[13px]">
      <div className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        Commitment <span className="text-sky-600">P2</span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-zinc-800 dark:text-zinc-200">
        <span>type: {String(commitment.type)}</span>
        <span>date: {String(commitment.date)}</span>
        <span>owner: {String(commitment.owner)}</span>
        <span>status: {String(commitment.status)}</span>
        <span className="w-full text-zinc-500">if missed: {String(commitment.miss_consequence ?? "re-engage")}</span>
      </div>
    </div>
  );
}
