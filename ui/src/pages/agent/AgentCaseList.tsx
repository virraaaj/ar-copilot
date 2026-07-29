import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listAgentCases, type AgentCase } from "../../api";
import { useSession } from "../../context/SessionContext";

export default function AgentCaseList() {
  const { token } = useSession();
  const [cases, setCases] = useState<AgentCase[]>([]);

  useEffect(() => {
    if (!token) return;
    listAgentCases(token).then(setCases).catch(console.error);
  }, [token]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="mb-4 font-display text-2xl font-semibold text-zinc-900 dark:text-zinc-50">Agent cases</h1>
      <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
        {cases.map((c) => (
          <li key={c.id} className="py-3">
            <Link className="text-green-800 hover:underline dark:text-green-400" to={`/agent/cases/${c.id}`}>
              {c.invoice_no} — {c.customer_name} — {c.state}
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
