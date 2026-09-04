// Login is the one screen in this pass that gets the full poster
// treatment -- a single-purpose gate, not a dense operational surface, so
// it carries the dramatic type scale the rest of the app deliberately
// avoids (see design brief: reserve full scale for Login + Dashboard
// headline metrics).
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, ApiError } from "../api";
import { useSession } from "../context/SessionContext";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Eyebrow } from "../components/ui/Eyebrow";

export default function Login() {
  const [email, setEmail] = useState("demo@corehelix.ai");
  const [password, setPassword] = useState("demo");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { setSession } = useSession();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { session_token, email: confirmedEmail, demo_mode } = await login(email, password);
      setSession(session_token, confirmedEmail);
      // Offline demo (DEV_AUTH_BYPASS): skip Dashboard — it needs Lummus UAT.
      navigate(demo_mode ? "/agent" : "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[7fr_5fr]">
      {/* Left: the poster panel -- headline scale, plenty of negative
          space, nothing competing with it. Hidden below lg so mobile goes
          straight to the form. */}
      <div className="relative hidden flex-col justify-between overflow-hidden border-r border-border px-16 py-16 lg:flex">
        <Eyebrow tone="accent">AR Copilot</Eyebrow>
        <h1 className="font-display text-8xl font-semibold leading-none tracking-tighter text-foreground">
          Chase
          <br />
          less.
          <br />
          <span className="text-accent">Collect</span>
          <br />
          more.
        </h1>
        <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
          One system of record for every open invoice, every chase, every promise to pay.
        </p>
      </div>

      {/* Right: the actual form -- restrained, functional. */}
      <div className="flex flex-col items-center justify-center px-6 py-16 sm:px-12">
        <div className="w-full max-w-sm">
          <div className="mb-10 lg:hidden">
            <Eyebrow tone="accent" as="p" className="mb-3">
              AR Copilot
            </Eyebrow>
            <h1 className="font-display text-5xl font-semibold leading-tight tracking-tight text-foreground">Sign in</h1>
          </div>
          <div className="mb-8 hidden lg:block">
            <h2 className="font-display text-3xl font-semibold tracking-tight text-foreground">Sign in</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Offline demo accepts any password when DEV_AUTH_BYPASS is on.
            </p>
          </div>

          <form onSubmit={handleSubmit} noValidate>
            <label htmlFor="login-email" className="mb-2 block font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Email
            </label>
            <Input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mb-6"
              required
            />

            <label htmlFor="login-password" className="mb-2 block font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Password
            </label>
            <Input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mb-6"
              required
            />

            {error && (
              <p className="mb-6 border border-accent px-4 py-3 text-sm text-accent" role="alert">
                {error}
              </p>
            )}

            <Button type="submit" variant="secondary" size="lg" disabled={loading} className="w-full">
              {loading ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
