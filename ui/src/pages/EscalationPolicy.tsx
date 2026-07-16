import { useEffect, useState } from "react";
import { getEscalationPolicy, updateEscalationStage, type EscalationPolicy as PolicyType, type EscalationStage } from "../api";
import { useSession } from "../context/SessionContext";

interface RowState {
  value: string;
  saving: boolean;
  saved: boolean;
  error: string | null;
}

export default function EscalationPolicy() {
  const { token } = useSession();
  const [policy, setPolicy] = useState<PolicyType | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getEscalationPolicy(token)
      .then((p) => {
        setPolicy(p);
        const initial: Record<string, RowState> = {};
        for (const s of p.stages) {
          initial[s.stage_id] = {
            value: s.min_days_in_stage != null ? String(s.min_days_in_stage) : "",
            saving: false,
            saved: false,
            error: null,
          };
        }
        setRows(initial);
      })
      .catch((e) => setLoadError(String(e)));
  }, [token]);

  function setRow(stageId: string, patch: Partial<RowState>) {
    setRows((r) => ({ ...r, [stageId]: { ...r[stageId], ...patch } }));
  }

  async function saveStage(stage: EscalationStage) {
    if (!token || !policy || !stage.stage_rule_id) return;
    const row = rows[stage.stage_id];
    const days = parseInt(row.value, 10);
    if (!Number.isFinite(days) || days < 0) {
      setRow(stage.stage_id, { error: "Enter a whole number of days (0 or more).", saved: false });
      return;
    }
    setRow(stage.stage_id, { saving: true, error: null, saved: false });
    try {
      await updateEscalationStage(token, policy.version_id, stage.stage_rule_id, { min_days_in_stage: days });
      setRow(stage.stage_id, { saving: false, saved: true });
    } catch (e) {
      setRow(stage.stage_id, { saving: false, error: String(e) });
    }
  }

  if (loadError) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{loadError}</p>
      </div>
    );
  }
  if (!policy) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="text-[13px] text-zinc-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">Escalation Policy</h1>
        <p className="mt-1 text-[14px] text-zinc-400">
          The day (counted from the due date) each stage begins. A stage's duration is the gap until the next stage's start day.
          {policy.version_label ? ` Editing the published version (${policy.version_label}).` : ""}
        </p>
      </div>

      <div className="overflow-hidden rounded-2xl border border-zinc-200/70 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        {policy.stages.map((stage, i) => {
          const row = rows[stage.stage_id];
          const nextStage = policy.stages[i + 1];
          const nextRow = nextStage ? rows[nextStage.stage_id] : undefined;
          const thisDay = row ? parseInt(row.value, 10) : NaN;
          const nextDay = nextRow ? parseInt(nextRow.value, 10) : NaN;
          const duration =
            Number.isFinite(thisDay) && Number.isFinite(nextDay) ? nextDay - thisDay : null;

          return (
            <div key={stage.stage_id} className="border-b border-zinc-50 px-5 py-4 last:border-0">
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-100 text-[12px] font-semibold text-zinc-500">
                    {stage.sequence_order}
                  </span>
                  <div>
                    <p className="text-[14px] font-medium text-zinc-800">{stage.stage_name}</p>
                    <p className="text-[12px] text-zinc-400">
                      {stage.is_terminal_stage
                        ? "Terminal stage"
                        : duration !== null && duration >= 0
                          ? `Lasts ${duration} day${duration === 1 ? "" : "s"}, until ${nextStage.stage_name}`
                          : null}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-[13px] text-zinc-400">Starts on day</span>
                  <input
                    type="number"
                    min={0}
                    value={row?.value ?? ""}
                    onChange={(e) => setRow(stage.stage_id, { value: e.target.value, saved: false, error: null })}
                    className="w-16 rounded-lg border border-zinc-200 px-3 py-1.5 text-right text-[14px] tabular-nums outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
                  />
                  <button
                    onClick={() => saveStage(stage)}
                    disabled={row?.saving}
                    className="rounded-lg border border-zinc-200 px-3 py-1.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-40"
                  >
                    {row?.saving ? "Saving..." : "Save"}
                  </button>
                  {row?.saved && <span className="text-[12px] font-medium text-emerald-600">Saved</span>}
                </div>
              </div>
              {row?.error && <p className="mt-2 text-[12px] text-rose-600">{row.error}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
