import { useState } from "react";

export default function SimulationLab({
  simDate,
  onAdvance,
  onJump,
  onInject,
  onPay,
  onDispute,
  onRun,
  onReset,
}: {
  simDate?: string;
  onAdvance: (days: number) => Promise<void>;
  onJump: (date: string) => Promise<void>;
  onInject: (text: string) => Promise<void>;
  onPay: () => Promise<void>;
  onDispute: () => Promise<void>;
  onRun: () => Promise<void>;
  onReset: () => Promise<void>;
}) {
  const [jump, setJump] = useState("2026-07-28");
  const [reply, setReply] = useState("we already paid");
  const [busy, setBusy] = useState(false);

  async function wrap(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  const btn =
    "rounded-full bg-zinc-900 px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900";
  const ghost =
    "rounded-full border border-zinc-300 px-3 py-1.5 text-[12px] font-medium text-zinc-700 dark:border-zinc-600 dark:text-zinc-200";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
          Simulation lab
        </div>
        <div className="text-[12px] text-zinc-500">sim date: {simDate || "—"}</div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy} className={btn} onClick={() => wrap(() => onReset())}>
          Reset Demo
        </button>
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onAdvance(1))}>
          +1 day
        </button>
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onAdvance(3))}>
          +3
        </button>
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onAdvance(7))}>
          +7
        </button>
        <button type="button" disabled={busy} className={btn} onClick={() => wrap(() => onRun())}>
          Run Agent
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={jump}
          onChange={(e) => setJump(e.target.value)}
          className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-[13px] dark:border-zinc-600"
        />
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onJump(jump))}>
          Jump to date
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          className="min-w-[240px] flex-1 rounded border border-zinc-300 bg-transparent px-2 py-1 text-[13px] dark:border-zinc-600"
        />
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onInject(reply))}>
          Inject reply
        </button>
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onPay())}>
          Post payment
        </button>
        <button type="button" disabled={busy} className={ghost} onClick={() => wrap(() => onDispute())}>
          Create dispute
        </button>
      </div>
    </div>
  );
}
