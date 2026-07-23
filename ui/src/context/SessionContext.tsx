// Session persists across refresh/close (changed 2026-07-23 -- the
// original PLAN.md §5 Phase 2 choice of memory-only was deliberate but the
// user now wants to stay logged in). Backed by localStorage rather than
// sessionStorage so it survives closing the tab/browser, not just a
// refresh. The server's own session store is still in-memory and process-
// local (web.py's _sessions dict -- unchanged), so a stored token can go
// stale across a server restart; api.ts's request() clears this same key
// and bounces to /login on a 401 rather than leaving a dead session
// sitting in localStorage.
import { createContext, useContext, useState, type ReactNode } from "react";
import type { PinnedInvoice, Project } from "../api";

const STORAGE_KEY = "ar_copilot_session";

function loadStoredSession(): { token: string | null; email: string | null } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { token: null, email: null };
    const parsed = JSON.parse(raw);
    return { token: parsed.token ?? null, email: parsed.email ?? null };
  } catch {
    return { token: null, email: null };
  }
}

interface SessionState {
  token: string | null;
  email: string | null;
  setSession: (token: string, email: string) => void;
  clearSession: () => void;
  // The invoice-ID-free UI handoff: Dashboard sets this, Chat reads it. The
  // raw invoice_id only ever appears in the outgoing chat API call, never
  // rendered anywhere as something the user should read or type back.
  pinnedInvoice: PinnedInvoice | null;
  setPinnedInvoice: (pin: PinnedInvoice | null) => void;
  // Project-scoped chat (added 2026-07-23): every chat conversation is now
  // about exactly one project. Sibling state to pinnedInvoice, same
  // lifecycle -- reset together on logout, and Dashboard/InvoiceDetail set
  // both at once when handing off into Chat.
  currentProject: Project | null;
  setCurrentProject: (project: Project | null) => void;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const initial = loadStoredSession();
  const [token, setToken] = useState<string | null>(initial.token);
  const [email, setEmail] = useState<string | null>(initial.email);
  const [pinnedInvoice, setPinnedInvoice] = useState<PinnedInvoice | null>(null);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);

  const setSession = (t: string, e: string) => {
    setToken(t);
    setEmail(e);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: t, email: e }));
  };
  const clearSession = () => {
    setToken(null);
    setEmail(null);
    setPinnedInvoice(null);
    setCurrentProject(null);
    localStorage.removeItem(STORAGE_KEY);
  };

  return (
    <SessionContext.Provider
      value={{ token, email, setSession, clearSession, pinnedInvoice, setPinnedInvoice, currentProject, setCurrentProject }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
