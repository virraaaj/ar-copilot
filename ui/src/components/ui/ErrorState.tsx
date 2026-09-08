// Shared failure UI (added for the error-handling policy pass, 2026-09-07).
// Before this, a failed fetch either dumped a raw exception string inline
// under an intact page (Chat's project picker) or replaced the whole page
// -- header and all -- with a bare alert (Documents, ProjectContacts,
// DefaultProjectContacts, ProjectInvoicePicker, InvoiceDetail). Neither
// told the user anything they could act on.
//
// Policy this component exists to enforce:
//   - never show a raw exception/technical string as the headline
//   - never replace a page's header/identity -- this fills the content
//     region only, so callers render it *below* their PageHeader
//   - always offer a way forward: a retry callback, or an explicit
//     `action` when retrying genuinely can't help (e.g. a bad id)
//   - announced to assistive tech, and never colour-only (the eyebrow and
//     button carry an accent hint, but the message itself is plain text)
import type { ReactNode } from "react";
import { Button } from "./Button";
import { Eyebrow } from "./Eyebrow";

export interface ErrorStateProps {
  /** Human-readable explanation. Never pass a raw Error/exception string here. */
  message: ReactNode;
  /** Short label above the message, e.g. "Couldn't load", "Not found". */
  eyebrow?: string;
  /** Re-runs the failed fetch. Omit when retrying can't help (use `action` instead). */
  onRetry?: () => void;
  retryLabel?: string;
  /** True while a retry triggered from here is in flight. */
  retrying?: boolean;
  /** Alternative or additional way forward (a link back, a different action). */
  action?: ReactNode;
  /** Technical detail (the actual exception message) -- kept available but never the headline. */
  detail?: string | null;
  className?: string;
}

// Renders as `role="alert"` (implicitly assertive) so screen readers
// announce the failure the moment it appears, matching how the rest of
// this app already announces its inline errors.
export function ErrorState({
  message,
  eyebrow = "Couldn't load this",
  onRetry,
  retryLabel = "Try again",
  retrying = false,
  action,
  detail,
  className = "",
}: ErrorStateProps) {
  return (
    <div role="alert" aria-live="assertive" className={`border border-border px-6 py-12 text-center ${className}`}>
      <Eyebrow as="p" tone="accent" className="mb-3">
        {eyebrow}
      </Eyebrow>
      <p className="mx-auto max-w-md text-sm leading-normal text-muted-foreground">{message}</p>

      {(onRetry || action) && (
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          {onRetry && (
            <Button variant="secondary" size="sm" className="min-h-11" onClick={onRetry} disabled={retrying}>
              {retrying ? "Retrying…" : retryLabel}
            </Button>
          )}
          {action}
        </div>
      )}

      {detail && (
        <details className="mx-auto mt-6 max-w-md text-left">
          <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground">
            Technical details
          </summary>
          <p className="mt-2 whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-muted-foreground">
            {detail}
          </p>
        </details>
      )}
    </div>
  );
}

// Turns a caught value into a safe-to-render message plus (optionally) the
// real technical detail, so call sites never have to `String(err)` a raw
// exception onto the screen themselves. `fallback` is the human sentence
// shown per call site (tailored to what actually failed); `detail` is
// meant for ErrorState's collapsed section or a `title` attribute, never
// the headline.
export function describeError(err: unknown, fallback = "Something went wrong. Please try again."): {
  message: string;
  detail?: string;
} {
  const detail = err instanceof Error ? err.message : typeof err === "string" ? err : undefined;
  return { message: fallback, detail };
}
