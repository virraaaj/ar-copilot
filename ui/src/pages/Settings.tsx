// Merges the two remaining admin/config pages (Project Contacts, Default
// Project Contacts) into one nav tab (added 2026-07-23, nav consolidation).
// Escalation Policy was a third tab here until the dunning/stage-escalation
// system was retired in favor of the chase agent (removed 2026-07-23) --
// see AgentPhaseRail on InvoiceDetail/Dashboard for what replaced it.
import { useState } from "react";
import ProjectContacts from "./ProjectContacts";
import DefaultProjectContacts from "./DefaultProjectContacts";
import PolicyConfig from "./PolicyConfig";

type Tab = "contacts" | "defaults" | "policy";

const TABS: { id: Tab; label: string }[] = [
  { id: "contacts", label: "Project Contacts" },
  { id: "defaults", label: "Default Contacts" },
  { id: "policy", label: "Agent Policy" },
];

export default function Settings() {
  const [tab, setTab] = useState<Tab>("contacts");

  return (
    <div>
      <div className="mx-auto max-w-4xl px-6 pt-8">
        <div className="mb-2 flex gap-1 border-b border-zinc-200/70">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`-mb-px border-b-2 px-3.5 py-2.5 text-[13px] font-medium transition-colors ${
                tab === t.id
                  ? "border-zinc-900 text-zinc-900"
                  : "border-transparent text-zinc-500 hover:text-zinc-800"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "contacts" && <ProjectContacts />}
      {tab === "defaults" && <DefaultProjectContacts />}
      {tab === "policy" && <PolicyConfig />}
    </div>
  );
}
