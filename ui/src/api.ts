// Thin fetch wrapper over the FastAPI backend (app/channels/web.py).
// Every call takes the session token explicitly rather than reaching into
// context, so this file has zero React dependency and stays easy to test.

export interface Invoice {
  invoice_id: string;
  case_key: string;
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
}

export interface EscalationStage {
  stage_id: string;
  stage_rule_id: string | null;
  stage_code: string;
  stage_name: string;
  sequence_order: number;
  is_terminal_stage: boolean;
  min_days_in_stage: number | null;
  max_days_in_stage: number | null;
}

export interface EscalationPolicy {
  policy_id: string;
  version_id: string;
  version_label: string | null;
  stages: EscalationStage[];
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

export async function getEscalationPolicy(token: string): Promise<EscalationPolicy> {
  return request("/escalation-policy", token);
}

export async function updateEscalationStage(
  token: string,
  versionId: string,
  stageRuleId: string,
  update: { max_days_in_stage?: number; min_days_in_stage?: number }
): Promise<void> {
  await request(`/escalation-policy/versions/${versionId}/stage-rules/${stageRuleId}`, token, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
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

export async function listDocuments(token: string, docType?: string): Promise<DocumentRef[]> {
  const qs = docType ? `?doc_type=${encodeURIComponent(docType)}` : "";
  return request(`/documents${qs}`, token);
}

export async function searchDocuments(token: string, query: string, docType?: string): Promise<DocumentSearchResult[]> {
  const params = new URLSearchParams({ query });
  if (docType) params.set("doc_type", docType);
  return request(`/documents/search?${params.toString()}`, token);
}

export async function uploadDocument(token: string, file: File, docType?: string): Promise<Record<string, unknown>> {
  const form = new FormData();
  form.append("file", file);
  const qs = docType ? `?doc_type=${encodeURIComponent(docType)}` : "";
  const resp = await fetch(`/api/documents/upload${qs}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!resp.ok) throw new ApiError(resp.status, `Upload failed (${resp.status})`);
  return resp.json();
}

// SSE isn't easily done via fetch()'s streaming body in a cross-browser-safe
// way with auth headers (EventSource doesn't support custom headers at all),
// so this reads the streamed body directly and parses `data: ` lines as they
// arrive -- same wire format, just consumed by hand instead of EventSource.
export async function* streamChat(
  token: string,
  message: string,
  pinnedInvoice?: PinnedInvoice
): AsyncGenerator<ChatEvent> {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message, pinned_invoice: pinnedInvoice }),
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
