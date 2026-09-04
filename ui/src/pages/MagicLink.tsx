// Lands here when a Teams reminder-card button or chat redirect is tapped
// (added 2026-07-16). Exchanges the one-time signed token for a real
// session, then forwards on to wherever that token was meant to go --
// an invoice's snooze/comment form, or the project invoice picker.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { exchangeMagicLink, ApiError } from "../api";
import { useSession } from "../context/SessionContext";
import { Eyebrow } from "../components/ui/Eyebrow";

export default function MagicLink() {
  const [searchParams] = useSearchParams();
  const { setSession } = useSession();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = searchParams.get("token");
    if (!token) {
      setError("This link is missing its token.");
      return;
    }
    exchangeMagicLink(token)
      .then(({ session_token, email, redirect }) => {
        setSession(session_token, email);
        navigate(redirect, { replace: true });
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "This link is no longer valid."));
    // Only run once on mount -- re-running on every searchParams identity
    // change would re-exchange (and burn) a single-use token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm text-center">
        <Eyebrow tone="accent" as="p" className="mb-4">
          AR Copilot
        </Eyebrow>
        {error ? (
          <>
            <h1 className="mb-4 font-display text-3xl font-semibold tracking-tight text-foreground">Link expired</h1>
            <p className="mb-6 border border-accent px-4 py-3 text-sm text-accent" role="alert">
              {error}
            </p>
            <a
              href="/login"
              className="font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground underline decoration-border decoration-1 underline-offset-4 transition-colors duration-150 ease-bold hover:text-foreground hover:decoration-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Go to sign in
            </a>
          </>
        ) : (
          <>
            <h1 className="mb-3 font-display text-3xl font-semibold tracking-tight text-foreground">Signing you in</h1>
            <p className="text-sm text-muted-foreground">Exchanging your link for a session…</p>
          </>
        )}
      </div>
    </div>
  );
}
