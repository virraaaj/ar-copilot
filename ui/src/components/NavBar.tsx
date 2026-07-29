import { NavLink } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import { useTheme } from "../context/ThemeContext";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors duration-150 ${
    isActive ? "bg-green-700 text-white" : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-900 dark:hover:text-zinc-100"
  }`;

export default function NavBar() {
  const { email, clearSession } = useSession();
  const { theme, toggleTheme } = useTheme();

  return (
    <nav className="sticky top-0 z-10 border-b border-zinc-200/70 dark:border-zinc-700 bg-white/80 dark:bg-zinc-900/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="flex items-center gap-2 font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-zinc-900 dark:bg-zinc-700 text-[11px] font-bold text-white">
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
            <NavLink to="/agent" className={linkClass}>
              Agent
            </NavLink>
            <NavLink to="/chases" className={linkClass}>
              Chases
            </NavLink>
            <NavLink to="/outbox" className={linkClass}>
              Outbox
            </NavLink>
            <NavLink to="/settings" className={linkClass}>
              Settings
            </NavLink>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={toggleTheme}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="flex h-7 w-7 items-center justify-center rounded-full text-[14px] text-zinc-500 dark:text-zinc-400 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
          <span className="text-[13px] text-zinc-400 dark:text-zinc-500">{email}</span>
          <button
            onClick={clearSession}
            className="text-[13px] font-medium text-zinc-500 dark:text-zinc-400 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            Log out
          </button>
        </div>
      </div>
    </nav>
  );
}
