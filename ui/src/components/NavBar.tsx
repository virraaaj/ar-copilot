import { NavLink } from "react-router-dom";
import { useSession } from "../context/SessionContext";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors duration-150 ${
    isActive ? "bg-zinc-900 text-white" : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900"
  }`;

export default function NavBar() {
  const { email, clearSession } = useSession();

  return (
    <nav className="sticky top-0 z-10 border-b border-zinc-200/70 bg-white/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="flex items-center gap-2 font-display text-[15px] font-semibold tracking-tight text-zinc-900">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-zinc-900 text-[11px] font-bold text-white">
              AR
            </span>
            Copilot
          </span>
          <div className="flex items-center gap-1">
            <NavLink to="/" className={linkClass} end>
              Dashboard
            </NavLink>
            <NavLink to="/chat" className={linkClass}>
              Chat
            </NavLink>
            <NavLink to="/documents" className={linkClass}>
              Documents
            </NavLink>
            <NavLink to="/ar-health" className={linkClass}>
              AR Health
            </NavLink>
            <NavLink to="/escalation-policy" className={linkClass}>
              Escalation Policy
            </NavLink>
            <NavLink to="/project-contacts" className={linkClass}>
              Project Contacts
            </NavLink>
            <NavLink to="/default-project-contacts" className={linkClass}>
              Default Contacts
            </NavLink>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-[13px] text-zinc-400">{email}</span>
          <button
            onClick={clearSession}
            className="text-[13px] font-medium text-zinc-500 transition-colors hover:text-zinc-900"
          >
            Log out
          </button>
        </div>
      </div>
    </nav>
  );
}
