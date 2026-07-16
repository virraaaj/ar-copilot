import { NavLink } from "react-router-dom";
import { useSession } from "../context/SessionContext";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-2 rounded-md text-sm font-medium ${isActive ? "bg-slate-800 text-white" : "text-slate-600 hover:bg-slate-100"}`;

export default function NavBar() {
  const { email, clearSession } = useSession();

  return (
    <nav className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
      <div className="flex items-center gap-1">
        <span className="mr-4 font-semibold text-slate-800">AR Copilot</span>
        <NavLink to="/" className={linkClass} end>
          Dashboard
        </NavLink>
        <NavLink to="/chat" className={linkClass}>
          Chat
        </NavLink>
        <NavLink to="/documents" className={linkClass}>
          Documents
        </NavLink>
      </div>
      <div className="flex items-center gap-3 text-sm text-slate-500">
        <span>{email}</span>
        <button onClick={clearSession} className="rounded-md px-2 py-1 hover:bg-slate-100">
          Log out
        </button>
      </div>
    </nav>
  );
}
