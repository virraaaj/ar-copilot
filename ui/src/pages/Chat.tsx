import { useState, useRef, type FormEvent } from "react";
import { streamChat, ApiError } from "../api";
import { useSession } from "../context/SessionContext";

interface DisplayMessage {
  role: "user" | "assistant" | "progress";
  content: string;
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
    setMessages((m) => [...m, { role: "user", content: question }]);
    setSending(true);

    try {
      for await (const event of streamChat(token, question, pinnedInvoice ?? undefined)) {
        if (event.type === "tool_call") {
          setMessages((m) => [...m, { role: "progress", content: `Checking ${event.name.replace(/_/g, " ")}...` }]);
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
    <div className="mx-auto flex h-[calc(100vh-49px)] max-w-2xl flex-col p-6">
      <h1 className="mb-3 text-xl font-semibold text-slate-800">Chat</h1>

      {pinnedInvoice && (
        <div className="mb-3 flex items-center justify-between rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-600">
          <span>
            Re: <span className="font-medium text-slate-800">{pinnedInvoice.label}</span>
          </span>
          <button onClick={() => setPinnedInvoice(null)} className="text-slate-400 hover:text-slate-600">
            &times;
          </button>
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-slate-200 bg-white p-4">
        {messages.length === 0 && (
          <p className="text-sm text-slate-400">
            Ask about invoices, documents, or aging -- e.g. "which invoices are overdue past 60 days?"
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <span
              className={`inline-block max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-slate-800 text-white"
                  : m.role === "progress"
                    ? "italic text-slate-400"
                    : "bg-slate-100 text-slate-800"
              }`}
            >
              {m.content}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question..."
          disabled={sending}
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  );
}
