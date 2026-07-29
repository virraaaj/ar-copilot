import { useState } from "react";

export default function ContextPacketViewer({ packet }: { packet: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[12px] font-semibold uppercase tracking-wide text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
      >
        Context packet {open ? "▾" : "▸"} <span className="text-sky-600">P6/P7</span>
      </button>
      {open && packet ? (
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-zinc-50 p-2 text-[11px] text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          {JSON.stringify(packet, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
