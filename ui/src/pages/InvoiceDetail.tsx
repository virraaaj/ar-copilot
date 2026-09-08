import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, useSearchParams, Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
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
  ApiError,
  type Invoice,
  type TimelineEvent,
  type FollowUpStatus,
  type AgentCase,
} from "../api";
import { useSession } from "../context/SessionContext";
import { AgentPhaseRail } from "../components/AgentPhaseRail";
import { Button, buttonVariants } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge, type BadgeTone } from "../components/ui/Badge";
import { ErrorState, describeError } from "../components/ui/ErrorState";

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

const STATUS_TONE: Record<string, BadgeTone> = {
  active: "positive",
  closed_paid: "neutral",
  closed_other: "neutral",
  snoozed: "warning",
};

// Distinguishes "this invoice genuinely doesn't exist" from "the backend
// is unreachable/erroring", per the failure policy -- those read very
// differently to a user, and a retry only makes sense for the latter.
// getInvoice (app/agent/tools_read.py) tries a case lookup, falls back to
// the raw invoice table, and only lets a BackendError through once *both*
// have failed -- the local API wraps that BackendError as a 422 whose
// message embeds the upstream status, e.g. "Get invoice (raw) failed
// (404): ...". A bare id that simply doesn't exist surfaces that way; the
// live-audit case (the backend's own login to the Lummus sandbox
// rejecting every request) surfaces as "Backend login failed (401)."
// instead, which is a systemic failure, not a missing invoice.
function classifyLoadError(err: unknown): "not-found" | "failed" {
  if (err instanceof ApiError && /\(404\)/.test(err.message) && !/login failed/i.test(err.message)) {
    return "not-found";
  }
  return "failed";
}

function ModalShell({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 px-4" onClick={onClose}>
      <div
        className="w-full max-w-md border border-border bg-card p-7"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
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
    <ModalShell onClose={onClose}>
      <h2 className="font-display text-xl font-semibold tracking-tight text-foreground">Snooze a follow-up</h2>
      <p className="mt-1 text-sm text-muted-foreground">Pauses dunning outreach on this invoice until you resume it.</p>

      <Eyebrow as="p" className="mb-1.5 mt-5">Reason</Eyebrow>
      <Input
        autoFocus
        dense
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. customer disputing amount"
        className="mb-4"
      />
      <Eyebrow as="p" className="mb-1.5">Resume on (optional)</Eyebrow>
      <Input type="date" dense value={resumeDate} onChange={(e) => setResumeDate(e.target.value)} className="mb-4" />
      {error && (
        <p className="mb-4 border border-accent px-3 py-2 text-sm text-accent" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-3">
        <Button variant="secondary" size="sm" disabled={submitting || !reason.trim()} onClick={() => onConfirm(reason, resumeDate)}>
          {submitting ? "Snoozing..." : "Confirm snooze"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </ModalShell>
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
    <ModalShell onClose={onClose}>
      <h2 className="font-display text-xl font-semibold tracking-tight text-foreground">Follow up with the customer</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Sends a follow-up email now, then repeats on the schedule below until you cancel it or the end date passes.
      </p>

      <Eyebrow as="p" className="mb-1.5 mt-5">Customer email</Eyebrow>
      <Input
        autoFocus
        dense
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="customer@example.com"
        className="mb-4"
      />
      <Eyebrow as="p" className="mb-1.5">Every how many days</Eyebrow>
      <Input dense type="number" min={1} value={cadenceDays} onChange={(e) => setCadenceDays(e.target.value)} className="mb-4" />
      <Eyebrow as="p" className="mb-1.5">Until (optional)</Eyebrow>
      <Input dense type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="mb-4" />
      {error && (
        <p className="mb-4 border border-accent px-3 py-2 text-sm text-accent" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-3">
        <Button variant="secondary" size="sm" disabled={submitting || !canSubmit} onClick={() => onConfirm(email.trim(), cadence, endDate)}>
          {submitting ? "Starting..." : "Start follow-ups"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </ModalShell>
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
  const [error, setError] = useState<{ kind: "not-found" | "failed"; message: string; detail?: string } | null>(null);
  const [invoiceLoading, setInvoiceLoading] = useState(false);
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
    setInvoiceLoading(true);
    setError(null);
    getInvoice(token, invoiceId)
      .then(setInvoice)
      .catch((e) => {
        const kind = classifyLoadError(e);
        setError({
          kind,
          ...describeError(
            e,
            kind === "not-found"
              ? "This invoice couldn't be found. It may have been removed, or the link may be out of date."
              : "We couldn't load this invoice."
          ),
        });
      })
      .finally(() => setInvoiceLoading(false));
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

  function loadAgentCase() {
    if (!token || !invoiceId) return;
    listAgentCases(token)
      .then((cases) =>
        setAgentCase(
          cases.find((c) => c.case_id === invoiceId || c.invoice_no === invoiceId) ?? null
        )
      )
      .catch(() => setAgentCase(null));
  }

  useEffect(loadAgentCase, [token, invoiceId]);

  // Poll so a reply landing or the agent sending its next outreach shows
  // up without a manual reload (added 2026-08-06, user request).
  useEffect(() => {
    if (!token || !invoiceId) return;
    const id = setInterval(() => {
      loadInvoice();
      loadTimeline();
      loadFollowUp();
      loadAgentCase();
    }, 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      setFollowUpError(describeError(e, "That didn't go through. Please try again.").message);
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
      setCommentError(describeError(e, "That comment didn't post. Please try again.").message);
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
      setSnoozeError(describeError(e, "That didn't go through. Please try again.").message);
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
      setResumeError(describeError(e, "That didn't go through. Please try again.").message);
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

  // The "back to dashboard" link is this page's only fixed identity/nav
  // element before an invoice loads (there's no separate PageHeader --
  // the title itself comes from the invoice), so it stays visible in
  // every state instead of being wiped out by a bare error.
  const backLink = (
    <Link
      to="/"
      className="font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      &larr; Back to dashboard
    </Link>
  );

  if (error) {
    // A genuinely missing invoice can't be fixed by retrying the same id
    // -- offer a way out instead. A backend/connectivity failure is
    // usually transient, so offer retry there.
    return (
      <div className="mx-auto max-w-4xl px-6 py-10 sm:px-12">
        {backLink}
        <div className="mt-6">
          <ErrorState
            eyebrow={error.kind === "not-found" ? "Invoice not found" : "Couldn't load this invoice"}
            message={error.message}
            detail={error.detail}
            onRetry={error.kind === "not-found" ? undefined : loadInvoice}
            retrying={invoiceLoading}
            action={
              error.kind === "not-found" ? (
                <Link to="/" className={`${buttonVariants("secondary", "sm")} min-h-11`}>
                  Back to dashboard
                </Link>
              ) : undefined
            }
          />
        </div>
      </div>
    );
  }
  if (!invoice)
    return (
      <div className="mx-auto max-w-4xl px-6 py-10 sm:px-12">
        {backLink}
        <p className="mt-6 text-sm text-muted-foreground">Loading…</p>
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
    ["Due date", invoice.due_date],
    ["Aging status", invoice.aging_status],
    ["Open amount", money(invoice.open_amount)],
  ];

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 sm:px-12">
      <Link to="/" className="font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground">
        &larr; Back to dashboard
      </Link>

      <div className="mt-6 border border-border p-6 md:p-8">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <Eyebrow as="p" className="mb-2">Invoice</Eyebrow>
            <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              {invoice.project_name ?? invoice.project_number ?? "Invoice"}
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {isSnoozed ? (
              <Button variant="secondary" size="sm" disabled={resuming} onClick={handleResume}>
                {resuming ? "Resuming..." : "Resume"}
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => setShowSnoozeModal(true)}>
                Snooze
              </Button>
            )}
            {!activeCampaign && (
              <Button variant="secondary" size="sm" onClick={() => setShowFollowUpModal(true)}>
                Follow up
              </Button>
            )}
            <Button variant="primary" size="sm" onClick={askAboutThis}>
              Ask about this
            </Button>
          </div>
        </div>

        {invoice.status && (
          <div className="mb-5">
            <Badge tone={STATUS_TONE[invoice.status] ?? "neutral"}>{invoice.status.replace(/_/g, " ")}</Badge>
          </div>
        )}

        <dl className="grid grid-cols-2 gap-4 border-y border-border py-4 text-xs sm:grid-cols-3">
          {fields.map(([label, value]) => (
            <div key={label}>
              <dt className="mb-1"><Eyebrow>{label}</Eyebrow></dt>
              <dd className="font-mono tabular-nums text-foreground">{value ?? "--"}</dd>
            </div>
          ))}
        </dl>

        {isSnoozed && (
          <div className="mt-4 border border-[#a3660a] px-4 py-3 text-sm text-[#facc15]">
            Snoozed &mdash; reminders are paused until this is resumed.
          </div>
        )}
        {resumeError && (
          <p className="mt-4 border border-accent px-4 py-3 text-sm text-accent" role="alert">{resumeError}</p>
        )}

        {activeCampaign && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border border-border px-4 py-3 text-sm text-foreground">
            <span>
              Following up with {activeCampaign.customer_email} every {activeCampaign.cadence_days} day
              {activeCampaign.cadence_days === 1 ? "" : "s"}
              {activeCampaign.end_date ? ` until ${activeCampaign.end_date}` : ""}
              {activeCampaign.send_count > 0 ? ` (${activeCampaign.send_count} sent)` : ""}
            </span>
            <Button variant="ghost" size="sm" disabled={cancellingFollowUp} onClick={handleCancelFollowUp}>
              {cancellingFollowUp ? "Cancelling..." : "Cancel"}
            </Button>
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
        <div className="mt-4 border border-border p-6 md:p-8">
          <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
            <div>
              <Eyebrow as="p" className="mb-2">Outcome agent</Eyebrow>
              <p className="text-sm text-muted-foreground">Long-horizon pursuit state for this invoice.</p>
            </div>
            <Link to={`/agent/cases/${agentCase.id}`} className={buttonVariants("primary", "sm")}>
              Open cockpit
              <ExternalLink size={14} strokeWidth={1.5} aria-hidden />
              <span
                aria-hidden
                className="pointer-events-none absolute -bottom-0.5 left-0 h-0.5 w-full origin-left scale-x-100 bg-accent transition-transform duration-150 ease-bold group-hover:scale-x-110"
              />
            </Link>
          </div>
          <AgentPhaseRail state={agentCase.state} target={agentCase.target} tone="bold" />
        </div>
      )}

      <div className="mt-4 border border-border p-6 md:p-8">
        <Eyebrow as="p" className="mb-2">Comments</Eyebrow>
        <p className="text-sm text-muted-foreground">
          Comments posted from Teams and the web -- both land in the same timeline.
        </p>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row">
          <Input
            ref={commentInputRef}
            dense
            value={commentText}
            onChange={(e) => setCommentText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !posting) submitComment();
            }}
            placeholder="Add a comment..."
            className="flex-1"
          />
          <Button variant="secondary" size="sm" disabled={posting || !commentText.trim()} onClick={submitComment}>
            {posting ? "Posting..." : "Post"}
          </Button>
        </div>
        {commentError && <p className="mt-2 text-xs text-accent">{commentError}</p>}

        <ul className="mt-6 space-y-4">
          {timelineLoading && <li className="text-sm text-muted-foreground">Loading…</li>}
          {!timelineLoading && comments.length === 0 && (
            <li className="text-sm text-muted-foreground">No comments yet.</li>
          )}
          {!timelineLoading &&
            comments.map((event, i) => {
              const { author, body } = parseCommentAuthor(event.summary ?? event.title);
              return (
                <li key={i} className="border-l-2 border-border pl-4">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs font-medium uppercase tracking-wide text-foreground">{author ?? "Unknown user"}</span>
                    <span className="font-mono text-xs text-muted-foreground">{when(event.at)}</span>
                  </div>
                  <p className="mt-1 text-sm leading-normal text-foreground">{body}</p>
                </li>
              );
            })}
        </ul>
      </div>

      <div className="mt-4 border border-border p-6 md:p-8">
        <Eyebrow as="p" className="mb-2">Activity</Eyebrow>
        <p className="text-sm text-muted-foreground">Stage changes, outreach sent, and other system events.</p>

        <ul className="mt-6 space-y-4">
          {timelineLoading && <li className="text-sm text-muted-foreground">Loading…</li>}
          {!timelineLoading && activity.length === 0 && (
            <li className="text-sm text-muted-foreground">No activity yet.</li>
          )}
          {!timelineLoading &&
            activity.map((event, i) => (
              <li key={i} className="border-l-2 border-border pl-4">
                <div className="flex items-center justify-between">
                  <Eyebrow>{humanizeEventType(event.event_type)}</Eyebrow>
                  <span className="font-mono text-xs text-muted-foreground">{when(event.at)}</span>
                </div>
                <p className="mt-1 text-sm leading-normal text-foreground">{event.summary ?? event.title ?? "--"}</p>
              </li>
            ))}
        </ul>
      </div>
    </div>
  );
}
