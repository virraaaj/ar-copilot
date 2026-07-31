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

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-primary">{call.name || "llm"}</span>
        <span className="text-muted-foreground">· {call.mode || "unknown"}</span>
      </div>

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
