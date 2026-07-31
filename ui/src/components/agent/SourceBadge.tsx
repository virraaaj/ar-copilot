const LABELS: Record<string, { label: string; className: string }> = {
  postgres: { label: "Postgres", className: "src-badge src-badge-postgres" },
  cosmos_gremlin: { label: "Cosmos Gremlin", className: "src-badge src-badge-gremlin" },
  sqlite: { label: "SQLite", className: "src-badge src-badge-sqlite" },
  policy_index: { label: "Policy index", className: "src-badge src-badge-policy" },
  runtime: { label: "Runtime", className: "src-badge src-badge-runtime" },
  document: { label: "Policy index", className: "src-badge src-badge-policy" },
};

export function SourceBadge({ backend }: { backend?: string }) {
  const key = (backend || "runtime").toLowerCase();
  const meta = LABELS[key] || { label: key, className: "src-badge src-badge-runtime" };
  return <span className={meta.className}>{meta.label}</span>;
}

export function backendsFromStep(step: Record<string, unknown> | null): string[] {
  if (!step) return [];
  const found = new Set<string>();
  for (const item of [...((step.reads as Array<Record<string, unknown>>) || []), ...((step.writes as Array<Record<string, unknown>>) || [])]) {
    if (item?.backend) found.add(String(item.backend));
  }
  return [...found];
}

type IoItem = {
  store?: string;
  key?: string;
  summary?: string;
  backend?: string;
  payload?: unknown;
};

export function IoList({ title, items }: { title: string; items: IoItem[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-muted-foreground mb-1">{title}</p>
      <ul className="space-y-2 max-h-56 overflow-auto">
        {items.map((item, idx) => (
          <li key={`${item.store}-${item.key}-${idx}`} className="rounded-md border border-border bg-secondary/40 p-2">
            <div className="flex flex-wrap items-center gap-2">
              <SourceBadge backend={item.backend} />
              <span className="font-mono text-[11px] text-primary">
                {item.store}/{item.key}
              </span>
            </div>
            <p className="mt-1 text-xs text-foreground">{item.summary}</p>
            {item.payload != null && (
              <details className="mt-1">
                <summary className="cursor-pointer text-[11px] text-muted-foreground">payload</summary>
                <pre className="lummus-code mt-1 max-h-32 overflow-auto text-[10px]">
                  {JSON.stringify(item.payload, null, 2)}
                </pre>
              </details>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
