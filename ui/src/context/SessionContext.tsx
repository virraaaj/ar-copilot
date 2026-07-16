// Session token lives in memory only (PLAN.md §5 Phase 2: "store token in
// memory -- this is a dev tool, not production auth"). A page refresh logs
// you out; that's intentional, not a bug to fix later.
import { createContext, useContext, useState, type ReactNode } from "react";
import type { PinnedInvoice } from "../api";

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
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [pinnedInvoice, setPinnedInvoice] = useState<PinnedInvoice | null>(null);

  const setSession = (t: string, e: string) => {
    setToken(t);
    setEmail(e);
  };
  const clearSession = () => {
    setToken(null);
    setEmail(null);
    setPinnedInvoice(null);
  };

  return (
    <SessionContext.Provider value={{ token, email, setSession, clearSession, pinnedInvoice, setPinnedInvoice }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
