// Policy/Configuration Viewer (spec §6.18, added 2026-07-28) -- window
// onto GET /api/policy-config, which itself reads straight from
// ChaseConfig/chase_guardrails.py/config.py so this view can never drift
// from what the agent is actually enforcing. Two rows (AI composer, smart
// escalation) are live toggles backed by runtime_flags.py -- the only
// two CHASE_* flags an operator can flip without editing .env and
// restarting; everything else here stays read-only by design.
import { useEffect, useState } from "react";
import { useSession } from "../context/SessionContext";
import {
  clearDefaultPolicyOverride,
  clearProjectPolicyOverride,
  getAgentPolicy,
  getPolicyConfig,
  getPolicyDocuments,
  listProjects,
  setDefaultPolicyOverride,
  setProjectPolicyOverride,
  setRuntimeFlag,
  type AgentPolicyField,
  type PolicyConfig,
  type PolicyDocument,
  type Project,
} from "../api";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-zinc-100 dark:border-zinc-800 py-2 text-[13px] last:border-0">
      <span className="text-zinc-500 dark:text-zinc-400">{label}</span>
      <span className="font-medium text-zinc-800 dark:text-zinc-200">{value}</span>
    </div>
  );
}

function Bool({ value }: { value: boolean }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${value ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300" : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"}`}>
      {value ? "On" : "Off"}
    </span>
  );
}

function Toggle({ value, disabled, onToggle }: { value: boolean; disabled: boolean; onToggle: () => void }) {
  return (
    <button
      disabled={disabled}
      onClick={onToggle}
      className={`rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-50 ${
        value ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-900/40" : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700"
      }`}
    >
      {value ? "On" : "Off"}
    </button>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">{title}</h3>
      {children}
    </div>
  );
}

// Customizable policy panel (added 2026-08-06, user feedback): every
// timing/threshold knob AgentPolicy exposes, editable at a per-project
// level with a global default underneath. Resolution order (see
// app/outcome_agent/config/policy_overrides.py): project override >
// default override > factory (.env) value. dry_run and allowlists stay
// out of this panel on purpose -- deploy-time safety decisions, not
// something a UI click should change.
const POLICY_FIELD_META: Record<string, { label: string; unit: string; kind: "int" | "float" | "bool" }> = {
  nudge_interval_days: { label: "Days between nudges", unit: "days", kind: "int" },
  min_hours_between_touches: { label: "Min hours between any two touches", unit: "hours", kind: "int" },
  max_unanswered: { label: "Max unanswered nudges before pausing", unit: "", kind: "int" },
  max_missed_promises: { label: "Max missed commitments before escalation", unit: "", kind: "int" },
  max_postponements: { label: "Max postponements", unit: "", kind: "int" },
  max_commitment_days: { label: "Max days out a commitment can be", unit: "days", kind: "int" },
  grace_days: { label: "Grace period after due date", unit: "days", kind: "int" },
  payment_verify_days: { label: "Days to verify a claimed payment", unit: "days", kind: "int" },
  max_sends_per_tick: { label: "Max sends per tick", unit: "", kind: "int" },
  high_dollar_threshold: { label: "High-dollar threshold", unit: "$", kind: "float" },
  composer_enabled: { label: "AI composer enabled", unit: "", kind: "bool" },
  smart_escalation_enabled: { label: "Smart escalation enabled", unit: "", kind: "bool" },
};

const SOURCE_LABEL: Record<string, string> = {
  project: "Overridden for this project",
  default: "Overridden at default",
  factory: "Factory (.env) value",
};

function PolicyFieldRow({
  field,
  data,
  scope,
  onSave,
  onReset,
}: {
  field: string;
  data: AgentPolicyField;
  scope: "default" | "project";
  onSave: (field: string, value: number | boolean) => Promise<void>;
  onReset: (field: string) => Promise<void>;
}) {
  const meta = POLICY_FIELD_META[field];
  const [draft, setDraft] = useState<string>(String(data.value));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft(String(data.value));
  }, [data.value]);

  const dirty = meta.kind !== "bool" && draft !== String(data.value);
  const canReset = data.source === scope;

  async function save() {
    setBusy(true);
    try {
      await onSave(field, meta.kind === "float" ? parseFloat(draft) : parseInt(draft, 10));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    try {
      await onReset(field);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-3 border-b border-zinc-100 dark:border-zinc-800 py-2.5 text-[13px] last:border-0">
      <div className="min-w-0">
        <p className="text-zinc-700 dark:text-zinc-300">{meta.label}</p>
        <p className="text-[11px] text-zinc-400 dark:text-zinc-500">{SOURCE_LABEL[data.source]}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {meta.kind === "bool" ? (
          <Toggle value={Boolean(data.value)} disabled={busy} onToggle={() => onSave(field, !data.value)} />
        ) : (
          <>
            {meta.unit && meta.unit !== "$" && <span className="text-[11px] text-zinc-400 dark:text-zinc-500">{meta.unit}</span>}
            {meta.unit === "$" && <span className="text-[11px] text-zinc-400 dark:text-zinc-500">$</span>}
            <input
              type="number"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="w-20 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1 text-right text-[13px] tabular-nums"
            />
            {dirty && (
              <button
                disabled={busy}
                onClick={save}
                className="rounded-full bg-green-700 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-green-800 disabled:opacity-40"
              >
                Save
              </button>
            )}
          </>
        )}
        {canReset && (
          <button
            disabled={busy}
            onClick={reset}
            className="rounded-full border border-zinc-200 dark:border-zinc-700 px-2.5 py-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:opacity-40"
            title={scope === "default" ? "Revert to factory value" : "Revert to default"}
          >
            Reset
          </button>
        )}
      </div>
    </div>
  );
}

function EditablePolicyPanel() {
  const { token } = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [fields, setFields] = useState<Record<string, AgentPolicyField> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listProjects(token).then(setProjects).catch(() => setProjects([]));
  }, [token]);

  function load() {
    if (!token) return;
    getAgentPolicy(token, selectedProject || undefined)
      .then((r) => setFields(r.fields))
      .catch((e) => setError(String(e)));
  }

  useEffect(load, [token, selectedProject]);

  const scope: "default" | "project" = selectedProject ? "project" : "default";

  async function handleSave(field: string, value: number | boolean) {
    if (!token) return;
    setError(null);
    try {
      if (scope === "default") await setDefaultPolicyOverride(token, field, value);
      else await setProjectPolicyOverride(token, selectedProject, field, value);
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleReset(field: string) {
    if (!token) return;
    setError(null);
    try {
      if (scope === "default") await clearDefaultPolicyOverride(token, field);
      else await clearProjectPolicyOverride(token, selectedProject, field);
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="sm:col-span-2 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">Customize policy</h3>
          <p className="mt-0.5 text-[12px] text-zinc-400 dark:text-zinc-500">
            Set defaults for every project, or override just one. A project falls back to the default for any field
            it hasn't overridden.
          </p>
        </div>
        <select
          value={selectedProject}
          onChange={(e) => setSelectedProject(e.target.value)}
          className="rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2.5 py-1.5 text-[13px]"
        >
          <option value="">Default (all projects)</option>
          {projects.map((p) => (
            <option key={p.project_number} value={p.project_number}>
              {p.project_name ?? p.project_number} ({p.project_number})
            </option>
          ))}
        </select>
      </div>

      {error && (
        <p className="mb-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[12.5px] text-rose-600 dark:text-rose-400">{error}</p>
      )}

      {!fields && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>}
      {fields && (
        <div>
          {Object.entries(POLICY_FIELD_META).map(([field]) => (
            <PolicyFieldRow
              key={field}
              field={field}
              data={fields[field]}
              scope={scope}
              onSave={handleSave}
              onReset={handleReset}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function PolicyConfig() {
  const { token } = useSession();
  const [data, setData] = useState<PolicyConfig | null>(null);
  const [docs, setDocs] = useState<PolicyDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyFlag, setBusyFlag] = useState<string | null>(null);

  function load() {
    if (!token) return;
    getPolicyConfig(token).then(setData).catch((e) => setError(String(e)));
    getPolicyDocuments(token).then(setDocs).catch((e) => setError(String(e)));
  }

  useEffect(load, [token]);

  async function toggle(flag: string, current: boolean) {
    if (!token) return;
    setBusyFlag(flag);
    setError(null);
    try {
      await setRuntimeFlag(token, flag, !current);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyFlag(null);
    }
  }

  if (error) return <p className="px-6 py-8 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>;
  if (!data) return <p className="px-6 py-8 text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>;

  return (
    <div className="mx-auto grid max-w-4xl grid-cols-1 gap-4 px-6 py-8 sm:grid-cols-2">
      <EditablePolicyPanel />

      <Card title="Contact frequency">
        <Row label="Nudge interval" value={`${data.contact_frequency.nudge_interval_days} days`} />
        <Row label="Max nudges" value={data.contact_frequency.max_nudges} />
        <Row label="Min hours between touches" value={data.contact_frequency.min_hours_between_touches} />
      </Card>

      <Card title="Escalation rules">
        <Row label="Max missed commitments" value={data.escalation_rules.max_missed_commitments} />
        <Row label="Max commitment days" value={data.escalation_rules.max_commitment_days} />
        <Row label="Grace days" value={data.escalation_rules.grace_days} />
        <Row label="Payment verify days" value={data.escalation_rules.payment_verify_days} />
        <Row label="Max clarifications" value={data.escalation_rules.max_clarifications} />
        <Row label="Max postponements" value={data.escalation_rules.max_postponements} />
        <Row
          label="Blackout dates"
          value={data.escalation_rules.blackout_dates.length ? data.escalation_rules.blackout_dates.join(", ") : "None"}
        />
      </Card>

      <Card title="Channel configuration">
        <Row label="Chase engine enabled" value={<Bool value={data.channel_configuration.chase_enabled} />} />
        <Row label="Mail polling enabled" value={<Bool value={data.channel_configuration.mail_poll_enabled} />} />
        <Row
          label="AI composer enabled"
          value={
            <Toggle
              value={data.channel_configuration.composer_enabled}
              disabled={busyFlag === "CHASE_COMPOSER_ENABLED"}
              onToggle={() => toggle("CHASE_COMPOSER_ENABLED", data.channel_configuration.composer_enabled)}
            />
          }
        />
        <Row
          label="Smart escalation enabled"
          value={
            <Toggle
              value={data.channel_configuration.smart_escalation_enabled}
              disabled={busyFlag === "CHASE_SMART_ESCALATION_ENABLED"}
              onToggle={() =>
                toggle("CHASE_SMART_ESCALATION_ENABLED", data.channel_configuration.smart_escalation_enabled)
              }
            />
          }
        />
        <Row label="Max sends per tick" value={data.channel_configuration.max_sends_per_tick} />
        <Row
          label="To-address allowlist"
          value={data.channel_configuration.to_address_allowlist.length ? data.channel_configuration.to_address_allowlist.join(", ") : "Unrestricted"}
        />
      </Card>

      <Card title="Allowed actions">
        <div className="flex flex-wrap gap-1.5">
          {data.allowed_actions.map((a) => (
            <span key={a} className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2.5 py-1 text-[11.5px] font-medium text-zinc-600 dark:text-zinc-300">
              {a.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      </Card>

      {docs && (
        <div className="sm:col-span-2 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">Policy documents</h3>
          <p className="mb-3 text-[12px] text-zinc-400 dark:text-zinc-500">
            The library the AI composer references when drafting messages (keyword-matched to what it's writing).
          </p>
          <div className="space-y-3">
            {docs.map((d) => (
              <div key={d.id} className="border-t border-zinc-100 dark:border-zinc-800 pt-3 first:border-0 first:pt-0">
                <p className="mb-0.5 text-[13px] font-medium text-zinc-800 dark:text-zinc-200">{d.title}</p>
                <p className="text-[12.5px] text-zinc-500 dark:text-zinc-400">{d.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
