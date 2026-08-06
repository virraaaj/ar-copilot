const LABELS: Record<string, string> = {
  P1: "World ≠ dialogue",
  P2: "Commitment-centric",
  P3: "Bounded autonomy",
  P4: "Critic before act",
  P5: "Judge after outcome",
  P6: "Memory as retrieval",
  P7: "Explicit uncertainty",
  P8: "Hierarchical goals",
  P9: "Simulate-then-act",
  P10: "Delayed credit",
  P11: "Escalation handoff",
  P12: "Det. skeleton / AI",
  P13: "Reflexion",
};

export default function PrincipleTrace({ principles }: { principles: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {(principles || []).map((p) => (
        <span
          key={p}
          title={LABELS[p] || p}
          className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
        >
          {p}
        </span>
      ))}
    </div>
  );
}
