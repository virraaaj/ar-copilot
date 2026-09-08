// Icon-based event timeline for the Chases page (ported design from
// master's ChaseEventRow.tsx 2026-08-06, adapted to this branch's actual
// oa_events kind vocabulary -- invoice_synced/outreach_sent/reply_received/
// contact_changed_notice/critic_blocked/decision/promise_missed/
// date_advanced/payment_posted/dispute_created/note, not master's
// chase_machine-driven kinds. Each row also carries "reasoning" pulled
// from the matching oa_decision_traces entry (same rationale/judgment
// data Trace Studio's Reasoning panel shows) so the Chases tab doesn't
// need Trace Studio open to see *why* a step happened, not just that it did.
//
// Ships two visual tones behind an explicit `tone` prop:
//  - "legacy" (default): the original rainbow palette. Used only by
//    Trace Studio (AgentCockpit.tsx, inside .lummus-shell).
//  - "bold": the restrained Bold Typography palette (see ui/src/index.css
//    @theme + ui/src/components/ui/Badge.tsx). All in-scope callers
//    (Chases.tsx, GuidedDemo.tsx) pass tone="bold".
import { useState, type ReactNode } from "react";
import { formatTimestamp } from "../utils/format";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeftRight,
  Ban,
  Brain,
  CheckCircle2,
  Circle,
  Clock,
  MessageSquare,
  Rocket,
  Send,
  Sprout,
  StickyNote,
  type LucideIcon,
} from "lucide-react";
import { Badge, type BadgeTone } from "./ui/Badge";

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
  const candidates = Array.isArray(trace.candidates) ? (trace.candidates as Array<Record<string, unknown>>) : [];
  const lines: Array<{ label: string; value: string }> = [];
  if (plan.selected_tactic) lines.push({ label: "Tactic", value: String(plan.selected_tactic) });
  if (plan.rationale) lines.push({ label: "Why", value: String(plan.rationale) });
  if (typeof judgment.passed === "boolean") lines.push({ label: "Judge", value: judgment.passed ? "Passed" : "Failed" });
  if (Array.isArray(judgment.failures) && judgment.failures.length) {
    lines.push({ label: "Failed checks", value: (judgment.failures as string[]).join(", ") });
  }
  if (judgment.notes) lines.push({ label: "Judge notes", value: String(judgment.notes) });
  if (lines.length === 0 && candidates.length === 0) return null;

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
          {candidates.length > 0 && (
            <div className="pt-1">
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Considered {candidates.length} option{candidates.length === 1 ? "" : "s"}:
              </p>
              <ul className="mt-0.5 space-y-0.5">
                {[...candidates]
                  .sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0))
                  .map((c, i) => {
                    const isSelected = c.tactic === plan.selected_tactic;
                    return (
                      <li
                        key={i}
                        className={`text-[11.5px] leading-relaxed ${isSelected ? "font-medium text-zinc-700 dark:text-zinc-200" : "text-zinc-500 dark:text-zinc-400"}`}
                      >
                        {isSelected ? "✓ " : "· "}
                        {String(c.tactic ?? "").replace(/_/g, " ")}
                        {typeof c.score === "number" && <span className="tabular-nums"> ({c.score.toFixed(2)})</span>}
                      </li>
                    );
                  })}
              </ul>
            </div>
          )}
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
          <span className="text-[11px] text-zinc-400 dark:text-zinc-500">{formatTimestamp(at)}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

function LegacyChaseEventRow({
  event,
  trace,
  isLast,
}: {
  event: Record<string, unknown>;
  trace?: Record<string, unknown>;
  isLast: boolean;
}) {
  const kind = String(event.kind ?? "");
  const detail = (event.detail as Record<string, unknown>) || {};
  const at = event.at as string | undefined;

  if (kind === "outreach_sent" || kind === "dry_run_send") {
    const recipient = detail.recipient ? String(detail.recipient) : null;
    const tactic = detail.tactic ? String(detail.tactic) : null;
    const body = detail.body ? String(detail.body) : null;
    const realSend = detail.real_send as Record<string, unknown> | undefined;
    const humanReview = detail.human_review === true;
    const fallbackReason = detail.fallback_reason ? String(detail.fallback_reason) : null;
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
          {humanReview && (
            <span
              title={fallbackReason ?? undefined}
              className="rounded-full bg-amber-50 dark:bg-amber-950/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300"
            >
              safe fallback — needs review
            </span>
          )}
        </div>
        {body && <MessageBubble text={body} tone="sky" />}
        {humanReview && fallbackReason && (
          <p className="mt-1 text-[11.5px] italic text-amber-600 dark:text-amber-400">
            AI draft blocked: {fallbackReason}
          </p>
        )}
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
    const notes = detail.notes ? String(detail.notes) : null;
    return (
      <EventShell kind={kind} at={at} isLast={isLast}>
        {checks.length > 0 && (
          <p className="mt-1 text-[12px] text-zinc-600 dark:text-zinc-300">Failed: {checks.join(", ")}</p>
        )}
        {notes && <MessageBubble text={notes} />}
        <p className="mt-1 text-[11px] text-zinc-400 dark:text-zinc-500">
          A safe template was sent instead — see the next event.
        </p>
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

// ---------------------------------------------------------------------
// Bold Typography tone
// ---------------------------------------------------------------------

// Colour policy mirrors AgentPhaseRail's and Chases.tsx's STATE_TONE: the
// accent (vermillion, "critical") is reserved for events that represent a
// genuine failure or a problem needing a human -- a blocked/unsafe draft,
// or a dispute being opened. A missed commitment gets the same amber
// "warning" tone the state badges use for promise_missed. A posted
// payment is good news ("positive", same as STATE_TONE.paid). Every other
// event -- sends, replies, notices, decisions, notes -- is routine
// process and stays neutral grey. Every event always carries its label as
// mono uppercase text plus an icon, so colour is never the only signal.
type BoldKindMeta = { Icon: LucideIcon; tone: BadgeTone; label: string };

const BOLD_KIND_META: Record<string, BoldKindMeta> = {
  invoice_synced: { Icon: Rocket, tone: "neutral", label: "Case started" },
  outreach_sent: { Icon: Send, tone: "neutral", label: "Outreach sent" },
  dry_run_send: { Icon: Send, tone: "neutral", label: "Outreach (dry run)" },
  reply_received: { Icon: MessageSquare, tone: "neutral", label: "Reply received" },
  contact_changed_notice: { Icon: ArrowLeftRight, tone: "neutral", label: "Contact changed" },
  critic_blocked: { Icon: Ban, tone: "critical", label: "Draft blocked" },
  decision: { Icon: Brain, tone: "neutral", label: "Decision" },
  promise_missed: { Icon: AlertCircle, tone: "warning", label: "Promise missed" },
  date_advanced: { Icon: Clock, tone: "neutral", label: "Clock advanced" },
  payment_posted: { Icon: CheckCircle2, tone: "positive", label: "Payment posted" },
  dispute_created: { Icon: AlertTriangle, tone: "critical", label: "Dispute created" },
  note: { Icon: StickyNote, tone: "neutral", label: "Note" },
  seed_loaded: { Icon: Sprout, tone: "neutral", label: "Seeded" },
};

const BOLD_DEFAULT_META: Omit<BoldKindMeta, "label"> = { Icon: Circle, tone: "neutral" };

function boldMetaFor(kind: string): BoldKindMeta {
  return (
    BOLD_KIND_META[kind] ?? {
      ...BOLD_DEFAULT_META,
      label: kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    }
  );
}

const TONE_BORDER_TEXT: Record<BadgeTone, string> = {
  neutral: "border-border text-muted-foreground",
  positive: "border-[#2f8f4e] text-[#4ade80]",
  warning: "border-[#a3660a] text-[#facc15]",
  critical: "border-accent text-accent",
  accent: "border-accent text-accent",
};

function BoldMessageBubble({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > COLLAPSE_THRESHOLD;
  const shown = expanded || !isLong ? text : text.slice(0, COLLAPSE_THRESHOLD).trimEnd() + "…";
  return (
    <div className="mt-2 whitespace-pre-wrap border border-border bg-card px-3 py-2 text-sm leading-relaxed text-foreground">
      {shown}
      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="ml-2 font-mono text-[11px] font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

function BoldReasoningToggle({ trace }: { trace: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const plan = (trace.plan as Record<string, unknown>) || {};
  const judgment = (trace.judgment as Record<string, unknown>) || {};
  const candidates = Array.isArray(trace.candidates) ? (trace.candidates as Array<Record<string, unknown>>) : [];
  const lines: Array<{ label: string; value: string }> = [];
  if (plan.selected_tactic) lines.push({ label: "Tactic", value: String(plan.selected_tactic) });
  if (plan.rationale) lines.push({ label: "Why", value: String(plan.rationale) });
  if (typeof judgment.passed === "boolean") lines.push({ label: "Judge", value: judgment.passed ? "Passed" : "Failed" });
  if (Array.isArray(judgment.failures) && judgment.failures.length) {
    lines.push({ label: "Failed checks", value: (judgment.failures as string[]).join(", ") });
  }
  if (judgment.notes) lines.push({ label: "Judge notes", value: String(judgment.notes) });
  if (lines.length === 0 && candidates.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="font-mono text-[11px] font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground"
      >
        {open ? "Hide reasoning" : "Why?"}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5 border border-border bg-card px-3 py-2.5">
          {lines.map((l) => (
            <p key={l.label} className="text-[12.5px] leading-relaxed text-foreground">
              <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{l.label}: </span>
              <span>{l.value}</span>
            </p>
          ))}
          {candidates.length > 0 && (
            <div className="pt-1">
              <p className="font-mono text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Considered {candidates.length} option{candidates.length === 1 ? "" : "s"}
              </p>
              <ul className="mt-1 space-y-0.5">
                {[...candidates]
                  .sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0))
                  .map((c, i) => {
                    const isSelected = c.tactic === plan.selected_tactic;
                    return (
                      <li
                        key={i}
                        className={`text-[12px] leading-relaxed ${isSelected ? "font-medium text-foreground" : "text-muted-foreground"}`}
                      >
                        {isSelected ? "✓ " : "· "}
                        {String(c.tactic ?? "").replace(/_/g, " ")}
                        {typeof c.score === "number" && <span className="tabular-nums"> ({c.score.toFixed(2)})</span>}
                      </li>
                    );
                  })}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BoldEventShell({
  kind,
  at,
  isLast,
  children,
}: {
  kind: string;
  at: string | null | undefined;
  isLast: boolean;
  children: ReactNode;
}) {
  const meta = boldMetaFor(kind);
  const { Icon } = meta;
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center border bg-current/5 ${TONE_BORDER_TEXT[meta.tone]}`}>
          <Icon size={14} strokeWidth={1.5} />
        </span>
        {!isLast && <span className="mt-1 w-px flex-1 bg-border" />}
      </div>
      <div className="min-w-0 flex-1 pb-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
          <Badge tone={meta.tone}>{meta.label}</Badge>
          <span className="font-mono text-[11px] text-muted-foreground">{formatTimestamp(at)}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

function BoldChaseEventRow({
  event,
  trace,
  isLast,
}: {
  event: Record<string, unknown>;
  trace?: Record<string, unknown>;
  isLast: boolean;
}) {
  const kind = String(event.kind ?? "");
  const detail = (event.detail as Record<string, unknown>) || {};
  const at = event.at as string | undefined;

  if (kind === "outreach_sent" || kind === "dry_run_send") {
    const recipient = detail.recipient ? String(detail.recipient) : null;
    const tactic = detail.tactic ? String(detail.tactic) : null;
    const body = detail.body ? String(detail.body) : null;
    const realSend = detail.real_send as Record<string, unknown> | undefined;
    const humanReview = detail.human_review === true;
    const fallbackReason = detail.fallback_reason ? String(detail.fallback_reason) : null;
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[13px] text-foreground">
          {recipient && <span className="font-medium">&rarr; {recipient}</span>}
          {tactic && <Badge tone="neutral">{tactic.replace(/_/g, " ")}</Badge>}
          {realSend && realSend.success === false && (
            <Badge tone="critical" icon={<AlertTriangle size={12} strokeWidth={1.5} />}>
              Real send failed
            </Badge>
          )}
          {humanReview && (
            <span title={fallbackReason ?? undefined}>
              <Badge tone="warning" icon={<AlertCircle size={12} strokeWidth={1.5} />}>
                Safe fallback — needs review
              </Badge>
            </span>
          )}
        </div>
        {body && <BoldMessageBubble text={body} />}
        {humanReview && fallbackReason && (
          <p className="mt-1.5 text-[12.5px] text-[#facc15]">AI draft blocked: {fallbackReason}</p>
        )}
        {trace && <BoldReasoningToggle trace={trace} />}
      </BoldEventShell>
    );
  }

  if (kind === "reply_received") {
    const text = detail.text ? String(detail.text) : null;
    const replyType = detail.reply_type ? String(detail.reply_type) : null;
    const confidence = typeof detail.confidence === "number" ? detail.confidence : null;
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[13px] text-muted-foreground">
          {replyType && <Badge tone="neutral">Classified: {replyType.replace(/_/g, " ")}</Badge>}
          {confidence != null && <span className="font-mono text-[11px]">{Math.round(confidence * 100)}% confidence</span>}
        </div>
        {text && <BoldMessageBubble text={text} />}
        {trace && <BoldReasoningToggle trace={trace} />}
      </BoldEventShell>
    );
  }

  if (kind === "contact_changed_notice") {
    const fields = Array.isArray(detail.fields_changed) ? (detail.fields_changed as string[]) : [];
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        <p className="mt-1.5 text-[13px] text-foreground">
          {fields.length > 0 && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{fields.join(", ")}: </span>
          )}
          <span className="font-medium">{String(detail.old_contact ?? "")}</span>
          <span className="mx-1.5 text-muted-foreground">&rarr;</span>
          <span className="font-medium">{String(detail.new_contact ?? "")}</span>
        </p>
      </BoldEventShell>
    );
  }

  if (kind === "critic_blocked") {
    const checks = Array.isArray(detail.checks) ? (detail.checks as string[]) : [];
    const notes = detail.notes ? String(detail.notes) : null;
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        {checks.length > 0 && <p className="mt-1.5 text-[13px] text-foreground">Failed: {checks.join(", ")}</p>}
        {notes && <BoldMessageBubble text={notes} />}
        <p className="mt-1.5 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          A safe template was sent instead — see the next event.
        </p>
      </BoldEventShell>
    );
  }

  if (kind === "promise_missed") {
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        {Boolean(detail.date) && <p className="mt-1.5 text-[13px] text-foreground">Was due {String(detail.date)}</p>}
      </BoldEventShell>
    );
  }

  if (kind === "payment_posted") {
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        {Boolean(detail.paid_at) && <p className="mt-1.5 text-[13px] text-foreground">Paid {String(detail.paid_at)}</p>}
      </BoldEventShell>
    );
  }

  if (kind === "dispute_created") {
    return (
      <BoldEventShell kind={kind} at={at} isLast={isLast}>
        {detail.note ? <BoldMessageBubble text={String(detail.note)} /> : null}
      </BoldEventShell>
    );
  }

  // decision / note / seed_loaded / date_advanced / anything unmapped:
  // fall back to the server-computed plain-language explanation.
  return (
    <BoldEventShell kind={kind} at={at} isLast={isLast}>
      {typeof event.explanation === "string" && event.explanation && (
        <p className="mt-1.5 text-[13px] text-foreground">{event.explanation}</p>
      )}
      {trace && <BoldReasoningToggle trace={trace} />}
    </BoldEventShell>
  );
}

export function ChaseEventRow({
  event,
  trace,
  isLast = false,
  tone = "legacy",
}: {
  event: Record<string, unknown>;
  trace?: Record<string, unknown>;
  isLast?: boolean;
  tone?: "legacy" | "bold";
}) {
  if (tone === "bold") {
    return <BoldChaseEventRow event={event} trace={trace} isLast={isLast} />;
  }
  return <LegacyChaseEventRow event={event} trace={trace} isLast={isLast} />;
}
