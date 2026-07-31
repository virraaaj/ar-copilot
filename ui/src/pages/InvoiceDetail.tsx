import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, useSearchParams, Link } from "react-router-dom";
import {
  getInvoice,
  getInvoiceTimeline,
  addComment,
  snoozeInvoice,
  resumeInvoice,
  getFollowUpStatus,
  createFollowUp,
  cancelFollowUp,
  listAgentCases,
  type Invoice,
  type TimelineEvent,
  type FollowUpStatus,
  type AgentCase,
} from "../api";
import { useSession } from "../context/SessionContext";
import { AgentPhaseRail } from "../components/AgentPhaseRail";

function money(n: number | null): string {
  if (n === null) return "--";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function when(at: string | null): string {
  if (!at) return "Unknown time";
  const d = new Date(at);
  if (isNaN(d.getTime())) return at;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const EVENT_LABELS: Record<string, string> = {
  reply_received: "Comment",
  stage_transition: "Stage change",
  case_created: "Case opened",
  followup_sent: "Follow-up email",
};

function humanizeEventType(eventType: string | null): string {
  if (!eventType) return "Event";
  return EVENT_LABELS[eventType] ?? eventType.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

// Comments posted through this app are prefixed "[email] text" by the
// backend (see web.py's add_comment_endpoint) -- the real backend has no
// per-comment author field this app can set, since every write goes
// through one shared service account, so the prefix is the only place the
// actual commenting user's identity survives. Older comments (posted
// before this existed, or via a raw API call) won't match and just show
// as-is with no author.
const COMMENT_AUTHOR_PATTERN = /^\[([^\]]+)\]\s?(.*)$/s;

function parseCommentAuthor(text: string | null): { author: string | null; body: string } {
  if (!text) return { author: null, body: "--" };
  const match = text.match(COMMENT_AUTHOR_PATTERN);
  if (!match) return { author: null, body: text };
  return { author: match[1], body: match[2] };
}

function SnoozeModal({
  onClose,
  onConfirm,
  submitting,
  error,
}: {
  onClose: () => void;
  onConfirm: (reason: string, resumeDate: string) => void;
  submitting: boolean;
  error: string | null;
}) {
  const [reason, setReason] = useState("");
  const [resumeDate, setResumeDate] = useState("");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/30 dark:bg-black/60 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.15)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Snooze a follow-up</h2>
        <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">Pauses dunning outreach on this invoice until you resume it.</p>

        <label className="mb-1.5 mt-5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Reason</label>
        <input
          autoFocus
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. customer disputing amount"
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Resume on (optional)</label>
        <input
          type="date"
          value={resumeDate}
          onChange={(e) => setResumeDate(e.target.value)}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        {error && <p className="mb-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => onConfirm(reason, resumeDate)}
            disabled={submitting || !reason.trim()}
            className="rounded-full bg-green-700 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "Snoozing..." : "Confirm snooze"}
          </button>
          <button
            onClick={onClose}
            className="rounded-full px-4 py-2 text-[13px] font-medium text-zinc-500 dark:text-zinc-400 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function FollowUpModal({
  onClose,
  onConfirm,
  submitting,
  error,
}: {
  onClose: () => void;
  onConfirm: (email: string, cadenceDays: number, endDate: string) => void;
  submitting: boolean;
  error: string | null;
}) {
  const [email, setEmail] = useState("");
  const [cadenceDays, setCadenceDays] = useState("3");
  const [endDate, setEndDate] = useState("");
  const cadence = parseInt(cadenceDays, 10);
  const canSubmit = email.trim().length > 0 && Number.isFinite(cadence) && cadence >= 1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/30 dark:bg-black/60 px-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.15)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Follow up with the customer</h2>
        <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">
          Sends a follow-up email now, then repeats on the schedule below until you cancel it or the end date passes.
        </p>

        <label className="mb-1.5 mt-5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Customer email</label>
        <input
          autoFocus
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="customer@example.com"
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Every how many days</label>
        <input
          type="number"
          min={1}
          value={cadenceDays}
          onChange={(e) => setCadenceDays(e.target.value)}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Until (optional)</label>
        <input
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        {error && <p className="mb-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => onConfirm(email.trim(), cadence, endDate)}
            disabled={submitting || !canSubmit}
            className="rounded-full bg-green-700 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "Starting..." : "Start follow-ups"}
          </button>
          <button
            onClick={onClose}
            className="rounded-full px-4 py-2 text-[13px] font-medium text-zinc-500 dark:text-zinc-400 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
          >
            Cancel
          </button>
        </div>
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
  const { token, setPinnedInvoice, setCurrentProject } = useSession();
  const navigate = useNavigate();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [commentText, setCommentText] = useState("");
  const [posting, setPosting] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const [showSnoozeModal, setShowSnoozeModal] = useState(requestedAction === "snooze");
  const [snoozing, setSnoozing] = useState(false);
  const [snoozeError, setSnoozeError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState<FollowUpStatus | null>(null);
  const [showFollowUpModal, setShowFollowUpModal] = useState(requestedAction === "follow_up");
  const [followUpSubmitting, setFollowUpSubmitting] = useState(false);
  const [followUpError, setFollowUpError] = useState<string | null>(null);
  const [cancellingFollowUp, setCancellingFollowUp] = useState(false);
  const [agentCase, setAgentCase] = useState<AgentCase | null>(null);
  const commentInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (invoice && requestedAction === "comment") commentInputRef.current?.focus();
    // Only once the invoice (and therefore the input) has actually mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoice]);

  function loadInvoice() {
    if (!token || !invoiceId) return;
    getInvoice(token, invoiceId).then(setInvoice).catch((e) => setError(String(e)));
  }

  useEffect(loadInvoice, [token, invoiceId]);

  function loadTimeline() {
    if (!token || !invoiceId) return;
    setTimelineLoading(true);
    getInvoiceTimeline(token, invoiceId)
      .then(setTimeline)
      .catch(() => setTimeline([]))
      .finally(() => setTimelineLoading(false));
  }

  useEffect(loadTimeline, [token, invoiceId]);

  function loadFollowUp() {
    if (!token || !invoiceId) return;
    getFollowUpStatus(token, invoiceId).then(setFollowUp).catch(() => setFollowUp(null));
  }

  useEffect(loadFollowUp, [token, invoiceId]);

  useEffect(() => {
    if (!token || !invoiceId) return;
    listAgentCases(token)
      .then((cases) =>
        setAgentCase(
          cases.find((c) => c.case_id === invoiceId || c.invoice_no === invoiceId) ?? null
        )
      )
      .catch(() => setAgentCase(null));
  }, [token, invoiceId]);

  async function submitFollowUp(email: string, cadenceDays: number, endDate: string) {
    if (!token || !invoiceId) return;
    setFollowUpSubmitting(true);
    setFollowUpError(null);
    try {
      await createFollowUp(token, invoiceId, { customer_email: email, cadence_days: cadenceDays, end_date: endDate || undefined });
      setShowFollowUpModal(false);
      loadFollowUp();
    } catch (e) {
      setFollowUpError(String(e));
    } finally {
      setFollowUpSubmitting(false);
    }
  }

  async function handleCancelFollowUp() {
    if (!token || !invoiceId) return;
    setCancellingFollowUp(true);
    try {
      await cancelFollowUp(token, invoiceId);
      loadFollowUp();
    } finally {
      setCancellingFollowUp(false);
    }
  }

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

  async function submitSnooze(reason: string, resumeDate: string) {
    if (!token || !invoiceId || !reason.trim()) return;
    setSnoozing(true);
    setSnoozeError(null);
    try {
      await snoozeInvoice(token, invoiceId, reason.trim(), resumeDate || undefined);
      setShowSnoozeModal(false);
      loadInvoice(); // active_pause_id flips, which flips the button to "Resume"
      loadTimeline();
    } catch (e) {
      setSnoozeError(String(e));
    } finally {
      setSnoozing(false);
    }
  }

  async function handleResume() {
    if (!token || !invoiceId) return;
    setResuming(true);
    setResumeError(null);
    try {
      await resumeInvoice(token, invoiceId);
      loadInvoice(); // active_pause_id clears, which flips the button back to "Snooze"
      loadTimeline();
    } catch (e) {
      setResumeError(String(e));
    } finally {
      setResuming(false);
    }
  }

  function askAboutThis() {
    if (!invoice) return;
    const label = `${invoice.project_name ?? invoice.project_number ?? "Unknown project"} -- ${money(
      invoice.open_amount
    )}, ${invoice.aging_status ?? "unknown aging"}`;
    setPinnedInvoice({ invoice_id: invoice.invoice_id, label });
    // Project-scoped chat (added 2026-07-23): set the project too, so this
    // handoff skips Chat's project picker entirely.
    if (invoice.project_number) {
      setCurrentProject({ project_number: invoice.project_number, project_name: invoice.project_name });
    }
    navigate("/chat");
  }

  if (error)
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>
      </div>
    );
  if (!invoice)
    return (
      <div className="mx-auto max-w-2xl px-6 py-10">
        <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>
      </div>
    );

  const comments = timeline.filter((e) => e.event_type === "reply_received");
  // Follow-up sends are logged locally (see followup_store.py's docstring
  // for why -- the real backend's response-events endpoint hardcodes its
  // resulting timeline event to "Inbound reply received", so using it for
  // an outbound send would mislabel it). Merged in here so Activity reads
  // as one coherent timeline instead of two separate lists.
  const followUpEvents: TimelineEvent[] = (followUp?.send_history ?? []).map((s) => ({
    event_type: "followup_sent",
    title: "Follow-up email sent",
    summary: `Sent to ${s.to_email}`,
    actor: null,
    at: s.sent_at,
  }));
  const activity = [...timeline.filter((e) => e.event_type !== "reply_received"), ...followUpEvents].sort(
    (a, b) => (b.at ?? "").localeCompare(a.at ?? "")
  );
  const isSnoozed = !!invoice.active_pause_id;
  const activeCampaign = followUp?.active_campaign ?? null;

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
      <Link to="/" className="text-[13px] font-medium text-zinc-400 dark:text-zinc-500 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100">
        &larr; Back to dashboard
      </Link>

      <div className="mt-4 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-[20px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            {invoice.project_name ?? invoice.project_number ?? "Invoice"}
          </h1>
          <div className="flex gap-2">
            {isSnoozed ? (
              <button
                onClick={handleResume}
                disabled={resuming}
                className="rounded-full border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-4 py-2 text-[13px] font-medium text-amber-700 dark:text-amber-300 transition-colors hover:bg-amber-100 dark:hover:bg-amber-900/40 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {resuming ? "Resuming..." : "Resume"}
              </button>
            ) : (
              <button
                onClick={() => setShowSnoozeModal(true)}
                className="rounded-full border border-zinc-200 dark:border-zinc-700 px-4 py-2 text-[13px] font-medium text-zinc-700 dark:text-zinc-300 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
              >
                Snooze
              </button>
            )}
            {!activeCampaign && (
              <button
                onClick={() => setShowFollowUpModal(true)}
                className="rounded-full border border-zinc-200 dark:border-zinc-700 px-4 py-2 text-[13px] font-medium text-zinc-700 dark:text-zinc-300 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
              >
                Follow up
              </button>
            )}
            <button
              onClick={askAboutThis}
              className="rounded-full bg-green-700 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-green-800"
            >
              Ask about this
            </button>
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-y-4 text-[14px]">
          {fields.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">{label}</dt>
              <dd className="text-right font-medium text-zinc-800 dark:text-zinc-200">{value ?? "--"}</dd>
            </div>
          ))}
        </dl>
        {isSnoozed && (
          <div className="mt-4 rounded-lg bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-[13px] text-amber-700 dark:text-amber-300">
            Snoozed &mdash; reminders are paused until this is resumed.
          </div>
        )}
        {resumeError && <p className="mt-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{resumeError}</p>}

        {activeCampaign && (
          <div className="mt-4 flex items-center justify-between gap-3 rounded-lg bg-blue-50 px-3 py-2 text-[13px] text-blue-700">
            <span>
              Following up with {activeCampaign.customer_email} every {activeCampaign.cadence_days} day
              {activeCampaign.cadence_days === 1 ? "" : "s"}
              {activeCampaign.end_date ? ` until ${activeCampaign.end_date}` : ""}
              {activeCampaign.send_count > 0 ? ` (${activeCampaign.send_count} sent)` : ""}
            </span>
            <button
              onClick={handleCancelFollowUp}
              disabled={cancellingFollowUp}
              className="shrink-0 rounded-full border border-blue-200 px-3 py-1 text-[12px] font-medium text-blue-700 transition-colors hover:bg-blue-100 disabled:opacity-40"
            >
              {cancellingFollowUp ? "Cancelling..." : "Cancel"}
            </button>
          </div>
        )}
      </div>

      {showSnoozeModal && (
        <SnoozeModal
          onClose={() => setShowSnoozeModal(false)}
          onConfirm={submitSnooze}
          submitting={snoozing}
          error={snoozeError}
        />
      )}

      {showFollowUpModal && (
        <FollowUpModal
          onClose={() => setShowFollowUpModal(false)}
          onConfirm={submitFollowUp}
          submitting={followUpSubmitting}
          error={followUpError}
        />
      )}

      {agentCase && (
        <div className="mt-4 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
                Outcome agent
              </h2>
              <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">
                Long-horizon pursuit state for this invoice.
              </p>
            </div>
            <Link
              to={`/agent/cases/${agentCase.id}`}
              className="shrink-0 rounded-full bg-green-700 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-green-800"
            >
              Open cockpit
            </Link>
          </div>
          <div className="mt-6">
            <AgentPhaseRail state={agentCase.state} />
          </div>
        </div>
      )}

      <div className="mt-4 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Comments</h2>
        <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">
          Comments posted from Teams and the web -- both land in the same timeline.
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
            className="flex-1 rounded-full border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/40 px-4 py-2 text-[13px] text-zinc-800 dark:text-zinc-200 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300 dark:focus:border-zinc-500 focus:bg-white"
          />
          <button
            onClick={submitComment}
            disabled={posting || !commentText.trim()}
            className="rounded-full bg-green-700 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {posting ? "Posting..." : "Post"}
          </button>
        </div>
        {commentError && <p className="mt-2 text-[12px] text-rose-600 dark:text-rose-400">{commentError}</p>}

        <ul className="mt-6 space-y-4">
          {timelineLoading && <li className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</li>}
          {!timelineLoading && comments.length === 0 && (
            <li className="text-[13px] text-zinc-400 dark:text-zinc-500">No comments yet.</li>
          )}
          {!timelineLoading &&
            comments.map((event, i) => {
              const { author, body } = parseCommentAuthor(event.summary ?? event.title);
              return (
                <li key={i} className="border-l-2 border-zinc-100 dark:border-zinc-800 pl-4">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] font-semibold text-zinc-700 dark:text-zinc-300">{author ?? "Unknown user"}</span>
                    <span className="text-[11px] text-zinc-400 dark:text-zinc-500">{when(event.at)}</span>
                  </div>
                  <p className="mt-1 text-[13px] text-zinc-800 dark:text-zinc-200">{body}</p>
                </li>
              );
            })}
        </ul>
      </div>

      <div className="mt-4 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Activity</h2>
        <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">Stage changes, outreach sent, and other system events.</p>

        <ul className="mt-6 space-y-4">
          {timelineLoading && <li className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</li>}
          {!timelineLoading && activity.length === 0 && (
            <li className="text-[13px] text-zinc-400 dark:text-zinc-500">No activity yet.</li>
          )}
          {!timelineLoading &&
            activity.map((event, i) => (
              <li key={i} className="border-l-2 border-zinc-100 dark:border-zinc-800 pl-4">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                    {humanizeEventType(event.event_type)}
                  </span>
                  <span className="text-[11px] text-zinc-300 dark:text-zinc-600">{when(event.at)}</span>
                </div>
                <p className="mt-1 text-[13px] text-zinc-800 dark:text-zinc-200">{event.summary ?? event.title ?? "--"}</p>
              </li>
            ))}
        </ul>
      </div>

    </div>
  );
}
