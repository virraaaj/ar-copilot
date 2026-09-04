// Bold Typography button primitive. Three variants, no fills except the
// "secondary" hover-invert -- depth and affordance come from underlines,
// borders and colour, never shadow or scale (see design system brief).
import { forwardRef, type ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "sm" | "md" | "lg";

const BASE =
  "group relative inline-flex items-center justify-center gap-2 whitespace-nowrap font-sans uppercase tracking-wider font-semibold transition-all duration-150 ease-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 active:translate-y-px";

const SIZE: Record<ButtonSize, string> = {
  sm: "text-xs py-2.5",
  md: "text-sm py-3",
  lg: "text-base py-4",
};

const VARIANT: Record<ButtonVariant, string> = {
  primary: "px-0 text-accent",
  secondary: "border border-foreground px-6 text-foreground hover:bg-foreground hover:text-background",
  ghost: "px-4 text-muted-foreground hover:text-foreground",
};

// Underline affordance shared by primary/ghost -- an absolutely
// positioned span that scales in on hover/focus instead of a border, so
// the button stays text-only until interacted with.
function Underline({ variant }: { variant: ButtonVariant }) {
  if (variant === "primary") {
    return (
      <span
        aria-hidden
        className="pointer-events-none absolute -bottom-0.5 left-0 h-0.5 w-full origin-left scale-x-100 bg-accent transition-transform duration-150 ease-bold group-hover:scale-x-110"
      />
    );
  }
  if (variant === "ghost") {
    return (
      <span
        aria-hidden
        className="pointer-events-none absolute bottom-1 left-4 right-4 h-px origin-left scale-x-0 bg-foreground transition-transform duration-150 ease-bold group-hover:scale-x-100"
      />
    );
  }
  return null;
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className = "", children, ...props },
  ref
) {
  return (
    <button ref={ref} className={`${BASE} ${SIZE[size]} ${VARIANT[variant]} ${className}`} {...props}>
      {children}
      <Underline variant={variant} />
    </button>
  );
});

// Class-string builder for non-<button> elements that need the same look
// (e.g. react-router <Link>, which can't be a <button>).
export function buttonVariants(variant: ButtonVariant = "primary", size: ButtonSize = "md"): string {
  return `${BASE} ${SIZE[size]} ${VARIANT[variant]}`;
}
