import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  advanceSimClock,
  createAgentDispute,
  getAgentCase,
  getAgentCaseEvents,
  getAgentDecisionTraces,
  getAgentMemory,
  getSimClock,
  injectAgentReply,
  jumpSimClock,
  resetAgentDemo,
  runAgentTick,
  simulateAgentPayment,
  type AgentCase,
} from "../../api";
import { useSession } from "../../context/SessionContext";
import AgentLoopStepper from "../../components/agent/AgentLoopStepper";
import BudgetMeter from "../../components/agent/BudgetMeter";
import CommitmentCard from "../../components/agent/CommitmentCard";
import ContextPacketViewer from "../../components/agent/ContextPacketViewer";
import CriticPanel from "../../components/agent/CriticPanel";
import GoalStack from "../../components/agent/GoalStack";
import MemoryRecallPanel from "../../components/agent/MemoryRecallPanel";
import PostOutcomePanel from "../../components/agent/PostOutcomePanel";
import PrincipleTrace from "../../components/agent/PrincipleTrace";
import SimulationLab from "../../components/agent/SimulationLab";
import WorldVsDialoguePanel from "../../components/agent/WorldVsDialoguePanel";

export default function AgentCockpit() {
  const { caseId } = useParams();
  const { token } = useSession();
  const [caseRow, setCaseRow] = useState<AgentCase | null>(null);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [traces, setTraces] = useState<Array<Record<string, unknown>>>([]);
  const [memory, setMemory] = useState<{
    memory_facts: Array<Record<string, unknown>>;
    context_packet: Record<string, unknown>;
  } | null>(null);
  const [simDate, setSimDate] = useState<string>("");
  const [err, setErr] = useState("");

  const reload = useCallback(async () => {
    if (!token || !caseId) return;
    const [c, e, t, m, clock] = await Promise.all([
      getAgentCase(token, caseId),
      getAgentCaseEvents(token, caseId),
      getAgentDecisionTraces(token, caseId),
      getAgentMemory(token, caseId),
      getSimClock(token),
    ]);
    setCaseRow(c);
    setEvents(e);
    setTraces(t);
    setMemory(m);
    setSimDate(clock.now?.slice?.(0, 10) || clock.now);
  }, [token, caseId]);

  useEffect(() => {
    reload().catch((e) => setErr(String(e)));
  }, [reload]);

  const activeCommitment = useMemo(() => {
    const list = caseRow?.commitments || [];
    return list.find((c) => c.status === "active") || null;
  }, [caseRow]);

  const last = caseRow?.last_decision || traces[traces.length - 1] || null;
  const candidates = ((last as any)?.candidates_scored as Array<Record<string, unknown>>) || [];
  const selectedId = ((last as any)?.selected_action as any)?.action_id;
  const uncertainty = ((memory?.context_packet as any)?.uncertainty || {}) as Record<string, unknown>;
  const conflict = Boolean((memory?.context_packet as any)?.world_dialogue_conflict);

  if (!caseRow) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-8 text-zinc-500">
        {err || "Loading cockpit…"}
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-6">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <Link to="/agent" className="text-[12px] text-zinc-500 hover:text-zinc-800">
            ← Dashboard
          </Link>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {caseRow.invoice_no} · {caseRow.customer_name}
          </h1>
          <p className="text-[13px] text-zinc-500">
            state <span className="font-medium text-zinc-800 dark:text-zinc-200">{caseRow.state}</span>
            {uncertainty.needs_clarification ? (
              <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                uncertainty P7 — ask one thing
              </span>
            ) : null}
          </p>
        </div>
        <PrincipleTrace principles={((last as any)?.principles_fired as string[]) || []} />
      </div>

      <div className="mb-5 border-b border-zinc-200 pb-4 dark:border-zinc-800">
        <AgentLoopStepper active={String((last as any)?.loop_phase || "schedule")} />
      </div>

      <div className="mb-6 grid gap-6 md:grid-cols-3">
        <GoalStack goals={caseRow.goals || {}} />
        <CommitmentCard commitment={activeCommitment} />
        <BudgetMeter budget={caseRow.budget || {}} />
      </div>

      <div className="mb-6 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <WorldVsDialoguePanel
          world={caseRow.world || {}}
          dialogue={caseRow.dialogue || {}}
          conflict={conflict}
        />
      </div>

      {caseRow.escalation ? (
        <div className="mb-6 rounded border border-rose-200 bg-rose-50/50 p-3 text-[13px] dark:border-rose-900 dark:bg-rose-950/20">
          <div className="mb-1 font-semibold text-rose-800 dark:text-rose-200">
            Escalation dossier <span className="text-sky-600">P11</span>
          </div>
          <p>{String((caseRow.escalation as any).reason)}</p>
          <p className="mt-1 text-zinc-600 dark:text-zinc-400">
            Recommended: {String((caseRow.escalation as any).recommended_human_move)}
          </p>
          <p className="text-zinc-500">autonomy suppressed</p>
        </div>
      ) : null}

      <div className="mb-6 grid gap-6 md:grid-cols-2">
        <MemoryRecallPanel facts={memory?.memory_facts || []} />
        <ContextPacketViewer packet={memory?.context_packet || null} />
      </div>

      <div className="mb-6">
        <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
          Candidate actions <span className="text-sky-600">P9</span>
        </div>
        <table className="w-full text-left text-[13px]">
          <thead className="text-[11px] uppercase text-zinc-500">
            <tr>
              <th className="py-1">Tactic</th>
              <th>Objective</th>
              <th>Score</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <tr
                key={String(c.action_id)}
                className={
                  c.action_id === selectedId
                    ? "bg-green-50 font-medium dark:bg-green-950/30"
                    : "text-zinc-700 dark:text-zinc-300"
                }
              >
                <td className="py-1">{String(c.tactic)}</td>
                <td>{String(c.objective)}</td>
                <td>{Number(c.score).toFixed(2)}</td>
                <td className="text-zinc-500">{String(c.rationale)}</td>
              </tr>
            ))}
            {!candidates.length && (
              <tr>
                <td colSpan={4} className="py-2 text-zinc-500">
                  Run Agent to score candidates.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mb-6 grid gap-6 md:grid-cols-2">
        <CriticPanel
          critic={((last as any)?.critic_result as Record<string, unknown>) || null}
          reflexion={String((last as any)?.reflexion_note || "")}
        />
        <PostOutcomePanel learning={[]} failedAsks={caseRow.failed_asks || []} />
      </div>

      <div className="mb-6">
        <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
          Event ledger
        </div>
        <ul className="max-h-56 space-y-2 overflow-y-auto text-[12px]">
          {events.map((e) => (
            <li key={String(e.id)} className="border-l-2 border-zinc-200 pl-2 dark:border-zinc-700">
              <span className="text-zinc-400">{String(e.at).slice(0, 16)}</span>{" "}
              <span className="font-medium">{String(e.kind)}</span>{" "}
              <PrincipleTrace principles={(e.principles as string[]) || []} />
              <div className="text-zinc-500">{String(e.explanation || "")}</div>
            </li>
          ))}
        </ul>
      </div>

      <div className="border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <SimulationLab
          simDate={simDate}
          onReset={async () => {
            if (!token) return;
            await resetAgentDemo(token);
            window.location.href = "/agent";
          }}
          onAdvance={async (days) => {
            if (!token) return;
            await advanceSimClock(token, days);
            await reload();
          }}
          onJump={async (date) => {
            if (!token) return;
            await jumpSimClock(token, date);
            await reload();
          }}
          onInject={async (text) => {
            if (!token || !caseId) return;
            await injectAgentReply(token, caseId, text);
            await reload();
          }}
          onPay={async () => {
            if (!token || !caseId) return;
            await simulateAgentPayment(token, caseId);
            await reload();
          }}
          onDispute={async () => {
            if (!token || !caseId) return;
            await createAgentDispute(token, caseId, "operator dispute");
            await reload();
          }}
          onRun={async () => {
            if (!token) return;
            await runAgentTick(token);
            await reload();
          }}
        />
      </div>
      {err && <p className="mt-3 text-[13px] text-rose-600">{err}</p>}
    </main>
  );
}
