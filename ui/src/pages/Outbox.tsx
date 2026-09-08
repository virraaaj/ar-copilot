// Outbox screen (spec §6.18, added 2026-07-28) -- every message the chase
// agent has generated, newest first, with policy/evaluation status per
// row. Pure read view over GET /api/outbox.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useSession } from "../context/SessionContext";
import { getOutbox, type OutboxEntry } from "../api";
import { PageHeader } from "../components/ui/PageHeader";
import { Badge, type BadgeTone } from "../components/ui/Badge";
import { Eyebrow } from "../components/ui/Eyebrow";

function formatWhen(iso: string): string {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const CHANNEL_TONE: Record<string, BadgeTone> = {
  email: "positive",
  teams: "positive",
  email_failed: "critical",
  blocked: "warning",
};

export default function Outbox() {
  const { token } = useSession();
  const [entries, setEntries] = useState<OutboxEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getOutbox(token).then(setEntries).catch((e) => setError(String(e)));
  }, [token]);

  if (error) {
    return (
      <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
        <p className="border border-accent px-4 py-3 text-sm text-accent" role="alert">
          {error}
        </p>
      </div>
    );
  }
  if (!entries) {
    return (
      <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
      <PageHeader eyebrow="Outcome agent" title="Outbox" description="Every message the agent has generated, newest first." />

      {entries.length === 0 && <p className="text-sm text-muted-foreground">Nothing sent yet.</p>}

      <div className="space-y-3">
        {entries.map((e) => (
          <div key={e.id} className="border border-border p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-sm">
                <Link
                  to={`/invoices/${e.case_id}`}
                  className="font-mono font-medium text-foreground underline decoration-border decoration-1 underline-offset-4 hover:decoration-accent"
                >
                  {e.invoice_no ?? e.case_key ?? e.case_id}
                </Link>
                <span className="text-muted-foreground">&middot;</span>
                <span className="font-mono text-xs text-muted-foreground">{e.project_number ?? "No project"}</span>
              </div>
              <span className="font-mono text-xs text-muted-foreground">{formatWhen(e.at)}</span>
            </div>

            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Badge tone={CHANNEL_TONE[e.channel ?? ""] ?? "neutral"}>{e.channel ?? "unknown"}</Badge>
              <Eyebrow>
                &rarr; {e.target === "pm" ? "PM" : e.target === "customer" ? "Customer" : e.target ?? "?"}
                {e.recipient ? ` (${e.recipient})` : ""}
              </Eyebrow>
              {e.composed && <Badge tone="neutral">AI-composed</Badge>}
              {e.requires_human_review && <Badge tone="warning">Needs review</Badge>}
              {e.policy_blocked && <Badge tone="critical">Blocked by policy</Badge>}
            </div>

            {e.subject && <p className="mb-1 text-sm font-medium text-foreground">{e.subject}</p>}
            {e.body && <p className="text-sm leading-normal text-muted-foreground">{e.body}</p>}
            {e.policy_reason && <p className="mt-2 text-sm text-accent">{e.policy_reason}</p>}
            {e.evaluation_failures.length > 0 && (
              <p className="mt-2 font-mono text-xs text-[#facc15]">Evaluation flags: {e.evaluation_failures.join(", ")}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
