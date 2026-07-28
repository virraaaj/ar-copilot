// Memory Graph Panel (added 2026-07-25, long-horizon outcome agent spec
// §6.18/§6.8) -- shows the temporal knowledge graph's current facts about
// an invoice: what it's connected to, and what relationship. Superseded
// (closed) edges are deliberately NOT shown here -- this panel is "what's
// true now", not a full history browser; ChaseEventRow's history log
// already tells the story of how a fact changed over time.
import { useEffect, useState } from "react";
import { getChaseGraph, type ChaseGraph } from "../api";
import { useSession } from "../context/SessionContext";

const NODE_TYPE_STYLES: Record<string, string> = {
  Customer: "bg-slate-100 text-slate-600",
  Invoice: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300",
  Blocker: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  Commitment: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300",
  Dispute: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300",
};

function relationshipLabel(rel: string): string {
  return rel.replace(/_/g, " ").toLowerCase();
}

export function MemoryGraphPanel({ chaseId }: { chaseId: string }) {
  const { token } = useSession();
  const [graph, setGraph] = useState<ChaseGraph | null>(null);

  useEffect(() => {
    if (!token || !chaseId) return;
    getChaseGraph(token, chaseId).then(setGraph).catch(() => setGraph(null));
  }, [token, chaseId]);

  if (!graph || graph.edges.length === 0) return null;

  return (
    <div className="mt-4">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
        Relationships
      </p>
      <div className="space-y-1.5">
        {graph.edges.map((edge) => {
          const from = graph.nodes[edge.from_node_id];
          const to = graph.nodes[edge.to_node_id];
          return (
            <div key={edge.id} className="flex flex-wrap items-center gap-1.5 text-[12px]">
              <span className={`rounded-full px-2 py-0.5 font-medium ${NODE_TYPE_STYLES[from?.type ?? ""] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300"}`}>
                {from?.label ?? edge.from_node_id}
              </span>
              <span className="text-zinc-400 dark:text-zinc-500">{relationshipLabel(edge.relationship)}</span>
              <span className={`rounded-full px-2 py-0.5 font-medium ${NODE_TYPE_STYLES[to?.type ?? ""] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300"}`}>
                {to?.label ?? edge.to_node_id}
              </span>
              {to?.attributes?.expected_resolution_date ? (
                <span className="text-zinc-400 dark:text-zinc-500">-- expected {String(to.attributes.expected_resolution_date)}</span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
