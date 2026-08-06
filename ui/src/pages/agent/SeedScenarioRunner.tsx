import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listAgentScenarios, resetAgentDemo, runAgentScenario } from "../../api";
import { useSession } from "../../context/SessionContext";
import PrincipleTrace from "../../components/agent/PrincipleTrace";

export default function SeedScenarioRunner() {
  const { token } = useSession();
  const [scenarios, setScenarios] = useState<Array<Record<string, unknown>>>([]);
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [narration, setNarration] = useState("");

  useEffect(() => {
    if (!token) return;
    listAgentScenarios(token).then(setScenarios).catch(console.error);
  }, [token]);

  async function run(id: string) {
    if (!token) return;
    setRunning(id);
    setResult(null);
    setNarration("Resetting demo seed…");
    try {
      await resetAgentDemo(token);
      setNarration(`Running ${id}…`);
      const r = await runAgentScenario(token, id);
      setResult(r);
      const steps = (r.steps as Array<Record<string, unknown>>) || [];
      const last = steps[steps.length - 1];
      setNarration(String(last?.narration || r.title || "Done"));
    } catch (e) {
      setNarration(String(e));
    } finally {
      setRunning(null);
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6">
        <Link to="/agent" className="text-[12px] text-zinc-500 hover:text-zinc-800">
          ← Dashboard
        </Link>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          Seed Scenario Runner
        </h1>
        <p className="mt-1 text-[14px] text-zinc-500">
          One-click demos that make every agentic principle observable (S1–S8).
        </p>
      </div>

      <div className="mb-6 grid gap-3 sm:grid-cols-2">
        {scenarios.map((s) => (
          <button
            key={String(s.id)}
            type="button"
            disabled={!!running}
            onClick={() => run(String(s.id))}
            className="border-b border-zinc-200 py-3 text-left transition hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-800 dark:hover:bg-zinc-900/40"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-zinc-900 dark:text-zinc-100">{String(s.title)}</span>
              <span className="text-[12px] text-zinc-400">{String(s.invoice_no)}</span>
            </div>
            <p className="mt-1 text-[12px] text-zinc-500">{String(s.narration)}</p>
            <div className="mt-2">
              <PrincipleTrace principles={(s.principles as string[]) || []} />
            </div>
            {running === s.id ? <p className="mt-2 text-[12px] text-amber-600">Running…</p> : null}
          </button>
        ))}
      </div>

      {narration && (
        <p className="mb-4 rounded bg-amber-50 px-3 py-2 text-[13px] text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          Now watching: {narration}
        </p>
      )}

      {result && (
        <div className="space-y-3">
          <h2 className="text-[13px] font-semibold uppercase tracking-wide text-zinc-500">Step traces</h2>
          {((result.steps as Array<Record<string, unknown>>) || []).map((step, i) => (
            <div key={i} className="border-l-2 border-green-700 pl-3 text-[13px]">
              <div className="font-medium">
                Step {String(step.step)} · {String(step.op)}
              </div>
              <div className="text-zinc-500">{String(step.narration)}</div>
              {step.asserts ? (
                <pre className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">
                  {JSON.stringify(step.asserts, null, 2)}
                </pre>
              ) : null}
              {(result.final_case as any)?.id ? (
                i === ((result.steps as any[])?.length || 1) - 1 ? (
                  <Link
                    className="mt-1 inline-block text-green-800 hover:underline dark:text-green-400"
                    to={`/agent/cases/${(result.final_case as any).id}`}
                  >
                    Open cockpit →
                  </Link>
                ) : null
              ) : null}
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
