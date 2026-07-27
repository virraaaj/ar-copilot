// Thin fetch wrapper over the FastAPI backend (app/channels/web.py).
// Every call takes the session token explicitly rather than reaching into
// context, so this file has zero React dependency and stays easy to test.

export interface Invoice {
  invoice_id: string;
  case_key: string;
  // The real, human-facing invoice number (e.g. "UAT-RND-FIN-002") --
  // distinct from case_key, an internal engine-generated reference. Use
  // this to identify an invoice to a user; case_key is a fallback for the
  // rare case where a case predates having one.
  invoice_no: string | null;
  status: string | null;
  stage: string | null;
  project_number: string | null;
  project_name: string | null;
  business_unit_id: string | null;
  due_date: string | null;
  open_amount: number | null;
  aging_status: string | null;
  // Only present on the single-invoice detail response (get_invoice's
  // full=True projection) -- non-null while a snooze is currently in
  // effect. Drives the Snooze/Resume button toggle on InvoiceDetail.tsx.
  active_pause_id?: string | null;
}

export interface AgingSummary {
  total_invoices: number;
  total_open_amount: number;
  by_aging_bucket: Record<string, { count: number; open_amount: number }>;
  by_stage: Record<string, { count: number; open_amount: number }>;
}

export interface DocumentSearchResult {
  doc_id: string;
  filename: string;
  doc_type: string | null;
  page: number;
  excerpt: string;
  score: number;
}

export interface DocumentRef {
  filename: string;
  doc_type: string | null;
  size_bytes: number | null;
  project_number: string | null;
}

// Projects have no dedicated entity in this app's own storage -- derived
// live from invoice data, same grouping Dashboard already does client-side.
// Added 2026-07-23 for the Documents folder view and Chat's project picker.
export interface Project {
  project_number: string;
  project_name: string | null;
}

export async function listProjects(token: string): Promise<Project[]> {
  return request("/projects", token);
}

export interface PinnedInvoice {
  invoice_id: string;
  label: string;
}

export interface TimelineEvent {
  event_type: string | null;
  title: string | null;
  summary: string | null;
  actor: string | null;
  at: string | null;
}

export type ChatEvent =
  | { type: "tool_call"; name: string; permitted: boolean }
  | { type: "answer"; content: string; truncated: boolean };

// Project contacts + default project contacts (added 2026-07-16) -- same
// design/concept as Lummus's own equivalent pages, see api.ts's module
// comment near the fetch functions below for the "default = template,
// not a live link" model.
export const CONTACT_TYPE_LABELS: Record<string, string> = {
  pm: "Project Manager",
  bu_finance: "BU Finance",
  corp_finance: "Corporate Finance",
  general_manager: "General Manager",
  legal: "Legal",
};

export const CONTACT_TYPES = Object.keys(CONTACT_TYPE_LABELS);

export interface Contact {
  contact_id: string;
  contact_type: string;
  name: string | null;
  email: string | null;
  phone: string | null;
}

export interface ProjectContactGroup {
  project_number: string;
  project_name: string | null;
  contacts: Contact[];
}

export interface DefaultContactScope {
  bu: string | null;
  bu_name: string | null;
  contacts: Contact[];
}

export interface BusinessUnit {
  bu_id: string;
  bu_name: string | null;
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(`/api${path}`, { ...init, headers });
  if (!resp.ok) {
    // A stored session (see SessionContext.tsx) can go stale across a
    // server restart, since the server's own session store is in-memory
    // and process-local. Rather than leave the UI stuck showing
    // authenticated pages that 401 on every fetch, drop the dead session
    // and bounce to login -- but only for an actually-authenticated
    // request (token present), so a bad-password login attempt doesn't
    // trigger a redirect loop on the login page itself.
    if (resp.status === 401 && token) {
      localStorage.removeItem("ar_copilot_session");
      if (window.location.pathname !== "/login") window.location.href = "/login";
    }
    const body = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, body.detail || `Request failed (${resp.status})`);
  }
  return resp.json();
}

export async function login(email: string, password: string): Promise<{ session_token: string; email: string }> {
  return request("/auth/login", null, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export interface MagicLinkExchangeResult {
  session_token: string;
  email: string;
  redirect: string;
}

export async function exchangeMagicLink(token: string): Promise<MagicLinkExchangeResult> {
  return request("/auth/magic-link", null, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
}

export async function listInvoices(
  token: string,
  filters: { status?: string; stage?: string; overdue_days_min?: number } = {}
): Promise<Invoice[]> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v !== undefined) params.set(k, String(v));
  const qs = params.toString();
  return request(`/invoices${qs ? `?${qs}` : ""}`, token);
}

export async function getInvoice(token: string, invoiceId: string): Promise<Invoice> {
  return request(`/invoices/${encodeURIComponent(invoiceId)}`, token);
}

export async function getAgingSummary(token: string): Promise<AgingSummary> {
  return request("/aging-summary", token);
}

export interface AgingUploadResult {
  sync: Record<string, unknown>;
  tick: Record<string, unknown> | null;
  tick_error: string | null;
}

export async function uploadAgingExcel(token: string, file: File): Promise<AgingUploadResult> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/aging-upload", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, body.detail || `Upload failed (${resp.status})`);
  }
  return resp.json();
}

export async function listProjectInvoices(token: string, projectNumber: string): Promise<Invoice[]> {
  return request(`/projects/${encodeURIComponent(projectNumber)}/invoices`, token);
}

export async function snoozeInvoice(
  token: string,
  invoiceId: string,
  reason: string,
  resumeDate?: string
): Promise<void> {
  await request(`/invoices/${encodeURIComponent(invoiceId)}/snooze`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason, resume_date: resumeDate || undefined }),
  });
}

export async function resumeInvoice(token: string, invoiceId: string): Promise<void> {
  await request(`/invoices/${encodeURIComponent(invoiceId)}/resume`, token, { method: "POST" });
}

// Manual follow-up email campaigns (added 2026-07-16) -- "follow up with
// the customer" from Teams or the web. See app/services/followup_engine.py
// for how sends actually go out and how replies get mirrored back into
// the project's Teams chat.
export interface FollowUpCampaign {
  id: string;
  case_id: string;
  customer_email: string;
  requested_by: string;
  cadence_days: number;
  end_date: string | null;
  status: string;
  created_at: string;
  next_send_at: string;
  last_sent_at: string | null;
  send_count: number;
}

export interface FollowUpSendRecord {
  sent_at: string;
  to_email: string;
}

export interface FollowUpStatus {
  active_campaign: FollowUpCampaign | null;
  send_history: FollowUpSendRecord[];
}

export async function getFollowUpStatus(token: string, invoiceId: string): Promise<FollowUpStatus> {
  return request(`/invoices/${encodeURIComponent(invoiceId)}/follow-up`, token);
}

export async function createFollowUp(
  token: string,
  invoiceId: string,
  body: { customer_email: string; cadence_days: number; end_date?: string }
): Promise<{ campaign_id: string }> {
  return request(`/invoices/${encodeURIComponent(invoiceId)}/follow-up`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function cancelFollowUp(token: string, invoiceId: string): Promise<void> {
  await request(`/invoices/${encodeURIComponent(invoiceId)}/follow-up/cancel`, token, { method: "POST" });
}

export async function listBusinessUnits(token: string): Promise<BusinessUnit[]> {
  return request("/business-units", token);
}

// "Default" contacts are a template Lummus applies when a *new* project is
// created (one Global scope + optional per-BU override scopes, one contact
// per role per scope) -- not a live link to existing projects. Editing a
// default afterwards never retroactively changes any project's contacts.
export async function listDefaultProjectContacts(token: string): Promise<DefaultContactScope[]> {
  return request("/default-project-contacts", token);
}

export async function addDefaultProjectContact(
  token: string,
  body: { contact_type: string; bu?: string | null; name?: string; email?: string; phone?: string }
): Promise<Contact> {
  return request("/default-project-contacts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateDefaultProjectContact(
  token: string,
  contactId: string,
  body: { name?: string; email?: string; phone?: string }
): Promise<Contact> {
  return request(`/default-project-contacts/${encodeURIComponent(contactId)}`, token, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteDefaultProjectContact(token: string, contactId: string): Promise<void> {
  await request(`/default-project-contacts/${encodeURIComponent(contactId)}`, token, { method: "DELETE" });
}

export async function listProjectContacts(token: string): Promise<ProjectContactGroup[]> {
  return request("/project-contacts", token);
}

export async function addProjectContact(
  token: string,
  body: { project_number: string; contact_type: string; name?: string; email?: string; phone?: string }
): Promise<Contact> {
  return request("/project-contacts", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateProjectContact(
  token: string,
  contactId: string,
  body: { name?: string; email?: string; phone?: string }
): Promise<Contact> {
  return request(`/project-contacts/${encodeURIComponent(contactId)}`, token, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteProjectContact(token: string, contactId: string): Promise<void> {
  await request(`/project-contacts/${encodeURIComponent(contactId)}`, token, { method: "DELETE" });
}

export async function getInvoiceTimeline(token: string, invoiceId: string): Promise<TimelineEvent[]> {
  return request(`/invoices/${encodeURIComponent(invoiceId)}/timeline`, token);
}

export async function addComment(token: string, invoiceId: string, comment: string): Promise<void> {
  await request(`/invoices/${encodeURIComponent(invoiceId)}/comments`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comment }),
  });
}

export async function listDocuments(token: string, docType?: string, projectNumber?: string): Promise<DocumentRef[]> {
  const params = new URLSearchParams();
  if (docType) params.set("doc_type", docType);
  if (projectNumber) params.set("project_number", projectNumber);
  const qs = params.toString();
  return request(`/documents${qs ? `?${qs}` : ""}`, token);
}

export async function searchDocuments(
  token: string,
  query: string,
  docType?: string,
  projectNumber?: string
): Promise<DocumentSearchResult[]> {
  const params = new URLSearchParams({ query });
  if (docType) params.set("doc_type", docType);
  if (projectNumber) params.set("project_number", projectNumber);
  return request(`/documents/search?${params.toString()}`, token);
}

// project_number is required (project folders, added 2026-07-23) -- every
// upload belongs to exactly one project.
export async function uploadDocument(
  token: string,
  file: File,
  projectNumber: string,
  docType?: string
): Promise<Record<string, unknown>> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams({ project_number: projectNumber });
  if (docType) params.set("doc_type", docType);
  const resp = await fetch(`/api/documents/upload?${params.toString()}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!resp.ok) throw new ApiError(resp.status, `Upload failed (${resp.status})`);
  return resp.json();
}

// Agentic chase engine (PLAN_AGENTIC_CHASE.md, added 2026-07-20) -- the
// Chases tab. Chase state mirrors app/services/chase_store.py's schema.
export interface Chase {
  id: string;
  case_id: string;
  case_key: string | null;
  invoice_no: string | null;
  project_number: string | null;
  subject_token: string;
  state: string;
  target: string | null;
  pm_email: string | null;
  customer_email: string | null;
  contact_email: string | null;
  promised_date: string | null;
  promised_by: string | null;
  missed_count: number;
  nudge_count: number;
  clarify_count: number;
  last_outreach_at: string | null;
  next_action_at: string | null;
  total_tokens_used: number;
  created_at: string;
  updated_at: string;
}

export interface ChaseEvent {
  id: string;
  chase_id: string;
  at: string;
  kind: string;
  detail: Record<string, unknown> | null;
}

export async function listChases(token: string, state?: string, caseId?: string): Promise<Chase[]> {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (caseId) params.set("case_id", caseId);
  const qs = params.toString();
  return request(`/chases${qs ? `?${qs}` : ""}`, token);
}

// Outcome-agent north-star metric (added 2026-07-25) -- % of open chases
// with a known next commitment, per outcome_metrics.py.
export interface CommitmentMetric {
  total_open: number;
  known: number;
  unknown: number;
  known_pct: number;
  unknown_cases: Array<{
    chase_id: string;
    case_id: string;
    invoice_no: string | null;
    project_number: string | null;
    state: string;
    known_commitment: boolean;
  }>;
}

export async function getCommitmentMetric(token: string): Promise<CommitmentMetric> {
  return request(`/chases/commitment-metric`, token);
}

export async function getChase(token: string, chaseId: string): Promise<Chase> {
  return request(`/chases/${encodeURIComponent(chaseId)}`, token);
}

export async function listChaseEvents(token: string, chaseId: string): Promise<ChaseEvent[]> {
  return request(`/chases/${encodeURIComponent(chaseId)}/events`, token);
}

export async function pauseChase(token: string, chaseId: string): Promise<void> {
  await request(`/chases/${encodeURIComponent(chaseId)}/pause`, token, { method: "POST" });
}

export async function resumeChase(token: string, chaseId: string): Promise<void> {
  await request(`/chases/${encodeURIComponent(chaseId)}/resume`, token, { method: "POST" });
}

export async function closeChase(token: string, chaseId: string, reason: string): Promise<void> {
  await request(`/chases/${encodeURIComponent(chaseId)}/close`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export async function restartChase(token: string, chaseId: string): Promise<void> {
  await request(`/chases/${encodeURIComponent(chaseId)}/restart`, token, { method: "POST" });
}

export async function editChaseCommitment(token: string, chaseId: string, promisedDate: string): Promise<void> {
  await request(`/chases/${encodeURIComponent(chaseId)}/commitment`, token, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ promised_date: promisedDate }),
  });
}

export interface ChatHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

// SSE isn't easily done via fetch()'s streaming body in a cross-browser-safe
// way with auth headers (EventSource doesn't support custom headers at all),
// so this reads the streamed body directly and parses `data: ` lines as they
// arrive -- same wire format, just consumed by hand instead of EventSource.
// `project` is required (project-scoped chat, added 2026-07-23) -- every
// conversation is now about exactly one project.
export async function* streamChat(
  token: string,
  message: string,
  project: Project,
  pinnedInvoice?: PinnedInvoice,
  history?: ChatHistoryMessage[]
): AsyncGenerator<ChatEvent> {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message, project, pinned_invoice: pinnedInvoice, history }),
  });
  if (!resp.ok || !resp.body) {
    const body = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, body.detail || "Chat request failed");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        yield JSON.parse(line.slice("data: ".length)) as ChatEvent;
      }
    }
  }
}

export { ApiError };
