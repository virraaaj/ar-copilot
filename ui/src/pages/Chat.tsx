import { useState, useRef, type FormEvent } from "react";
import { streamChat, ApiError } from "../api";
import { useSession } from "../context/SessionContext";

interface DisplayMessage {
  role: "user" | "assistant" | "progress";
  content: string;
}

// A small bouncing-dot "still working on it" indicator, the same idea as
// Claude's own thinking indicator -- shown the instant a question is sent
// (before any tool-call progress event has arrived) so the chat never sits
// silent while the agent reasons.
function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400" />
    </span>
  );
}

export default function Chat() {
  const { token, pinnedInvoice, setPinnedInvoice } = useSession();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token || !input.trim() || sending) return;

    const question = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content: question }, { role: "progress", content: "Thinking" }]);
    setSending(true);

    try {
      for await (const event of streamChat(token, question, pinnedInvoice ?? undefined)) {
        if (event.type === "tool_call") {
          // Replace the standing "Thinking" bubble with what it's actually
          // doing, rather than stacking a new bubble on top of it.
          setMessages((m) => [
            ...m.filter((msg) => msg.role !== "progress"),
            { role: "progress", content: `Checking ${event.name.replace(/_/g, " ")}` },
          ]);
        } else if (event.type === "answer") {
          setMessages((m) => [
            ...m.filter((msg) => msg.role !== "progress"),
            { role: "assistant", content: event.content },
          ]);
        }
      }
    } catch (err) {
      setMessages((m) => [
        ...m.filter((msg) => msg.role !== "progress"),
        { role: "assistant", content: err instanceof ApiError ? `Error: ${err.message}` : "Something went wrong." },
      ]);
    } finally {
      setSending(false);
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-57px)] max-w-2xl flex-col px-6 py-8">
      <h1 className="mb-4 font-display text-[22px] font-semibold tracking-tight text-zinc-900">Chat</h1>

      {pinnedInvoice && (
        <div className="mb-4 flex items-center justify-between rounded-xl border border-zinc-200/70 bg-white px-4 py-2.5 text-[13px] shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <span className="text-zinc-500">
            Re: <span className="font-medium text-zinc-900">{pinnedInvoice.label}</span>
          </span>
          <button
            onClick={() => setPinnedInvoice(null)}
            className="flex h-5 w-5 items-center justify-center rounded-full text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
          >
            &times;
          </button>
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto rounded-2xl border border-zinc-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        {messages.length === 0 && (
          <p className="text-[13px] text-zinc-400">
            Ask about invoices, documents, or aging — e.g. "which invoices are overdue past 60 days?"
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <span
              className={`inline-block max-w-[85%] rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed ${
                m.role === "user"
                  ? "bg-zinc-900 text-white"
                  : m.role === "progress"
                    ? "flex items-center gap-2 italic text-zinc-400"
                    : "bg-zinc-100 text-zinc-800"
              }`}
            >
              {m.role === "progress" ? (
                <>
                  {m.content}
                  <ThinkingDots />
                </>
              ) : (
                m.content
              )}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="mt-4 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question..."
          disabled={sending}
          className="flex-1 rounded-xl border border-zinc-200 bg-white px-4 py-2.5 text-[14px] outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-xl bg-zinc-900 px-5 py-2.5 text-[14px] font-medium text-white transition-colors hover:bg-zinc-800 disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}
