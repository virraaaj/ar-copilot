// Lands here when a Teams reminder-card button or chat redirect is tapped
// (added 2026-07-16). Exchanges the one-time signed token for a real
// session, then forwards on to wherever that token was meant to go --
// an invoice's snooze/comment form, or the project invoice picker.
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { exchangeMagicLink, ApiError } from "../api";
import { useSession } from "../context/SessionContext";

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
    <div className="flex min-h-screen items-center justify-center bg-[#fafafa] px-4">
      <div className="w-full max-w-[380px] text-center">
        {error ? (
          <>
            <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
            <a href="/login" className="text-[13px] font-medium text-zinc-500 hover:text-zinc-900">
              Go to sign in
            </a>
          </>
        ) : (
          <p className="text-[13px] text-zinc-400">Signing you in...</p>
        )}
      </div>
    </div>
  );
}
