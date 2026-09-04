// Mono, uppercase, wide-tracking label -- the recurring "technical detail"
// treatment this system uses for section kickers, field labels, badges
// and anything identifier-shaped (case ids, statuses, stage names).
import type { LabelHTMLAttributes } from "react";
import { BADGE_TONE_CLASSES } from "./Badge";

export type EyebrowTone = "muted" | "accent" | "foreground" | "positive" | "warning" | "critical";

export interface EyebrowProps extends LabelHTMLAttributes<HTMLElement> {
  tone?: EyebrowTone;
  as?: "span" | "p" | "div" | "label";
}

// "positive"/"warning"/"critical" reuse the exact text colour Badge uses
// for those tones (see ui/Badge.tsx BADGE_TONE_CLASSES) so a state's
// colour reads the same whether it's shown as a badge or a bare label.
const TONE: Record<EyebrowTone, string> = {
  muted: "text-muted-foreground",
  accent: "text-accent",
  foreground: "text-foreground",
  positive: BADGE_TONE_CLASSES.positive.split(" ")[1],
  warning: BADGE_TONE_CLASSES.warning.split(" ")[1],
  critical: BADGE_TONE_CLASSES.critical.split(" ")[1],
};

export function Eyebrow({ tone = "muted", as = "span", className = "", children, ...props }: EyebrowProps) {
  const Tag = as;
  return (
    <Tag
      className={`font-mono text-xs font-medium uppercase tracking-widest ${TONE[tone]} ${className}`}
      {...props}
    >
      {children}
    </Tag>
  );
}
