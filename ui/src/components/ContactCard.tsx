// Shared building blocks for the Project Contacts and Default Project
// Contacts pages (added 2026-07-16) -- deliberately mirrors the design of
// Lummus's own equivalent pages: a colored-accent card grid per contact
// (rotating through a fixed palette by position), a dashed "add" tile, and
// a simple modal form for add/edit.
import { useState } from "react";
import { CONTACT_TYPE_LABELS, CONTACT_TYPES, type Contact } from "../api";

export const CONTACT_ACCENTS = [
  { border: "border-blue-200", text: "text-blue-600" },
  { border: "border-amber-200 dark:border-amber-800", text: "text-amber-600 dark:text-amber-400" },
  { border: "border-emerald-200 dark:border-emerald-800", text: "text-emerald-600 dark:text-emerald-400" },
  { border: "border-purple-200", text: "text-purple-600" },
  { border: "border-rose-200 dark:border-rose-800", text: "text-rose-600 dark:text-rose-400" },
];

export function ContactCard({
  contact,
  accentIndex,
  onEdit,
  onDelete,
}: {
  contact: Contact;
  accentIndex: number;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const accent = CONTACT_ACCENTS[accentIndex % CONTACT_ACCENTS.length];
  return (
    <div className={`flex flex-col rounded-xl border ${accent.border} bg-white dark:bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className={`text-[11px] font-semibold uppercase tracking-wide ${accent.text}`}>
            {CONTACT_TYPE_LABELS[contact.contact_type] ?? contact.contact_type}
          </div>
          <div className="mt-0.5 truncate text-[14px] font-medium text-zinc-900 dark:text-zinc-100">{contact.name || "Unnamed"}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            onClick={onEdit}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-zinc-400 dark:text-zinc-500 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60 hover:text-zinc-700 dark:hover:text-zinc-300"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-zinc-400 dark:text-zinc-500 transition-colors hover:bg-rose-50 dark:hover:bg-rose-950/40 hover:text-rose-600 dark:hover:text-rose-400"
          >
            Delete
          </button>
        </div>
      </div>
      <div className="mt-3 space-y-1.5 border-t border-zinc-100 dark:border-zinc-800 pt-3 text-[12px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-400 dark:text-zinc-500">Email</span>
          <span className="truncate text-zinc-700 dark:text-zinc-300">{contact.email || "—"}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-400 dark:text-zinc-500">Phone</span>
          <span className="truncate text-zinc-700 dark:text-zinc-300">{contact.phone || "—"}</span>
        </div>
      </div>
    </div>
  );
}

export function AddContactTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex min-h-[104px] flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-zinc-200 dark:border-zinc-700 text-zinc-400 dark:text-zinc-500 transition-colors hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/60 hover:text-zinc-600 dark:hover:text-zinc-300"
    >
      <span className="text-[20px] leading-none">+</span>
      <span className="text-[12px] font-medium">Add contact</span>
    </button>
  );
}

export function EmptyContactsState({ onAddFirst }: { onAddFirst: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-200 dark:border-zinc-700 py-8 text-center">
      <p className="text-[13px] text-zinc-400 dark:text-zinc-500">No contacts yet.</p>
      <button
        onClick={onAddFirst}
        className="mt-3 rounded-full border border-zinc-200 dark:border-zinc-700 px-4 py-1.5 text-[13px] font-medium text-zinc-700 dark:text-zinc-300 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
      >
        Add first contact
      </button>
    </div>
  );
}

export interface ContactFormValues {
  contact_type: string;
  name: string;
  email: string;
  phone: string;
}

export function ContactFormModal({
  title,
  subtitle,
  initial,
  usedRoles,
  isEdit,
  submitting,
  error,
  onClose,
  onSubmit,
}: {
  title: string;
  subtitle?: string;
  initial: ContactFormValues;
  usedRoles: string[]; // roles already assigned in this scope -- excluded from the Add dropdown
  isEdit: boolean;
  submitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (values: ContactFormValues) => void;
}) {
  const [values, setValues] = useState<ContactFormValues>(initial);
  const availableRoles = CONTACT_TYPES.filter((t) => isEdit || t === initial.contact_type || !usedRoles.includes(t));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/30 dark:bg-black/60 px-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.15)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">{title}</h2>
        {subtitle && <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">{subtitle}</p>}

        <label className="mb-1.5 mt-5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Role</label>
        <select
          disabled={isEdit}
          value={values.contact_type}
          onChange={(e) => setValues((v) => ({ ...v, contact_type: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10 disabled:bg-zinc-50 disabled:text-zinc-400"
        >
          {availableRoles.map((t) => (
            <option key={t} value={t}>
              {CONTACT_TYPE_LABELS[t]}
            </option>
          ))}
        </select>

        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Name</label>
        <input
          autoFocus={!isEdit}
          value={values.name}
          onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Email</label>
        <input
          type="email"
          value={values.email}
          onChange={(e) => setValues((v) => ({ ...v, email: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600 dark:text-zinc-300">Phone</label>
        <input
          value={values.phone}
          onChange={(e) => setValues((v) => ({ ...v, phone: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-[14px] text-zinc-900 dark:text-zinc-100 outline-none transition-shadow focus:border-zinc-400 dark:focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 dark:focus:ring-zinc-100/10"
        />
        {error && <p className="mb-4 rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => onSubmit(values)}
            disabled={submitting}
            className="rounded-full bg-green-700 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-green-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "Saving..." : "Save"}
          </button>
          <button
            onClick={onClose}
            className="rounded-full px-4 py-2 text-[13px] font-medium text-zinc-500 dark:text-zinc-400 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
