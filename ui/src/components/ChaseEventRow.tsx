// Shared chase-event rendering (factored out of Chases.tsx 2026-07-23 so
// InvoiceDetail's new "agent communication" section and the Chases tab
// render the same event log the same way instead of two implementations
// drifting apart). Takes the owning chase so outreach rows can resolve
// "who -> who" from chase.pm_email/customer_email + event.detail.target
// (chase_engine.py only logs the target *role*, not a literal address).
import type { Chase, ChaseEvent } from "../api";

function formatWhen(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const TRAJECTORY_VERDICT_STYLES: Record<string, string> = {
  progressing: "bg-emerald-50 text-emerald-700",
  stalling: "bg-amber-50 text-amber-700",
  concerning: "bg-rose-50 text-rose-700",
};

function targetLabel(chase: Chase, target: unknown): string | null {
  if (target === "pm") return `PM${chase.pm_email ? ` (${chase.pm_email})` : ""}`;
  if (target === "customer") return `Customer${chase.customer_email ? ` (${chase.customer_email})` : ""}`;
  return null;
}

const CHANNEL_LABELS: Record<string, string> = {
  teams: "via Teams",
  email: "via email",
  email_failed: "via email (failed)",
  dry_run: "dry run",
};

export function ChaseEventRow({ chase, event }: { chase: Chase; event: ChaseEvent }) {
  if (event.kind === "trajectory_assessed") {
    const verdict = String(event.detail?.verdict ?? "");
    return (
      <div className="border-l-2 border-zinc-100 pl-3 text-[12.5px]">
        <p className="flex flex-wrap items-center gap-1.5 text-zinc-700">
          <span className="text-zinc-400">🧭 AI trajectory check:</span>
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
              TRAJECTORY_VERDICT_STYLES[verdict] ?? "bg-zinc-100 text-zinc-600"
            }`}
          >
            {verdict || "unknown"}
          </span>
          {event.detail?.reason ? <span className="text-zinc-500">-- {String(event.detail.reason)}</span> : null}
        </p>
        <p className="text-zinc-400">{formatWhen(event.at)}</p>
      </div>
    );
  }

  const isOutreach = event.kind === "outreach_sent" || event.kind === "dry_run_send";
  const composed = isOutreach && event.detail?.composed === true;
  const who = isOutreach ? targetLabel(chase, event.detail?.target) : null;
  const channel = isOutreach ? CHANNEL_LABELS[String(event.detail?.channel ?? "")] : null;

  return (
    <div className="border-l-2 border-zinc-100 pl-3 text-[12.5px]">
      <p className="text-zinc-700">
        <span className="font-medium">{event.kind.replace(/_/g, " ")}</span>
        {who && <span className="text-zinc-500"> &rarr; {who}</span>}
        {channel && <span className="text-zinc-400"> ({channel})</span>}
        {composed && (
          <span className="ml-1.5 rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
            ✨ AI-composed
          </span>
        )}
        {event.detail?.text ? `: ${String(event.detail.text)}` : ""}
        {event.detail?.reason ? `: ${String(event.detail.reason)}` : ""}
      </p>
      <p className="text-zinc-400">{formatWhen(event.at)}</p>
    </div>
  );
}
