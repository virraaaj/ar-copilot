// Policy/Configuration Viewer (spec §6.18, added 2026-07-28) -- window
// onto GET /api/policy-config, which itself reads straight from
// ChaseConfig/chase_guardrails.py/config.py so this view can never drift
// from what the agent is actually enforcing. Two rows (AI composer, smart
// escalation) are live toggles backed by runtime_flags.py -- the only
// two CHASE_* flags an operator can flip without editing .env and
// restarting; everything else here stays read-only by design.
//
// Converted to Bold Typography (2026-09-04): this is a config/settings
// screen, not a marketing page, so the system's poster-scale type is
// deliberately dialed back -- labeled rows, mono for every numeric/boolean
// value, and Card used only to group related fields, not for decoration.
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
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
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Input, Select } from "../components/ui/Input";
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge } from "../components/ui/Badge";

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border py-2.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-foreground">{value}</span>
    </div>
  );
}

function Bool({ value }: { value: boolean }) {
  return <Badge tone={value ? "positive" : "neutral"}>{value ? "On" : "Off"}</Badge>;
}

function Toggle({ value, disabled, onToggle }: { value: boolean; disabled: boolean; onToggle: () => void }) {
  return (
    <button
      disabled={disabled}
      onClick={onToggle}
      className={`min-h-11 border px-3 font-mono text-xs font-medium uppercase tracking-wide transition-colors duration-150 ease-bold disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
        value
          ? "border-[#2f8f4e] text-[#4ade80] hover:bg-[#2f8f4e]/10"
          : "border-border text-muted-foreground hover:border-muted-foreground hover:text-foreground"
      }`}
    >
      {value ? "On" : "Off"}
    </button>
  );
}

function SectionCard({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return (
    <Card className={`p-5 md:p-5 ${className}`}>
      <Eyebrow as="p" className="mb-3">{title}</Eyebrow>
      {children}
    </Card>
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
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 text-sm last:border-0">
      <div className="min-w-0">
        <p className="text-foreground">{meta.label}</p>
        <p className="font-mono text-xs uppercase tracking-wide text-muted-foreground">{SOURCE_LABEL[data.source]}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {meta.kind === "bool" ? (
          <Toggle value={Boolean(data.value)} disabled={busy} onToggle={() => onSave(field, !data.value)} />
        ) : (
          <>
            {meta.unit && (
              <span className="font-mono text-xs text-muted-foreground">{meta.unit === "$" ? "$" : meta.unit}</span>
            )}
            <Input
              dense
              type="number"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="w-20 text-right font-mono tabular-nums"
            />
            {dirty && (
              <Button variant="secondary" size="sm" disabled={busy} onClick={save}>
                Save
              </Button>
            )}
          </>
        )}
        {canReset && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={reset} title={scope === "default" ? "Revert to factory value" : "Revert to default"}>
            Reset
          </Button>
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
    <Card className="p-5 md:p-5 sm:col-span-2">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <Eyebrow as="p" tone="accent" className="mb-1">Customize policy</Eyebrow>
          <p className="max-w-lg text-sm leading-normal text-muted-foreground">
            Set defaults for every project, or override just one. A project falls back to the default for any field
            it hasn't overridden.
          </p>
        </div>
        <Select
          dense
          value={selectedProject}
          onChange={(e) => setSelectedProject(e.target.value)}
          className="w-auto"
        >
          <option value="">Default (all projects)</option>
          {projects.map((p) => (
            <option key={p.project_number} value={p.project_number}>
              {p.project_name ?? p.project_number} ({p.project_number})
            </option>
          ))}
        </Select>
      </div>

      {error && (
        <p className="mb-3 border border-accent px-3 py-2 text-sm text-accent" role="alert">
          {error}
        </p>
      )}

      {!fields && <p className="text-sm text-muted-foreground">Loading…</p>}
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
    </Card>
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

  if (error) {
    return (
      <p className="border border-accent px-6 py-4 mx-6 my-8 text-sm text-accent" role="alert">
        {error}
      </p>
    );
  }
  if (!data) return <p className="px-6 py-8 text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="mx-auto grid max-w-4xl grid-cols-1 gap-4 px-6 py-8 sm:grid-cols-2">
      <EditablePolicyPanel />

      <SectionCard title="Contact frequency">
        <Row label="Nudge interval" value={`${data.contact_frequency.nudge_interval_days} days`} />
        <Row label="Max nudges" value={data.contact_frequency.max_nudges} />
        <Row label="Min hours between touches" value={data.contact_frequency.min_hours_between_touches} />
      </SectionCard>

      <SectionCard title="Escalation rules">
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
      </SectionCard>

      <SectionCard title="Channel configuration">
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
      </SectionCard>

      <SectionCard title="Allowed actions">
        <div className="flex flex-wrap gap-1.5">
          {data.allowed_actions.map((a) => (
            <Badge key={a} tone="neutral">{a.replace(/_/g, " ")}</Badge>
          ))}
        </div>
      </SectionCard>

      {docs && (
        <Card className="p-5 md:p-5 sm:col-span-2">
          <Eyebrow as="p" className="mb-1">Policy documents</Eyebrow>
          <p className="mb-3 text-sm text-muted-foreground">
            The library the AI composer references when drafting messages (keyword-matched to what it's writing).
          </p>
          <div className="space-y-3">
            {docs.map((d) => (
              <div key={d.id} className="border-t border-border pt-3 first:border-0 first:pt-0">
                <p className="mb-0.5 text-sm font-medium text-foreground">{d.title}</p>
                <p className="text-sm leading-normal text-muted-foreground">{d.text}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
