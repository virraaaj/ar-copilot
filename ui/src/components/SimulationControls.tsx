// Simulation Control Panel — advance the Outcome Agent demo clock and
// trigger run_agent_tick on demand.
import { useEffect, useState } from "react";
import { advanceSimClock, getSimClock, resetSimClock, runAgentTick, type SimClockState } from "../api";
import { useSession } from "../context/SessionContext";
import { Card } from "./ui/Card";
import { Button } from "./ui/Button";
import { Eyebrow } from "./ui/Eyebrow";
import { Badge } from "./ui/Badge";

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

  return (
    <Card className="mb-8 p-4 md:p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Eyebrow as="p">Simulation clock</Eyebrow>
          <p className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-lg font-semibold tabular-nums text-foreground">
              {clock ? formatSimDate(clock.now) : "--"}
            </span>
            {clock?.is_simulated && <Badge tone="accent">Simulated</Badge>}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {[1, 3, 7].map((days) => (
            <Button
              key={days}
              variant="secondary"
              size="sm"
              disabled={busy}
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
            </Button>
          ))}
          {clock?.is_simulated && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
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
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() =>
              run(
                () => runAgentTick(token!).then((r) => ({ processed: Number((r as { processed?: number }).processed ?? 0) })),
                (result) => {
                  setMessage(`Agent run complete -- ${result.processed} chase${result.processed === 1 ? "" : "s"} processed.`);
                  onRan?.();
                }
              )
            }
          >
            {busy ? "Running…" : "Run agent"}
          </Button>
        </div>
      </div>

      {message && <p className="mt-3 text-xs text-muted-foreground">{message}</p>}
      {error && (
        <p className="mt-3 border border-accent px-3 py-2 text-sm text-accent" role="alert">
          {error}
        </p>
      )}
    </Card>
  );
}
