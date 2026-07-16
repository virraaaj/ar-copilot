import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, useSearchParams, Link } from "react-router-dom";
import {
  getInvoice,
  getInvoiceTimeline,
  addComment,
  snoozeInvoice,
  getEscalationPolicy,
  type Invoice,
  type TimelineEvent,
  type EscalationStage,
} from "../api";
import { useSession } from "../context/SessionContext";

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function when(at: string | null): string {
  if (!at) return "";
  const d = new Date(at);
  if (isNaN(d.getTime())) return at;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const EVENT_LABELS: Record<string, string> = {
  reply_received: "Comment",
  stage_transition: "Stage change",
  case_created: "Case opened",
};

function StageRail({ stages, currentStageCode }: { stages: EscalationStage[]; currentStageCode: string | null }) {
  const ordered = [...stages].sort((a, b) => a.sequence_order - b.sequence_order);
  const currentIndex = ordered.findIndex((s) => s.stage_code === currentStageCode);

  return (
    <div className="mt-4 rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900">Escalation status</h2>
      {currentIndex === -1 && currentStageCode && (
        <p className="mt-1 text-[12px] text-zinc-400">Current stage: {currentStageCode}</p>
      )}
      <div className="mt-6 flex items-start">
        {ordered.map((stage, i) => {
          const isDone = currentIndex !== -1 && i < currentIndex;
          const isCurrent = i === currentIndex;
          return (
            <div key={stage.stage_id} className="flex flex-1 flex-col items-center last:flex-none last:items-end">
              <div className="flex w-full items-center">
                <div
                  className={
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold " +
                    (isCurrent
                      ? "bg-zinc-900 text-white ring-4 ring-zinc-900/10"
                      : isDone
                        ? "bg-emerald-500 text-white"
                        : "bg-zinc-100 text-zinc-400")
                  }
                >
                  {isDone ? "✓" : stage.sequence_order}
                </div>
                {i < ordered.length - 1 && (
                  <div className={"h-0.5 flex-1 " + (isDone ? "bg-emerald-500" : "bg-zinc-100")} />
                )}
              </div>
              <div className="mt-2 max-w-[84px] text-center text-[11px] leading-tight">
                <span className={isCurrent ? "font-semibold text-zinc-900" : "text-zinc-400"}>{stage.stage_name}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function InvoiceDetail() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const [searchParams] = useSearchParams();
  // Set when arriving via a Teams magic link (reminder-card button, or a
  // project chat's invoice picker) -- opens the matching form directly
  // instead of making the user hunt for it. Added 2026-07-16.
  const requestedAction = searchParams.get("action");
  const { token, setPinnedInvoice } = useSession();
  const navigate = useNavigate();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [commentText, setCommentText] = useState("");
  const [posting, setPosting] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const [showSnoozeForm, setShowSnoozeForm] = useState(requestedAction === "snooze");
  const [snoozeReason, setSnoozeReason] = useState("");
  const [snoozeResumeDate, setSnoozeResumeDate] = useState("");
  const [snoozing, setSnoozing] = useState(false);
  const [snoozeError, setSnoozeError] = useState<string | null>(null);
  const [snoozeConfirmed, setSnoozeConfirmed] = useState<string | null>(null);
  const [stages, setStages] = useState<EscalationStage[] | null>(null);
  const commentInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!token) return;
    // Reuses the same global escalation policy the Escalation Policy page
    // edits -- the rail always reflects the real configured stage order,
    // not a hardcoded guess that could drift from it.
    getEscalationPolicy(token)
      .then((p) => setStages(p.stages))
      .catch(() => setStages(null));
  }, [token]);

  useEffect(() => {
    if (invoice && requestedAction === "comment") commentInputRef.current?.focus();
    // Only once the invoice (and therefore the input) has actually mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoice]);

  useEffect(() => {
    if (!token || !invoiceId) return;
    getInvoice(token, invoiceId).then(setInvoice).catch((e) => setError(String(e)));
  }, [token, invoiceId]);

  function loadTimeline() {
    if (!token || !invoiceId) return;
    setTimelineLoading(true);
    getInvoiceTimeline(token, invoiceId)
      .then(setTimeline)
      .catch(() => setTimeline([]))
      .finally(() => setTimelineLoading(false));
  }

  useEffect(loadTimeline, [token, invoiceId]);

  async function submitComment() {
    if (!token || !invoiceId || !commentText.trim()) return;
    setPosting(true);
    setCommentError(null);
    try {
      await addComment(token, invoiceId, commentText.trim());
      setCommentText("");
      loadTimeline();
    } catch (e) {
      setCommentError(String(e));
    } finally {
      setPosting(false);
    }
  }

  async function submitSnooze() {
    if (!token || !invoiceId || !snoozeReason.trim()) return;
    setSnoozing(true);
    setSnoozeError(null);
    try {
      await snoozeInvoice(token, invoiceId, snoozeReason.trim(), snoozeResumeDate || undefined);
      setSnoozeConfirmed(snoozeReason.trim());
      setSnoozeReason("");
      setSnoozeResumeDate("");
      setShowSnoozeForm(false);
    } catch (e) {
      setSnoozeError(String(e));
    } finally {
      setSnoozing(false);
    }
  }

  function askAboutThis() {
    if (!invoice) return;
    const label = `${invoice.project_name ?? invoice.project_number ?? "Unknown project"} -- ${money(
      invoice.open_amount
    )}, ${invoice.aging_status ?? "unknown aging"}`;
    setPinnedInvoice({ invoice_id: invoice.invoice_id, label });
    navigate("/chat");
  }

  if (error)
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
      </div>
    );
  if (!invoice)
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="text-[13px] text-zinc-400">Loading...</p>
      </div>
    );

  const fields: [string, string | number | null][] = [
    ["Case key", invoice.case_key],
    ["Business unit", invoice.business_unit_id],
    ["Stage", invoice.stage],
    ["Status", invoice.status],
    ["Due date", invoice.due_date],
    ["Aging status", invoice.aging_status],
    ["Open amount", money(invoice.open_amount)],
  ];

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <Link to="/" className="text-[13px] font-medium text-zinc-400 transition-colors hover:text-zinc-900">
        &larr; Back to dashboard
      </Link>

      <div className="mt-4 rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-[20px] font-semibold tracking-tight text-zinc-900">
            {invoice.project_name ?? invoice.project_number ?? "Invoice"}
          </h1>
          <div className="flex gap-2">
            <button
              onClick={() => setShowSnoozeForm((v) => !v)}
              className="rounded-full border border-zinc-200 px-4 py-2 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50"
            >
              Snooze
            </button>
            <button
              onClick={askAboutThis}
              className="rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800"
            >
              Ask about this
            </button>
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-y-4 text-[14px]">
          {fields.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">{label}</dt>
              <dd className="text-right font-medium text-zinc-800">{value ?? "--"}</dd>
            </div>
          ))}
        </dl>
      </div>

      {snoozeConfirmed && (
        <div className="mt-4 rounded-2xl border border-emerald-200/70 bg-emerald-50 px-5 py-3 text-[13px] text-emerald-700">
          Snoozed &mdash; "{snoozeConfirmed}"
        </div>
      )}

      {showSnoozeForm && (
        <div className="mt-4 rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900">Snooze a follow-up</h2>
          <p className="mt-1 text-[12px] text-zinc-400">Pauses dunning outreach on this invoice until you resume it.</p>

          <label className="mb-1.5 mt-5 block text-[13px] font-medium text-zinc-600">Reason</label>
          <input
            value={snoozeReason}
            onChange={(e) => setSnoozeReason(e.target.value)}
            placeholder="e.g. customer disputing amount"
            className="mb-4 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
          />
          <label className="mb-1.5 block text-[13px] font-medium text-zinc-600">Resume on (optional)</label>
          <input
            type="date"
            value={snoozeResumeDate}
            onChange={(e) => setSnoozeResumeDate(e.target.value)}
            className="mb-4 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
          />
          {snoozeError && <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{snoozeError}</p>}
          <div className="flex gap-2">
            <button
              onClick={submitSnooze}
              disabled={snoozing || !snoozeReason.trim()}
              className="rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {snoozing ? "Snoozing..." : "Confirm snooze"}
            </button>
            <button
              onClick={() => setShowSnoozeForm(false)}
              className="rounded-full px-4 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {stages && stages.length > 0 && <StageRail stages={stages} currentStageCode={invoice.stage} />}

      <div className="mt-4 rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900">Comments &amp; activity</h2>
        <p className="mt-1 text-[12px] text-zinc-400">
          Includes comments posted from Teams and the web -- both land in the same timeline.
        </p>

        <div className="mt-5 flex gap-2">
          <input
            ref={commentInputRef}
            value={commentText}
            onChange={(e) => setCommentText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !posting) submitComment();
            }}
            placeholder="Add a comment..."
            className="flex-1 rounded-full border border-zinc-200 bg-zinc-50 px-4 py-2 text-[13px] text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300 focus:bg-white"
          />
          <button
            onClick={submitComment}
            disabled={posting || !commentText.trim()}
            className="rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {posting ? "Posting..." : "Post"}
          </button>
        </div>
        {commentError && <p className="mt-2 text-[12px] text-rose-600">{commentError}</p>}

        <ul className="mt-6 space-y-4">
          {timelineLoading && <li className="text-[13px] text-zinc-400">Loading...</li>}
          {!timelineLoading && timeline.length === 0 && (
            <li className="text-[13px] text-zinc-400">No activity yet.</li>
          )}
          {!timelineLoading &&
            timeline.map((event, i) => (
              <li key={i} className="border-l-2 border-zinc-100 pl-4">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">
                    {EVENT_LABELS[event.event_type ?? ""] ?? event.event_type ?? "Event"}
                  </span>
                  <span className="text-[11px] text-zinc-300">{when(event.at)}</span>
                </div>
                <p className="mt-1 text-[13px] text-zinc-800">{event.summary ?? event.title ?? "--"}</p>
              </li>
            ))}
        </ul>
      </div>
    </div>
  );
}
