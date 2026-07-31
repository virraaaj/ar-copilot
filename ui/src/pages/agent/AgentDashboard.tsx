import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getAgentCommitmentMetric,
  getAgentLearningSummary,
  listAgentCases,
  resetAgentDemo,
  type AgentCase,
} from "../../api";
import { useSession } from "../../context/SessionContext";

export default function AgentDashboard() {
  const { token } = useSession();
  const [cases, setCases] = useState<AgentCase[]>([]);
  const [metric, setMetric] = useState<Record<string, unknown> | null>(null);
  const [learning, setLearning] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState("");

  async function load() {
    if (!token) return;
    const [c, m, l] = await Promise.all([
      listAgentCases(token),
      getAgentCommitmentMetric(token),
      getAgentLearningSummary(token),
    ]);
    setCases(c);
    setMetric(m);
    setLearning(l);
  }

  useEffect(() => {
    load().catch((e) => setMsg(String(e)));
  }, [token]);

  const byState: Record<string, number> = {};
  for (const c of cases) byState[c.state] = (byState[c.state] || 0) + 1;
  const open = cases.filter((c) => !["paid", "closed", "disputed", "suppressed"].includes(c.state));
  const escalations = cases.filter((c) =>
    ["escalation_required", "escalated_to_human"].includes(c.state)
  );
  const atRisk = cases.filter((c) => c.state === "promise_to_pay" || c.state === "promise_missed");

  return (
    <main className="lummus-shell mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Agent Dashboard
          </h1>
          <p className="mt-1 text-[14px] text-zinc-500">
            Portfolio view of outcome-agent cases — commitment-known % is the north star.
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/agent/mailbox" className="rounded-full border border-zinc-300 px-3 py-1.5 text-[13px] dark:border-zinc-600">
            Mailbox
          </Link>
          <Link to="/agent/scenarios" className="rounded-full border border-zinc-300 px-3 py-1.5 text-[13px] dark:border-zinc-600">
            Scenario Runner
          </Link>
          <button
            type="button"
            className="rounded-full bg-green-700 px-3 py-1.5 text-[13px] font-medium text-white"
            onClick={async () => {
              if (!token) return;
              try {
                await resetAgentDemo(token);
                await load();
                setMsg("Demo reset to 2026-07-22");
              } catch (e) {
                setMsg(String(e));
              }
            }}
          >
            Reset Demo
          </button>
        </div>
      </div>

      {msg && <p className="mb-4 text-[13px] text-zinc-500">{msg}</p>}

      <div className="mb-8 grid gap-4 sm:grid-cols-4">
        <Stat label="Open cases" value={String(open.length)} />
        <Stat label="Known commitment %" value={`${metric?.known_pct ?? 0}%`} />
        <Stat label="Promises at risk" value={String(atRisk.length)} />
        <Stat label="Escalations" value={String(escalations.length)} />
      </div>

      <section className="mb-8">
        <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-zinc-500">By state</h2>
        <div className="flex flex-wrap gap-2 text-[13px]">
          {Object.entries(byState).map(([s, n]) => (
            <span key={s} className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800">
              {s}: {n}
            </span>
          ))}
        </div>
      </section>

      <section className="mb-8">
        <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-zinc-500">
          Learning summary <span className="text-sky-600">P5/P10</span>
        </h2>
        <p className="text-[13px] text-zinc-700 dark:text-zinc-300">
          Kept: {String((learning as any)?.kept_promises ?? 0)} · Missed:{" "}
          {String((learning as any)?.missed_promises ?? 0)}
        </p>
      </section>

      <section>
        <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-zinc-500">Cases</h2>
        <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {cases.map((c) => (
            <li key={c.id} className="flex items-center justify-between py-2.5 text-[14px]">
              <Link to={`/agent/cases/${c.id}`} className="font-medium text-green-800 hover:underline dark:text-green-400">
                {c.invoice_no || c.case_key}
              </Link>
              <span className="text-zinc-500">{c.customer_name}</span>
              <span className="text-zinc-600 dark:text-zinc-400">{c.state}</span>
            </li>
          ))}
          {!cases.length && <li className="py-4 text-zinc-500">No cases — click Reset Demo.</li>}
        </ul>
      </section>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[12px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="font-display text-2xl font-semibold text-zinc-900 dark:text-zinc-50">{value}</div>
    </div>
  );
}
