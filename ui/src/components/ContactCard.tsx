// Shared building blocks for the Project Contacts and Default Project
// Contacts pages (added 2026-07-16) -- deliberately mirrors the design of
// Lummus's own equivalent pages: a colored-accent card grid per contact
// (rotating through a fixed palette by position), a dashed "add" tile, and
// a simple modal form for add/edit.
import { useState } from "react";
import { CONTACT_TYPE_LABELS, CONTACT_TYPES, type Contact } from "../api";

export const CONTACT_ACCENTS = [
  { border: "border-blue-200", text: "text-blue-600" },
  { border: "border-amber-200", text: "text-amber-600" },
  { border: "border-emerald-200", text: "text-emerald-600" },
  { border: "border-purple-200", text: "text-purple-600" },
  { border: "border-rose-200", text: "text-rose-600" },
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
    <div className={`flex flex-col rounded-xl border ${accent.border} bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className={`text-[11px] font-semibold uppercase tracking-wide ${accent.text}`}>
            {CONTACT_TYPE_LABELS[contact.contact_type] ?? contact.contact_type}
          </div>
          <div className="mt-0.5 truncate text-[14px] font-medium text-zinc-900">{contact.name || "Unnamed"}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            onClick={onEdit}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:bg-zinc-50 hover:text-zinc-700"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="rounded-md px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:bg-rose-50 hover:text-rose-600"
          >
            Delete
          </button>
        </div>
      </div>
      <div className="mt-3 space-y-1.5 border-t border-zinc-100 pt-3 text-[12px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-400">Email</span>
          <span className="truncate text-zinc-700">{contact.email || "—"}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-400">Phone</span>
          <span className="truncate text-zinc-700">{contact.phone || "—"}</span>
        </div>
      </div>
    </div>
  );
}

export function AddContactTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex min-h-[104px] flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-zinc-200 text-zinc-400 transition-colors hover:border-zinc-300 hover:bg-zinc-50 hover:text-zinc-600"
    >
      <span className="text-[20px] leading-none">+</span>
      <span className="text-[12px] font-medium">Add contact</span>
    </button>
  );
}

export function EmptyContactsState({ onAddFirst }: { onAddFirst: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-200 py-8 text-center">
      <p className="text-[13px] text-zinc-400">No contacts yet.</p>
      <button
        onClick={onAddFirst}
        className="mt-3 rounded-full border border-zinc-200 px-4 py-1.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50"
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/30 px-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-200/70 bg-white p-7 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.15)]"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900">{title}</h2>
        {subtitle && <p className="mt-1 text-[12px] text-zinc-400">{subtitle}</p>}

        <label className="mb-1.5 mt-5 block text-[13px] font-medium text-zinc-600">Role</label>
        <select
          disabled={isEdit}
          value={values.contact_type}
          onChange={(e) => setValues((v) => ({ ...v, contact_type: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 bg-white px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5 disabled:bg-zinc-50 disabled:text-zinc-400"
        >
          {availableRoles.map((t) => (
            <option key={t} value={t}>
              {CONTACT_TYPE_LABELS[t]}
            </option>
          ))}
        </select>

        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600">Name</label>
        <input
          autoFocus={!isEdit}
          value={values.name}
          onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600">Email</label>
        <input
          type="email"
          value={values.email}
          onChange={(e) => setValues((v) => ({ ...v, email: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
        />
        <label className="mb-1.5 block text-[13px] font-medium text-zinc-600">Phone</label>
        <input
          value={values.phone}
          onChange={(e) => setValues((v) => ({ ...v, phone: e.target.value }))}
          className="mb-4 w-full rounded-lg border border-zinc-200 px-3.5 py-2.5 text-[14px] text-zinc-900 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
        />
        {error && <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => onSubmit(values)}
            disabled={submitting}
            className="rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "Saving..." : "Save"}
          </button>
          <button
            onClick={onClose}
            className="rounded-full px-4 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
