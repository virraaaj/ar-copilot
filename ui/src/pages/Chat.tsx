import { useState, useRef, useEffect, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { streamChat, listProjects, ApiError, type ChatHistoryMessage, type Project } from "../api";
import { useSession } from "../context/SessionContext";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Eyebrow } from "../components/ui/Eyebrow";
import { PageHeader } from "../components/ui/PageHeader";

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
      <span className="h-1.5 w-1.5 animate-bounce bg-muted-foreground [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce bg-muted-foreground [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce bg-muted-foreground" />
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

// Turn timestamp, purely relative to when this session's messages array
// grows -- we don't get a server timestamp per streamed event, so this is
// "time the bubble appeared in this browser tab", shown mono like every
// other timestamp in the app for consistency.
function nowLabel(): string {
  return new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function Chat() {
  const { token, pinnedInvoice, setPinnedInvoice, currentProject, setCurrentProject } = useSession();
  const [messages, setMessages] = useState<(DisplayMessage & { at: string })[]>([]);
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
      .filter((m): m is DisplayMessage & { role: "user" | "assistant"; at: string } => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role, content: m.content }));

    setInput("");
    setMessages((m) => [...m, { role: "user", content: question, at: nowLabel() }, { role: "progress", content: "Thinking", at: nowLabel() }]);
    setSending(true);

    try {
      for await (const event of streamChat(token, question, currentProject, pinnedInvoice ?? undefined, priorHistory)) {
        if (event.type === "tool_call") {
          // Replace the standing "Thinking" bubble with what it's actually
          // doing, rather than stacking a new bubble on top of it.
          setMessages((m) => [
            ...m.filter((msg) => msg.role !== "progress"),
            { role: "progress", content: `Checking ${event.name.replace(/_/g, " ")}`, at: nowLabel() },
          ]);
        } else if (event.type === "answer") {
          setMessages((m) => [
            ...m.filter((msg) => msg.role !== "progress"),
            { role: "assistant", content: event.content, at: nowLabel() },
          ]);
        }
      }
    } catch (err) {
      setMessages((m) => [
        ...m.filter((msg) => msg.role !== "progress"),
        { role: "assistant", content: err instanceof ApiError ? `Error: ${err.message}` : "Something went wrong.", at: nowLabel() },
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
    <div className="mx-auto flex h-[calc(100vh-57px)] max-w-2xl flex-col px-6 py-8 sm:px-12">
      <div className="mb-1 flex items-start justify-between gap-4">
        <div>
          <Eyebrow tone="accent" as="p" className="mb-2">Chat</Eyebrow>
          <p className="text-sm text-muted-foreground">
            About <span className="font-medium text-foreground">{currentProject.project_name ?? currentProject.project_number}</span>
          </p>
        </div>
        <button
          onClick={changeProject}
          className="mt-1 shrink-0 font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground underline decoration-border decoration-1 underline-offset-4 transition-colors duration-150 ease-bold hover:text-foreground hover:decoration-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Change project
        </button>
      </div>

      {pinnedInvoice && (
        <div className="mt-5 flex items-center justify-between gap-3 border border-border px-4 py-3 text-sm">
          <span className="min-w-0 truncate text-muted-foreground">
            Re: <span className="font-medium text-foreground">{pinnedInvoice.label}</span>
          </span>
          <button
            onClick={() => setPinnedInvoice(null)}
            aria-label="Clear pinned invoice"
            className="flex h-6 w-6 shrink-0 items-center justify-center text-muted-foreground transition-colors duration-150 ease-bold hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            &times;
          </button>
        </div>
      )}

      {pinnedInvoice && messages.length === 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {PINNED_INVOICE_SUGGESTIONS.map((q) => (
            <button
              key={q}
              onClick={() => sendQuestion(q)}
              className="border border-border px-3.5 py-2 text-left text-xs font-medium text-muted-foreground transition-colors duration-150 ease-bold hover:border-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      <div className="mt-5 flex-1 overflow-y-auto border border-border">
        {messages.length === 0 && (
          <p className="px-5 py-6 text-sm text-muted-foreground">
            {pinnedInvoice
              ? "Pick a question above, or ask your own."
              : 'Ask about invoices, documents, or aging — e.g. "which invoices are overdue past 60 days?"'}
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`border-b border-border px-5 py-4 last:border-0 ${m.role === "user" ? "bg-muted/40" : ""}`}>
            <div className="mb-1.5 flex items-baseline gap-2">
              <Eyebrow tone={m.role === "user" ? "accent" : "muted"}>
                {m.role === "user" ? "You" : "Agent"}
              </Eyebrow>
              <span className="font-mono text-[10px] text-muted-foreground">{m.at}</span>
            </div>
            {m.role === "progress" ? (
              <span className="inline-flex items-center gap-2 text-sm italic text-muted-foreground">
                {m.content}
                <ThinkingDots />
              </span>
            ) : (
              <p className="whitespace-pre-wrap text-base leading-normal text-foreground">{m.content}</p>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="mt-4 flex gap-3">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question…"
          disabled={sending}
          dense
          className="flex-1"
        />
        <Button type="submit" variant="secondary" size="sm" disabled={sending || !input.trim()} className="shrink-0">
          Send
        </Button>
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
    <div className="mx-auto max-w-2xl px-6 py-10 sm:px-12">
      <PageHeader eyebrow="Chat" title="Pick a project" description="Invoices and documents both come from it." />

      {error && (
        <p className="mb-4 border border-accent px-4 py-3 text-sm text-accent" role="alert">{error}</p>
      )}
      {!projects && !error && <p className="text-sm text-muted-foreground">Loading projects…</p>}

      <div className="space-y-2">
        {projects?.map((p) => (
          <button
            key={p.project_number}
            onClick={() => onPick(p)}
            className="flex w-full items-center gap-3 border border-border p-4 text-left transition-colors duration-150 ease-bold hover:border-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center border border-border font-mono text-xs font-semibold text-foreground">
              {(p.project_name ?? p.project_number).slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{p.project_name ?? p.project_number}</p>
              <p className="font-mono text-xs text-muted-foreground">{p.project_number}</p>
            </div>
          </button>
        ))}
        {projects && projects.length === 0 && <p className="text-sm text-muted-foreground">No projects found.</p>}
      </div>
    </div>
  );
}
