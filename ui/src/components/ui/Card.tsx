// Bold Typography card primitive. Minimal by design -- most surfaces in
// this system should separate content with type and dividers rather than
// boxes; reach for Card only when content genuinely needs a container.
import type { HTMLAttributes } from "react";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  featured?: boolean;
  badge?: string;
}

export function Card({ featured = false, badge, className = "", children, ...props }: CardProps) {
  return (
    <div
      className={`relative border bg-transparent p-6 transition-colors duration-150 ease-bold md:p-8 ${
        featured ? "border-2 border-accent" : "border-border hover:border-muted-foreground"
      } ${className}`}
      {...props}
    >
      {featured && badge && (
        <span className="absolute -top-3 left-6 bg-background px-2 font-mono text-xs font-semibold uppercase tracking-wider text-accent">
          {badge}
        </span>
      )}
      {children}
    </div>
  );
}
