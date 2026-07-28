// Simulation Control Panel (added 2026-07-25, long-horizon outcome agent
// spec §6.18/§11.4) -- lets an operator advance the demo's simulated
// clock ("what day is it, as far as the chase engine is concerned") and
// manually trigger a chase-engine pass, so a multi-week collections
// workflow can be demonstrated in minutes. Advancing the clock alone does
// nothing by itself -- it only changes what "today" means for the next
// run, same as the real poller tick would use real time. "Run Agent"
// mirrors that tick on demand.
import { useEffect, useState } from "react";
import { advanceSimClock, getSimClock, resetSimClock, runChaseTick, type SimClockState } from "../api";
import { useSession } from "../context/SessionContext";

function formatSimDate(iso: string): string {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
}

export function SimulationControls({ onRan }: { onRan?: () => void }) {
  const { token } = useSession();
  const [clock, setClock] = useState<SimClockState | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    if (!token) return;
    getSimClock(token).then(setClock).catch(() => setClock(null));
  }

  useEffect(refresh, [token]);

  async function run<T>(action: () => Promise<T>, onSuccess: (result: T) => void) {
    setBusy(true);
    setError(null);
    try {
      onSuccess(await action());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const buttonClass =
    "rounded-full border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 " +
    "transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="mb-8 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">Simulation clock</p>
          <p className="mt-1 flex items-baseline gap-2">
            <span className="font-display text-[18px] font-semibold text-zinc-900 dark:text-zinc-100">
              {clock ? formatSimDate(clock.now) : "--"}
            </span>
            {clock?.is_simulated && (
              <span className="rounded-full bg-indigo-50 dark:bg-indigo-950/40 px-2 py-0.5 text-[11px] font-medium text-indigo-700 dark:text-indigo-300">
                simulated
              </span>
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {[1, 3, 7].map((days) => (
            <button
              key={days}
              disabled={busy}
              className={buttonClass}
              onClick={() =>
                run(
                  () => advanceSimClock(token!, days),
                  (result) => {
                    setClock(result);
                    setMessage(`Advanced ${days} day${days === 1 ? "" : "s"}.`);
                  }
                )
              }
            >
              +{days}d
            </button>
          ))}
          {clock?.is_simulated && (
            <button
              disabled={busy}
              className={buttonClass}
              onClick={() =>
                run(
                  () => resetSimClock(token!),
                  (result) => {
                    setClock(result);
                    setMessage("Reset to real time.");
                  }
                )
              }
            >
              Reset
            </button>
          )}
          <button
            disabled={busy}
            className="rounded-full bg-green-700 px-3.5 py-1.5 text-[12.5px] font-medium text-white transition-colors hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() =>
              run(
                () => runChaseTick(token!),
                (result) => {
                  setMessage(`Agent run complete -- ${result.processed} chase${result.processed === 1 ? "" : "s"} processed.`);
                  onRan?.();
                }
              )
            }
          >
            {busy ? "Running..." : "Run Agent"}
          </button>
        </div>
      </div>

      {message && <p className="mt-3 text-[12px] text-zinc-400 dark:text-zinc-500">{message}</p>}
      {error && <p className="mt-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[12px] text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}
