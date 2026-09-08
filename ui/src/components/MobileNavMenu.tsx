import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { NavLink } from "react-router-dom";
import { Sparkles, LogOut, X } from "lucide-react";

// Full-bleed overlay panel for < md. Mirrors the desktop nav's destinations
// exactly (same NavLink + active-state mechanism, same underline-not-just-
// colour cue) plus the actions that get `hidden` at this width on desktop
// (Guided Demo, email, Log out) so nothing reachable up top goes missing
// down here. Rendered via portal so it sits above everything regardless of
// where NavBar lives in the tree, and is unmounted (not just hidden) when
// closed so it never traps clicks behind an invisible layer.
const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex min-h-[44px] items-center border-b border-border px-6 font-mono text-sm font-medium uppercase tracking-wider transition-colors duration-150 ease-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset ${
    isActive ? "text-accent" : "text-muted-foreground hover:text-foreground"
  }`;

const NAV_ITEMS: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/chat", label: "Chat" },
  { to: "/documents", label: "Documents" },
  { to: "/chases", label: "Chases" },
  { to: "/agent", label: "Agent" },
  { to: "/agent/mailbox", label: "Mailbox" },
  { to: "/outbox", label: "Outbox" },
  { to: "/settings", label: "Settings" },
];

interface MobileNavMenuProps {
  open: boolean;
  onClose: () => void;
  menuId: string;
  email: string | null;
  onGuidedDemo: () => void;
  onLogout: () => void;
}

export default function MobileNavMenu({
  open,
  onClose,
  menuId,
  email,
  onGuidedDemo,
  onLogout,
}: MobileNavMenuProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Focus moves into the menu on open (onto its own close control), and
  // Escape closes it from anywhere inside.
  useEffect(() => {
    if (!open) return;
    closeButtonRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);

    // Lock page scroll while the overlay is open.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      id={menuId}
      role="dialog"
      aria-modal="true"
      aria-label="Site navigation"
      className="fixed inset-0 z-50 flex flex-col bg-background md:hidden"
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
        <span className="flex items-center gap-2 font-display text-base font-semibold tracking-tight text-foreground">
          <span className="flex h-6 w-6 items-center justify-center border border-accent font-mono text-[10px] font-bold text-accent">
            AR
          </span>
          Copilot
        </span>
        <button
          ref={closeButtonRef}
          type="button"
          aria-label="Close menu"
          onClick={onClose}
          className="flex h-11 w-11 items-center justify-center text-muted-foreground transition-colors duration-150 ease-bold hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X size={22} strokeWidth={1.5} aria-hidden />
        </button>
      </div>

      <nav ref={panelRef} className="flex flex-1 flex-col min-h-0 overflow-y-auto" aria-label="Primary">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={linkClass} onClick={onClose}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="flex shrink-0 flex-col gap-3 border-t border-border px-6 py-5">
        <button
          type="button"
          onClick={() => {
            onClose();
            onGuidedDemo();
          }}
          className="flex min-h-[44px] items-center justify-center gap-1.5 border border-border px-3 font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:border-accent hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <Sparkles size={14} strokeWidth={1.5} aria-hidden />
          Guided Demo
        </button>
        {email && (
          <span className="text-center font-mono text-xs text-muted-foreground">{email}</span>
        )}
        <button
          type="button"
          onClick={() => {
            onClose();
            onLogout();
          }}
          className="flex min-h-[44px] items-center justify-center gap-1.5 border border-border px-3 font-mono text-xs font-medium uppercase tracking-wider text-foreground transition-colors duration-150 ease-bold hover:border-accent hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <LogOut size={14} strokeWidth={1.5} aria-hidden />
          Log out
        </button>
      </div>
    </div>,
    document.body
  );
}
