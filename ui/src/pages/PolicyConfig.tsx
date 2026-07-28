// Policy/Configuration Viewer (spec §6.18, added 2026-07-28) -- read-only
// window onto GET /api/policy-config, which itself reads straight from
// ChaseConfig/chase_guardrails.py/config.py so this view can never drift
// from what the agent is actually enforcing.
import { useEffect, useState } from "react";
import { useSession } from "../context/SessionContext";
import { getPolicyConfig, getPolicyDocuments, type PolicyConfig, type PolicyDocument } from "../api";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-zinc-100 py-2 text-[13px] last:border-0">
      <span className="text-zinc-500">{label}</span>
      <span className="font-medium text-zinc-800">{value}</span>
    </div>
  );
}

function Bool({ value }: { value: boolean }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${value ? "bg-emerald-50 text-emerald-700" : "bg-zinc-100 text-zinc-500"}`}>
      {value ? "On" : "Off"}
    </span>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-zinc-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-zinc-400">{title}</h3>
      {children}
    </div>
  );
}

export default function PolicyConfig() {
  const { token } = useSession();
  const [data, setData] = useState<PolicyConfig | null>(null);
  const [docs, setDocs] = useState<PolicyDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getPolicyConfig(token).then(setData).catch((e) => setError(String(e)));
    getPolicyDocuments(token).then(setDocs).catch((e) => setError(String(e)));
  }, [token]);

  if (error) return <p className="px-6 py-8 text-[13px] text-rose-600">{error}</p>;
  if (!data) return <p className="px-6 py-8 text-[13px] text-zinc-400">Loading...</p>;

  return (
    <div className="mx-auto grid max-w-4xl grid-cols-1 gap-4 px-6 py-8 sm:grid-cols-2">
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
          label="High-dollar approval threshold"
          value={`$${data.escalation_rules.high_dollar_approval_threshold.toLocaleString()}`}
        />
        <Row
          label="Blackout dates"
          value={data.escalation_rules.blackout_dates.length ? data.escalation_rules.blackout_dates.join(", ") : "None"}
        />
      </Card>

      <Card title="Channel configuration">
        <Row label="Chase engine enabled" value={<Bool value={data.channel_configuration.chase_enabled} />} />
        <Row label="Dry run" value={<Bool value={data.channel_configuration.dry_run} />} />
        <Row label="Mail polling enabled" value={<Bool value={data.channel_configuration.mail_poll_enabled} />} />
        <Row label="AI composer enabled" value={<Bool value={data.channel_configuration.composer_enabled} />} />
        <Row label="Smart escalation enabled" value={<Bool value={data.channel_configuration.smart_escalation_enabled} />} />
        <Row label="Max sends per tick" value={data.channel_configuration.max_sends_per_tick} />
        <Row
          label="To-address allowlist"
          value={data.channel_configuration.to_address_allowlist.length ? data.channel_configuration.to_address_allowlist.join(", ") : "Unrestricted"}
        />
      </Card>

      <Card title="Allowed actions">
        <div className="flex flex-wrap gap-1.5">
          {data.allowed_actions.map((a) => (
            <span key={a} className="rounded-full bg-zinc-100 px-2.5 py-1 text-[11.5px] font-medium text-zinc-600">
              {a.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      </Card>

      {docs && (
        <div className="sm:col-span-2 rounded-2xl border border-zinc-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <h3 className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-zinc-400">Policy documents</h3>
          <p className="mb-3 text-[12px] text-zinc-400">
            The library the AI composer references when drafting messages (keyword-matched to what it's writing).
          </p>
          <div className="space-y-3">
            {docs.map((d) => (
              <div key={d.id} className="border-t border-zinc-100 pt-3 first:border-0 first:pt-0">
                <p className="mb-0.5 text-[13px] font-medium text-zinc-800">{d.title}</p>
                <p className="text-[12.5px] text-zinc-500">{d.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
