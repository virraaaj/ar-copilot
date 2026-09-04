// Bold Typography input primitive. Sharp corners, no glow on focus --
// the accent border is the entire focus affordance.
import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes } from "react";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  dense?: boolean;
}

const INPUT_BASE =
  "w-full border border-border bg-input px-4 text-base text-foreground placeholder-muted-foreground outline-none transition-colors duration-150 ease-bold focus:border-accent disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { dense = false, className = "", ...props },
  ref
) {
  return (
    <input
      ref={ref}
      className={`${INPUT_BASE} ${dense ? "h-11" : "h-12 md:h-14"} ${className}`}
      {...props}
    />
  );
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  dense?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { dense = false, className = "", children, ...props },
  ref
) {
  return (
    <select
      ref={ref}
      className={`${INPUT_BASE} ${dense ? "h-11" : "h-12 md:h-14"} cursor-pointer ${className}`}
      {...props}
    >
      {children}
    </select>
  );
});
