// Full-width rule used instead of card boundaries to separate sections --
// "depth comes from layered type, underlines and full-width dividers."
export function Divider({ className = "" }: { className?: string }) {
  return <hr className={`border-0 border-t border-border ${className}`} />;
}
