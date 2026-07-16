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

export interface PinnedInvoice {
  invoice_id: string;
  label: string;
}

export type ChatEvent =
  | { type: "tool_call"; name: string; permitted: boolean }
  | { type: "answer"; content: string; truncated: boolean };

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
