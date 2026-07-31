import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listMailbox } from "../../api";
import { useSession } from "../../context/SessionContext";

export default function Mailbox() {
  const { token } = useSession();
  const [tab, setTab] = useState<"outbound" | "inbound" | "all">("all");
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!token) return;
    listMailbox(token, tab === "all" ? undefined : { direction: tab })
      .then(setRows)
      .catch((e) => setErr(String(e)));
  }, [token, tab]);

  return (
    <main className="lummus-shell min-h-[calc(100vh-4rem)]">
      <div className="mx-auto max-w-6xl px-6 py-6 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Local mailbox</p>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">Agent Mail</h1>
          </div>
          <Link to="/agent" className="lummus-btn-ghost">
            Agent Dashboard
          </Link>
        </div>
        {err && <p className="text-sm text-destructive">{err}</p>}
        <div className="flex gap-2">
          {(["all", "outbound", "inbound"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={tab === t ? "lummus-btn-primary" : "lummus-btn-ghost"}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="lummus-card space-y-2 max-h-[70vh] overflow-y-auto">
            {rows.length === 0 && <p className="text-sm text-muted-foreground">No messages.</p>}
            {rows.map((m) => (
              <button
                key={String(m.id)}
                type="button"
                onClick={() => setSelected(m)}
                className={`w-full rounded-md border px-3 py-2 text-left text-sm ${
                  selected?.id === m.id ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40"
                }`}
              >
                <div className="flex justify-between gap-2 text-xs text-muted-foreground">
                  <span>{String(m.direction)}</span>
                  <span className="font-mono">{String(m.created_at || "").slice(0, 19)}</span>
                </div>
                <p className="font-medium text-foreground truncate">{String(m.subject)}</p>
                <p className="text-muted-foreground truncate">{String(m.to_addr)}</p>
                {m.case_id ? (
                  <Link
                    to={`/agent/cases/${String(m.case_id)}`}
                    className="text-xs text-primary hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Open Trace Studio
                  </Link>
                ) : null}
              </button>
            ))}
          </div>
          <div className="lummus-card">
            {!selected && <p className="text-sm text-muted-foreground">Select a message.</p>}
            {selected && (
              <div className="space-y-2 text-sm">
                <p className="font-semibold text-foreground">{String(selected.subject)}</p>
                <p className="text-xs text-muted-foreground font-mono">
                  From {String(selected.from_addr)} → {String(selected.to_addr)}
                </p>
                <pre className="lummus-code whitespace-pre-wrap">{String(selected.body)}</pre>
                <pre className="lummus-code text-xs">{JSON.stringify(selected.headers, null, 2)}</pre>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
