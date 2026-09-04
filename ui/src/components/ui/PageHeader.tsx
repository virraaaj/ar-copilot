// Shared page header for dense operational screens: eyebrow + title +
// description on the left, optional actions on the right -- an asymmetric
// split rather than a centered marketing hero, scaled down for density
// (see design brief: reserve the full dramatic scale for Login/Dashboard
// headline metrics, not every page title).
import type { ReactNode } from "react";
import { Eyebrow } from "./Eyebrow";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-10 flex flex-col items-start justify-between gap-6 border-b border-border pb-8 sm:flex-row sm:items-end">
      <div>
        {eyebrow && <Eyebrow as="p" className="mb-3">{eyebrow}</Eyebrow>}
        <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm leading-normal text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-3">{actions}</div>}
    </div>
  );
}
