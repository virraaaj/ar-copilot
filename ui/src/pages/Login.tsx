import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, ApiError } from "../api";
import { useSession } from "../context/SessionContext";

export default function Login() {
  const [email, setEmail] = useState("uat-test@lummus.internal");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { setSession } = useSession();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { session_token, email: confirmedEmail } = await login(email, password);
      setSession(session_token, confirmedEmail);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#fafafa] px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-8 flex flex-col items-center">
          <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-zinc-900 text-[13px] font-bold text-white shadow-sm">
            AR
          </span>
          <h1 className="font-display text-[22px] font-semibold tracking-tight text-zinc-900">AR Copilot</h1>
          <p className="mt-1 text-[13px] text-zinc-400">Sign in with your Lummus account</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_rgba(0,0,0,0.08)]"
        >
          <label className="mb-1.5 block text-[13px] font-medium text-zinc-600">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mb-4 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
            required
          />
          <label className="mb-1.5 block text-[13px] font-medium text-zinc-600">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mb-5 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
            required
          />
          {error && (
            <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-green-700 py-2.5 text-[14px] font-medium text-white transition-colors hover:bg-green-800 disabled:opacity-40"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
