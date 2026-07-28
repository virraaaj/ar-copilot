import { useState, useRef, useEffect, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { streamChat, listProjects, ApiError, type ChatHistoryMessage, type Project } from "../api";
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
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 dark:bg-zinc-500 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 dark:bg-zinc-500 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 dark:bg-zinc-500" />
    </span>
  );
}

// Shown as one-click chips once an invoice is pinned (the "Ask about this"
// handoff from Dashboard/InvoiceDetail) and before the user has typed
// anything -- the questions someone actually reaches for first when they
// land here about one specific invoice, so they don't have to type them.
const PINNED_INVOICE_SUGGESTIONS = [
  "When is this due?",
  "What's the current stage, and what happens next?",
  "How much is outstanding?",
  "Has the customer replied recently?",
  "Summarize recent activity on this invoice.",
  "Who are the contacts for this project?",
];

export default function Chat() {
  const { token, pinnedInvoice, setPinnedInvoice, currentProject, setCurrentProject } = useSession();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const initialQuestionSent = useRef(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!input.trim()) return;
    await sendQuestion(input.trim());
  }

  async function sendQuestion(question: string) {
    if (!token || sending || !currentProject) return;
    // Prior turns only (this question isn't in `messages` yet) -- lets the
    // agent understand a reply like "paying next week" in the context of
    // its own preceding question ("what would you like the comment to
    // say?") instead of treating every message as a fresh conversation.
    const priorHistory: ChatHistoryMessage[] = messages
      .filter((m): m is DisplayMessage & { role: "user" | "assistant" } => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role, content: m.content }));

    setInput("");
    setMessages((m) => [...m, { role: "user", content: question }, { role: "progress", content: "Thinking" }]);
    setSending(true);

    try {
      for await (const event of streamChat(token, question, currentProject, pinnedInvoice ?? undefined, priorHistory)) {
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

  useEffect(() => {
    // Set by a page handing off a pre-formed question (e.g. a "Chat about
    // this project" action) -- consumed once, then cleared from history
    // state so navigating back here later doesn't resend it. Needs
    // currentProject too now, since sendQuestion requires it.
    const initialQuestion = (location.state as { initialQuestion?: string } | null)?.initialQuestion;
    if (initialQuestion && !initialQuestionSent.current && token && currentProject) {
      initialQuestionSent.current = true;
      sendQuestion(initialQuestion);
      navigate(location.pathname, { replace: true, state: {} });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, currentProject]);

  function changeProject() {
    setCurrentProject(null);
    setPinnedInvoice(null);
    setMessages([]);
  }

  if (!currentProject) {
    return <ProjectPicker onPick={setCurrentProject} />;
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-57px)] max-w-2xl flex-col px-6 py-8">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="font-display text-[22px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Chat</h1>
        <button
          onClick={changeProject}
          className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1 text-[12px] font-medium text-zinc-500 dark:text-zinc-400 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          Change project
        </button>
      </div>
      <p className="-mt-2 mb-4 text-[13px] text-zinc-400 dark:text-zinc-500">
        Chatting about <span className="font-medium text-zinc-700 dark:text-zinc-300">{currentProject.project_name ?? currentProject.project_number}</span>
      </p>

      {pinnedInvoice && (
        <div className="mb-4 flex items-center justify-between rounded-xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-2.5 text-[13px] shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
          <span className="text-zinc-500 dark:text-zinc-400">
            Re: <span className="font-medium text-zinc-900 dark:text-zinc-100">{pinnedInvoice.label}</span>
          </span>
          <button
            onClick={() => setPinnedInvoice(null)}
            className="flex h-5 w-5 items-center justify-center rounded-full text-zinc-400 dark:text-zinc-500 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-700 dark:hover:text-zinc-300"
          >
            &times;
          </button>
        </div>
      )}

      {pinnedInvoice && messages.length === 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {PINNED_INVOICE_SUGGESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => sendQuestion(q)}
              className="rounded-full border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3.5 py-1.5 text-[12.5px] font-medium text-zinc-600 dark:text-zinc-300 transition-colors hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 hover:text-zinc-900 dark:hover:text-zinc-100"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        {messages.length === 0 && (
          <p className="text-[13px] text-zinc-400 dark:text-zinc-500">
            {pinnedInvoice
              ? "Pick a question above, or ask your own."
              : 'Ask about invoices, documents, or aging — e.g. "which invoices are overdue past 60 days?"'}
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <span
              className={`inline-block max-w-[85%] rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed ${
                m.role === "user"
                  ? "bg-green-700 text-white"
                  : m.role === "progress"
                    ? "flex items-center gap-2 italic text-zinc-400 dark:text-zinc-500"
                    : "bg-zinc-100 dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200"
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
          className="flex-1 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-2.5 text-[14px] outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-xl bg-green-700 px-5 py-2.5 text-[14px] font-medium text-white transition-colors hover:bg-green-800 disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}

// Gate shown before a project is picked -- chat can't proceed without one
// (project-scoped chat, added 2026-07-23). Same project list Documents'
// folder view uses.
function ProjectPicker({ onPick }: { onPick: (project: Project) => void }) {
  const { token } = useSession();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listProjects(token).then(setProjects).catch((e) => setError(String(e)));
  }, [token]);

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="mb-1 font-display text-[22px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Chat</h1>
      <p className="mb-6 text-[14px] text-zinc-400 dark:text-zinc-500">Pick a project to chat about — invoices and documents both come from it.</p>

      {error && <p className="mb-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
      {!projects && !error && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading projects...</p>}

      <div className="space-y-2">
        {projects?.map((p) => (
          <button
            key={p.project_number}
            onClick={() => onPick(p)}
            className="flex w-full items-center gap-3 rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4 text-left shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-zinc-900 dark:bg-zinc-700 text-[13px] font-semibold text-white">
              {(p.project_name ?? p.project_number).slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0">
              <p className="truncate text-[14px] font-medium text-zinc-900 dark:text-zinc-100">{p.project_name ?? p.project_number}</p>
              <p className="text-[12px] text-zinc-400 dark:text-zinc-500">{p.project_number}</p>
            </div>
          </button>
        ))}
        {projects && projects.length === 0 && <p className="text-[13px] text-zinc-400 dark:text-zinc-500">No projects found.</p>}
      </div>
    </div>
  );
}
