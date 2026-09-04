import { NavLink, useNavigate } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { useSession } from "../context/SessionContext";

// Nav links: mono uppercase, wide tracking, underline-on-active/hover --
// no pill backgrounds. The active state is the underline plus full
// foreground colour, never colour alone.
const linkClass = ({ isActive }: { isActive: boolean }) =>
  `relative py-1.5 font-mono text-xs font-medium uppercase tracking-wider transition-colors duration-150 ease-bold after:absolute after:-bottom-0.5 after:left-0 after:h-px after:bg-accent after:transition-transform after:duration-150 after:ease-bold ${
    isActive
      ? "text-foreground after:w-full after:scale-x-100"
      : "text-muted-foreground after:w-full after:scale-x-0 hover:text-foreground hover:after:scale-x-100"
  }`;

export default function NavBar() {
  const { email, clearSession } = useSession();
  const navigate = useNavigate();

  return (
    <nav className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur-sm">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-6 px-6 py-4 sm:px-12">
        <div className="flex items-center gap-8">
          <span className="flex items-center gap-2 font-display text-base font-semibold tracking-tight text-foreground">
            <span className="flex h-6 w-6 items-center justify-center border border-accent font-mono text-[10px] font-bold text-accent">
              AR
            </span>
            Copilot
          </span>
          <div className="hidden items-center gap-5 md:flex">
            <NavLink to="/" className={linkClass} end>
              Dashboard
            </NavLink>
            <NavLink to="/chat" className={linkClass}>
              Chat
            </NavLink>
            <NavLink to="/documents" className={linkClass}>
              Documents
            </NavLink>
            <NavLink to="/chases" className={linkClass}>
              Chases
            </NavLink>
            <NavLink to="/agent" className={linkClass}>
              Agent
            </NavLink>
            <NavLink to="/agent/mailbox" className={linkClass}>
              Mailbox
            </NavLink>
            <NavLink to="/outbox" className={linkClass}>
              Outbox
            </NavLink>
            <NavLink to="/settings" className={linkClass}>
              Settings
            </NavLink>
          </div>
        </div>
        <div className="flex items-center gap-5">
          <button
            onClick={() => navigate("/guided-demo")}
            className="hidden items-center gap-1.5 border border-border px-3 py-1.5 font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:border-accent hover:text-accent sm:inline-flex"
          >
            <Sparkles size={14} strokeWidth={1.5} aria-hidden />
            Guided Demo
          </button>
          <span className="hidden font-mono text-xs text-muted-foreground lg:inline">{email}</span>
          <button
            onClick={clearSession}
            className="font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-accent"
          >
            Log out
          </button>
        </div>
      </div>
    </nav>
  );
}
