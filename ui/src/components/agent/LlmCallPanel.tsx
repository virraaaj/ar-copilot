type LlmCall = {
  name?: string;
  mode?: string;
  request?: { messages?: unknown; tools?: unknown; tool_choice?: unknown };
  response?: { tool_args?: unknown; result?: unknown; error?: unknown };
  tokens?: { input?: number; output?: number; total?: number } | number;
  prompt_tokens?: number;
  completion_tokens?: number;
  messages?: unknown;
  args?: unknown;
  result?: unknown;
  error?: unknown;
};

// Surfaces the "why" a step's own result already carries (rationale,
// judge notes/failures, reply classification) as plain-language lines
// instead of making the user dig for it inside the raw JSON below. Added
// 2026-08-06 (user feedback): Trace Studio showed the decision but not the
// reasoning behind it.
function reasoningLines(result: unknown): Array<{ label: string; value: string }> {
  if (!result || typeof result !== "object") return [];
  const r = result as Record<string, unknown>;
  const lines: Array<{ label: string; value: string }> = [];
  if (r.selected_tactic) lines.push({ label: "Chose tactic", value: String(r.selected_tactic) });
  if (r.objective) lines.push({ label: "Objective", value: String(r.objective) });
  if (r.rationale) lines.push({ label: "Why", value: String(r.rationale) });
  if (typeof r.passed === "boolean") lines.push({ label: "Judge result", value: r.passed ? "Passed" : "Failed" });
  if (Array.isArray(r.failures) && r.failures.length) lines.push({ label: "Failed checks", value: r.failures.join(", ") });
  if (r.notes) lines.push({ label: "Judge notes", value: String(r.notes) });
  if (r.reply_type) lines.push({ label: "Classified reply as", value: String(r.reply_type) });
  if (r.summary) lines.push({ label: "Summary", value: String(r.summary) });
  if (r.blocker_description) lines.push({ label: "Blocker", value: String(r.blocker_description) });
  if (typeof r.authorizes_customer_contact === "boolean") {
    lines.push({ label: "Authorized customer contact", value: r.authorizes_customer_contact ? "Yes" : "No" });
  }
  return lines;
}

function tokenSplit(call: LlmCall): { input: number; output: number; total: number } {
  if (call.tokens && typeof call.tokens === "object") {
    return {
      input: Number(call.tokens.input || 0),
      output: Number(call.tokens.output || 0),
      total: Number(call.tokens.total || 0),
    };
  }
  const input = Number(call.prompt_tokens || 0);
  const output = Number(call.completion_tokens || 0);
  const total = typeof call.tokens === "number" ? call.tokens : input + output;
  return { input, output, total };
}

export default function LlmCallPanel({ call }: { call: LlmCall }) {
  const request = call.request || { messages: call.messages };
  const response = call.response || { tool_args: call.args, result: call.result, error: call.error };
  const tokens = tokenSplit(call);
  const reasoning = reasoningLines(response.result ?? response.tool_args);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-primary">{call.name || "llm"}</span>
        <span className="text-muted-foreground">· {call.mode || "unknown"}</span>
      </div>

      {reasoning.length > 0 && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 space-y-1.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-primary">Reasoning</p>
          {reasoning.map((line) => (
            <p key={line.label} className="text-sm leading-snug">
              <span className="text-muted-foreground">{line.label}: </span>
              <span className="text-foreground">{line.value}</span>
            </p>
          ))}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-md border border-border bg-secondary/50 px-2 py-1.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Input tokens</p>
          <p className="font-mono text-sm text-foreground tabular-nums">{tokens.input}</p>
        </div>
        <div className="rounded-md border border-border bg-secondary/50 px-2 py-1.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Output tokens</p>
          <p className="font-mono text-sm text-foreground tabular-nums">{tokens.output}</p>
        </div>
        <div className="rounded-md border border-primary/40 bg-primary/10 px-2 py-1.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Total</p>
          <p className="font-mono text-sm text-primary tabular-nums">{tokens.total}</p>
        </div>
      </div>

      <div>
        <p className="text-xs uppercase tracking-wider text-muted-foreground mb-1">Sent to model</p>
        <pre className="lummus-code max-h-40 overflow-auto">
          {JSON.stringify(
            {
              messages: request.messages,
              tools: request.tools,
              tool_choice: request.tool_choice,
            },
            null,
            2
          )}
        </pre>
      </div>

      <div>
        <p className="text-xs uppercase tracking-wider text-muted-foreground mb-1">Returned from model</p>
        <pre className="lummus-code max-h-40 overflow-auto">
          {JSON.stringify(
            {
              tool_args: response.tool_args,
              result: response.result,
              error: response.error,
            },
            null,
            2
          )}
        </pre>
      </div>
    </div>
  );
}
