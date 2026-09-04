// Status badge -- mono, uppercase, bordered (never filled), always
// carrying the state as text so colour is never the only signal (this app
// shows case states and pass/fail-shaped statuses throughout).
import type { HTMLAttributes, ReactNode } from "react";

export type BadgeTone = "neutral" | "positive" | "warning" | "critical" | "accent";

const TONE: Record<BadgeTone, string> = {
  neutral: "border-border text-muted-foreground",
  positive: "border-[#2f8f4e] text-[#4ade80]",
  warning: "border-[#a3660a] text-[#facc15]",
  critical: "border-accent text-accent",
  accent: "border-accent bg-accent text-accent-foreground",
};

// Exported so other components can colour non-Badge elements (icons,
// stepper nodes, timeline markers) with the exact same tone tokens
// instead of re-deriving their own hex values.
export const BADGE_TONE_CLASSES = TONE;

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  icon?: ReactNode;
  children: ReactNode;
}

export function Badge({ tone = "neutral", icon, children, className = "", ...props }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 border px-2 py-1 font-mono text-xs font-medium uppercase tracking-wide ${TONE[tone]} ${className}`}
      {...props}
    >
      {icon}
      {children}
    </span>
  );
}
