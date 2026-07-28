// Shared chase-event rendering (factored out of Chases.tsx 2026-07-23 so
// InvoiceDetail's new "agent communication" section and the Chases tab
// render the same event log the same way instead of two implementations
// drifting apart). Takes the owning chase so outreach rows can resolve
// "who -> who" from chase.pm_email/customer_email + event.detail.target
// (chase_engine.py only logs the target *role*, not a literal address).
import { useState } from "react";
import type { Chase, ChaseEvent } from "../api";

function formatWhen(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const TRAJECTORY_VERDICT_STYLES: Record<string, string> = {
  progressing: "bg-emerald-50 text-emerald-700",
  stalling: "bg-amber-50 text-amber-700",
  concerning: "bg-rose-50 text-rose-700",
};

function humanizeRole(role: string): string {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function targetLabel(chase: Chase, target: unknown): string | null {
  if (typeof target !== "string" || !target) return null;
  if (target === "pm") return `PM${chase.pm_email ? ` (${chase.pm_email})` : ""}`;
  if (target === "customer") return `Customer${chase.customer_email ? ` (${chase.customer_email})` : ""}`;
  // Any other project-contact role (bu_finance, general_manager, ...) --
  // added 2026-07-23 for handoff_to_contact, resolved via chase.contact_email.
  return `${humanizeRole(target)}${chase.contact_email ? ` (${chase.contact_email})` : ""}`;
}

const CHANNEL_LABELS: Record<string, string> = {
  teams: "via Teams",
  email: "via email",
  email_failed: "via email (failed)",
  dry_run: "dry run",
  blocked: "blocked by policy",
};

// Per-kind icon/color/label -- added 2026-07-24 (was a flat wall of text
// that read as one undifferentiated stream; this gives each kind its own
// visual identity so the shape of a chase's history is scannable at a
// glance instead of requiring reading every line).
type KindMeta = { icon: string; color: string; ring: string; label: string };

const KIND_META: Record<string, KindMeta> = {
  created: { icon: "🚀", color: "bg-slate-100 text-slate-600", ring: "ring-slate-200", label: "Started" },
  action_decided: { icon: "🧠", color: "bg-slate-100 text-slate-600", ring: "ring-slate-200", label: "Decided" },
  outreach_sent: { icon: "📤", color: "bg-sky-50 text-sky-700", ring: "ring-sky-200", label: "Outreach sent" },
  dry_run_send: { icon: "📤", color: "bg-sky-50 text-sky-700", ring: "ring-sky-200", label: "Outreach (dry run)" },
  reply_received: { icon: "💬", color: "bg-zinc-100 text-zinc-700", ring: "ring-zinc-200", label: "Reply received" },
  reply_parsed: { icon: "🔍", color: "bg-zinc-100 text-zinc-700", ring: "ring-zinc-200", label: "Reply parsed" },
  clarify_requested: { icon: "❓", color: "bg-amber-50 text-amber-700", ring: "ring-amber-200", label: "Clarifying" },
  commitment_tracked: { icon: "📅", color: "bg-emerald-50 text-emerald-700", ring: "ring-emerald-200", label: "Payment date tracked" },
  commitment_missed: { icon: "⏰", color: "bg-amber-50 text-amber-700", ring: "ring-amber-200", label: "Commitment missed" },
  handoff_to_customer: { icon: "🔀", color: "bg-violet-50 text-violet-700", ring: "ring-violet-200", label: "Handed off to customer" },
  handoff_to_contact: { icon: "🔀", color: "bg-violet-50 text-violet-700", ring: "ring-violet-200", label: "Handed off" },
  checkback_scheduled: { icon: "🕒", color: "bg-indigo-50 text-indigo-700", ring: "ring-indigo-200", label: "Checking back later" },
  blocker_reported: { icon: "🚧", color: "bg-amber-50 text-amber-700", ring: "ring-amber-200", label: "Blocker reported" },
  blocker_check_in: { icon: "🚧", color: "bg-amber-50 text-amber-700", ring: "ring-amber-200", label: "Blocker check-in" },
  escalated: { icon: "⚠️", color: "bg-rose-50 text-rose-700", ring: "ring-rose-200", label: "Escalated" },
  closed: { icon: "✅", color: "bg-emerald-50 text-emerald-700", ring: "ring-emerald-200", label: "Closed" },
  human_action: { icon: "🖐️", color: "bg-indigo-50 text-indigo-700", ring: "ring-indigo-200", label: "Human action" },
  out_of_office_detected: { icon: "🌴", color: "bg-zinc-100 text-zinc-600", ring: "ring-zinc-200", label: "Out of office" },
  suppressed: { icon: "🔕", color: "bg-zinc-100 text-zinc-600", ring: "ring-zinc-200", label: "Unsubscribed" },
};

const DEFAULT_META: KindMeta = { icon: "•", color: "bg-zinc-100 text-zinc-600", ring: "ring-zinc-200", label: "" };

function metaFor(kind: string): KindMeta {
  const found = KIND_META[kind];
  if (found) return found;
  return { ...DEFAULT_META, label: kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) };
}

// Long quoted-reply text (mobile clients auto-append the whole prior
// thread) made every reply row balloon to a wall of text -- collapse
// anything past a couple lines behind a toggle instead of always showing
// the raw quote.
const COLLAPSE_THRESHOLD = 180;

function MessageBubble({ text, tone = "zinc" }: { text: string; tone?: "zinc" | "sky" | "violet" }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > COLLAPSE_THRESHOLD;
  const shown = expanded || !isLong ? text : text.slice(0, COLLAPSE_THRESHOLD).trimEnd() + "…";
  const toneClass =
    tone === "sky" ? "bg-sky-50/60 border-sky-100" : tone === "violet" ? "bg-violet-50/60 border-violet-100" : "bg-zinc-50 border-zinc-100";
  return (
    <div className={`mt-1.5 rounded-lg border px-2.5 py-2 text-[12.5px] leading-relaxed text-zinc-700 ${toneClass}`}>
      {shown}
      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="ml-1.5 text-[11px] font-medium text-zinc-400 underline decoration-dotted hover:text-zinc-600"
        >
          {expanded ? "show less" : "show more"}
        </button>
      )}
    </div>
  );
}

function WhyExplanation({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="text-[11px] font-medium text-zinc-400 underline decoration-dotted hover:text-zinc-600"
      >
        {open ? "hide why" : "why?"}
      </button>
      {open && <p className="mt-1 text-[12px] italic leading-relaxed text-zinc-500">{text}</p>}
    </div>
  );
}

function EventShell({
  kind, at, explanation, children,
}: {
  kind: string; at: string | null; explanation?: string | null; children: React.ReactNode;
}) {
  const meta = metaFor(kind);
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[13px] ring-4 ${meta.color} ${meta.ring}`}>
          {meta.icon}
        </span>
        <span className="mt-1 w-px flex-1 bg-zinc-100" />
      </div>
      <div className="min-w-0 flex-1 pb-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${meta.color}`}>
            {meta.label}
          </span>
          <span className="text-[11px] text-zinc-400">{formatWhen(at)}</span>
        </div>
        {children}
        {explanation && <WhyExplanation text={explanation} />}
      </div>
    </div>
  );
}

export function ChaseEventRow({ chase, event }: { chase: Chase; event: ChaseEvent }) {
  if (event.kind === "trajectory_assessed") {
    const verdict = String(event.detail?.verdict ?? "");
    return (
      <EventShell kind="trajectory_assessed" at={event.at} explanation={event.explanation}>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="text-[12px] text-zinc-500">🧭 AI trajectory check:</span>
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
              TRAJECTORY_VERDICT_STYLES[verdict] ?? "bg-zinc-100 text-zinc-600"
            }`}
          >
            {verdict || "unknown"}
          </span>
        </div>
        {event.detail?.reason ? <MessageBubble text={String(event.detail.reason)} /> : null}
      </EventShell>
    );
  }

  if (event.kind === "checkback_scheduled") {
    const followupDate = event.detail?.followup_date ? String(event.detail.followup_date) : null;
    const postponeCount = event.detail?.postpone_count;
    return (
      <EventShell kind="checkback_scheduled" at={event.at} explanation={event.explanation}>
        <p className="mt-1 text-[12px] text-zinc-600">
          {followupDate ? (
            <>
              Following up again on <span className="font-medium text-zinc-700">{followupDate}</span>
            </>
          ) : (
            "Asked when a good time to follow up would be"
          )}
          {typeof postponeCount === "number" && (
            <span className="ml-1.5 text-zinc-400">
              ({postponeCount} check-in{postponeCount === 1 ? "" : "s"} so far)
            </span>
          )}
        </p>
      </EventShell>
    );
  }

  if (event.kind === "blocker_reported") {
    const blockerType = event.detail?.blocker_type ? String(event.detail.blocker_type).replace(/_/g, " ") : "blocker";
    const description = event.detail?.blocker_description ? String(event.detail.blocker_description) : null;
    const resolutionDate = event.detail?.blocker_resolution_date ? String(event.detail.blocker_resolution_date) : null;
    return (
      <EventShell kind="blocker_reported" at={event.at} explanation={event.explanation}>
        <p className="mt-1 text-[12px] text-zinc-600">
          <span className="font-medium capitalize text-zinc-700">{blockerType}</span>
          {resolutionDate ? (
            <>
              {" "}-- expected to clear <span className="font-medium text-zinc-700">{resolutionDate}</span>
            </>
          ) : (
            " -- no resolution date yet"
          )}
        </p>
        {description && <MessageBubble text={description} tone="violet" />}
      </EventShell>
    );
  }

  const isOutreach = event.kind === "outreach_sent" || event.kind === "dry_run_send";
  const composed = isOutreach && event.detail?.composed === true;
  const who = isOutreach ? targetLabel(chase, event.detail?.target) : null;
  const channel = isOutreach ? CHANNEL_LABELS[String(event.detail?.channel ?? "")] : null;
  const bodyText = event.detail?.text ? String(event.detail.text) : event.detail?.reason ? String(event.detail.reason) : null;
  const sentiment = event.kind === "reply_received" ? (event.detail?.sentiment as string | undefined) : undefined;
  const needsReview = event.kind === "reply_received" && event.detail?.requires_human_review === true;

  return (
    <EventShell kind={event.kind} at={event.at} explanation={event.explanation}>
      {(who || channel || composed || sentiment || needsReview) && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-zinc-600">
          {who && (
            <span className="font-medium text-zinc-700">
              &rarr; {who}
            </span>
          )}
          {channel && <span className="text-zinc-400">{channel}</span>}
          {composed && (
            <span className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
              ✨ AI-composed
            </span>
          )}
          {sentiment && (sentiment === "angry" || sentiment === "frustrated") && (
            <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium capitalize text-amber-700">
              {sentiment}
            </span>
          )}
          {needsReview && (
            <span className="rounded-full bg-rose-50 px-1.5 py-0.5 text-[10px] font-medium text-rose-700">
              ⚑ needs review
            </span>
          )}
        </div>
      )}
      {bodyText && <MessageBubble text={bodyText} tone={isOutreach ? "sky" : "zinc"} />}
    </EventShell>
  );
}
