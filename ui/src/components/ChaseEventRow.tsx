// Icon-based event timeline for the Chases page (ported design from
// master's ChaseEventRow.tsx 2026-08-06, adapted to this branch's actual
// oa_events kind vocabulary -- invoice_synced/outreach_sent/reply_received/
// contact_changed_notice/critic_blocked/decision/promise_missed/
// date_advanced/payment_posted/dispute_created/note, not master's
// chase_machine-driven kinds. Each row also carries "reasoning" pulled
// from the matching oa_decision_traces entry (same rationale/judgment
// data Trace Studio's Reasoning panel shows) so the Chases tab doesn't
// need Trace Studio open to see *why* a step happened, not just that it did.
import { useState } from "react";

type KindMeta = { icon: string; color: string; label: string };

const KIND_META: Record<string, KindMeta> = {
  invoice_synced: { icon: "🚀", color: "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300", label: "Case started" },
  outreach_sent: { icon: "📤", color: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300", label: "Outreach sent" },
  dry_run_send: { icon: "📤", color: "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300", label: "Outreach (dry run)" },
  reply_received: { icon: "💬", color: "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300", label: "Reply received" },
  contact_changed_notice: { icon: "🔀", color: "bg-violet-50 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300", label: "Contact changed" },
  critic_blocked: { icon: "🚫", color: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300", label: "Draft blocked" },
  decision: { icon: "🧠", color: "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300", label: "Decision" },
  promise_missed: { icon: "⏰", color: "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300", label: "Promise missed" },
  date_advanced: { icon: "🕒", color: "bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300", label: "Clock advanced" },
  payment_posted: { icon: "✅", color: "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300", label: "Payment posted" },
  dispute_created: { icon: "⚠️", color: "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300", label: "Dispute created" },
  note: { icon: "📝", color: "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300", label: "Note" },
  seed_loaded: { icon: "🌱", color: "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300", label: "Seeded" },
};

const DEFAULT_META: KindMeta = { icon: "•", color: "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300", label: "" };

function metaFor(kind: string): KindMeta {
  return KIND_META[kind] ?? { ...DEFAULT_META, label: kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) };
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const COLLAPSE_THRESHOLD = 220;

function MessageBubble({ text, tone = "zinc" }: { text: string; tone?: "zinc" | "sky" | "violet" }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > COLLAPSE_THRESHOLD;
  const shown = expanded || !isLong ? text : text.slice(0, COLLAPSE_THRESHOLD).trimEnd() + "…";
  const toneClass =
    tone === "sky"
      ? "bg-sky-50/60 dark:bg-sky-950/40 border-sky-100 dark:border-sky-800"
      : tone === "violet"
        ? "bg-violet-50/60 dark:bg-violet-950/40 border-violet-100 dark:border-violet-800"
        : "bg-zinc-50 dark:bg-zinc-800/40 border-zinc-100 dark:border-zinc-800";
  return (
    <div className={`mt-1.5 whitespace-pre-wrap rounded-lg border px-2.5 py-2 text-[12.5px] leading-relaxed text-zinc-700 dark:text-zinc-300 ${toneClass}`}>
      {shown}
      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="ml-1.5 text-[11px] font-medium text-zinc-400 dark:text-zinc-500 underline decoration-dotted hover:text-zinc-600 dark:hover:text-zinc-300"
        >
          {expanded ? "show less" : "show more"}
        </button>
      )}
    </div>
  );
}

function ReasoningToggle({ trace }: { trace: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const plan = (trace.plan as Record<string, unknown>) || {};
  const judgment = (trace.judgment as Record<string, unknown>) || {};
  const lines: Array<{ label: string; value: string }> = [];
  if (plan.selected_tactic) lines.push({ label: "Tactic", value: String(plan.selected_tactic) });
  if (plan.rationale) lines.push({ label: "Why", value: String(plan.rationale) });
  if (typeof judgment.passed === "boolean") lines.push({ label: "Judge", value: judgment.passed ? "Passed" : "Failed" });
  if (Array.isArray(judgment.failures) && judgment.failures.length) {
    lines.push({ label: "Failed checks", value: (judgment.failures as string[]).join(", ") });
  }
  if (judgment.notes) lines.push({ label: "Judge notes", value: String(judgment.notes) });
  if (lines.length === 0) return null;

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen((o) => !o)}
        className="text-[11px] font-medium text-zinc-400 dark:text-zinc-500 underline decoration-dotted hover:text-zinc-600 dark:hover:text-zinc-300"
      >
        {open ? "hide reasoning" : "why?"}
      </button>
      {open && (
        <div className="mt-1 space-y-1 rounded-lg border border-primary/20 bg-primary/5 px-2.5 py-2">
          {lines.map((l) => (
            <p key={l.label} className="text-[12px] leading-relaxed">
              <span className="text-zinc-500 dark:text-zinc-400">{l.label}: </span>
              <span className="italic text-zinc-700 dark:text-zinc-300">{l.value}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function EventShell({
  kind,
  at,
  isLast,
  children,
}: {
  kind: string;
  at: string | null | undefined;
  isLast: boolean;
  children: React.ReactNode;
}) {
  const meta = metaFor(kind);
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[13px] ${meta.color}`}>{meta.icon}</span>
        {!isLast && <span className="mt-1 w-px flex-1 bg-zinc-100 dark:bg-zinc-800" />}
      </div>
      <div className="min-w-0 flex-1 pb-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${meta.color}`}>{meta.label}</span>
          <span className="text-[11px] text-zinc-400 dark:text-zinc-500">{formatWhen(at)}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ChaseEventRow({
  event,
  trace,
  isLast = false,
}: {
  event: Record<string, unknown>;
  trace?: Record<string, unknown>;
  isLast?: boolean;
}) {
  const kind = String(event.kind ?? "");
  const detail = (event.detail as Record<string, unknown>) || {};
  const at = event.at as string | undefined;

  if (kind === "outreach_sent" || kind === "dry_run_send") {
    const recipient = detail.recipient ? String(detail.recipient) : null;
    const tactic = detail.tactic ? String(detail.tactic) : null;
    const body = detail.body ? String(detail.body) : null;
    const realSend = detail.real_send as Record<string, unknown> | undefined;
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-zinc-600 dark:text-zinc-300">
          {recipient && <span className="font-medium text-zinc-700 dark:text-zinc-300">&rarr; {recipient}</span>}
          {tactic && (
            <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 dark:text-zinc-400">
              {tactic.replace(/_/g, " ")}
            </span>
          )}
          {realSend && realSend.success === false && (
            <span className="rounded-full bg-rose-50 dark:bg-rose-950/40 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-300">
              real send failed
            </span>
          )}
        </div>
        {body && <MessageBubble text={body} tone="sky" />}
        {trace && <ReasoningToggle trace={trace} />}
      </EventShell>
    );
  }

  if (kind === "reply_received") {
    const text = detail.text ? String(detail.text) : null;
    const replyType = detail.reply_type ? String(detail.reply_type) : null;
    const confidence = typeof detail.confidence === "number" ? detail.confidence : null;
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-zinc-600 dark:text-zinc-300">
          {replyType && (
            <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium capitalize text-zinc-500 dark:text-zinc-400">
              classified: {replyType.replace(/_/g, " ")}
            </span>
          )}
          {confidence != null && <span className="text-zinc-400 dark:text-zinc-500">{Math.round(confidence * 100)}% confidence</span>}
        </div>
        {text && <MessageBubble text={text} />}
        {trace && <ReasoningToggle trace={trace} />}
      </EventShell>
    );
  }

  if (kind === "contact_changed_notice") {
    const fields = Array.isArray(detail.fields_changed) ? (detail.fields_changed as string[]) : [];
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        <p className="mt-1 text-[12px] text-zinc-600 dark:text-zinc-300">
          {fields.length > 0 && <span className="text-zinc-400 dark:text-zinc-500">{fields.join(", ")}: </span>}
          <span className="font-medium text-zinc-700 dark:text-zinc-300">{String(detail.old_contact ?? "")}</span>
          <span className="mx-1 text-zinc-400 dark:text-zinc-500">&rarr;</span>
          <span className="font-medium text-zinc-700 dark:text-zinc-300">{String(detail.new_contact ?? "")}</span>
        </p>
      </EventShell>
    );
  }

  if (kind === "critic_blocked") {
    const checks = Array.isArray(detail.checks) ? (detail.checks as string[]) : [];
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        {checks.length > 0 && (
          <p className="mt-1 text-[12px] text-zinc-600 dark:text-zinc-300">Failed: {checks.join(", ")}</p>
        )}
      </EventShell>
    );
  }

  if (kind === "promise_missed") {
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        {Boolean(detail.date) && <p className="mt-1 text-[12px] text-zinc-600 dark:text-zinc-300">Was due {String(detail.date)}</p>}
      </EventShell>
    );
  }

  if (kind === "payment_posted") {
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        {Boolean(detail.paid_at) && <p className="mt-1 text-[12px] text-zinc-600 dark:text-zinc-300">Paid {String(detail.paid_at)}</p>}
      </EventShell>
    );
  }

  if (kind === "dispute_created") {
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        {detail.note ? <MessageBubble text={String(detail.note)} tone="violet" /> : null}
      </EventShell>
    );
  }

  // decision / note / seed_loaded / date_advanced / anything unmapped:
  // fall back to the server-computed plain-language explanation.
  return (
    <EventShell kind={kind} at={at} isLast={isLast}>
      {typeof event.explanation === "string" && event.explanation && (
        <p className="mt-1 text-[12px] text-zinc-600 dark:text-zinc-300">{event.explanation}</p>
      )}
      {trace && <ReasoningToggle trace={trace} />}
    </EventShell>
  );
}
