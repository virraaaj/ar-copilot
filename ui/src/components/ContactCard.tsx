// Shared building blocks for the Project Contacts and Default Project
// Contacts pages (added 2026-07-16, converted to Bold Typography
// 2026-09-04) -- a bordered card per contact (role shown as a mono
// Eyebrow, not a color-coded accent -- this system reserves color for
// state/accent, not for arbitrary role identity), a dashed "add" tile,
// and a simple modal form for add/edit built on the shared Input/Button
// primitives.
import { useState } from "react";
import { Pencil, Trash2, Plus } from "lucide-react";
import { CONTACT_TYPE_LABELS, CONTACT_TYPES, type Contact } from "../api";
import { Card } from "./ui/Card";
import { Button } from "./ui/Button";
import { Input, Select } from "./ui/Input";
import { Eyebrow } from "./ui/Eyebrow";

export function ContactCard({
  contact,
  onEdit,
  onDelete,
}: {
  contact: Contact;
  accentIndex?: number;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <Card className="p-4 md:p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Eyebrow tone="accent">{CONTACT_TYPE_LABELS[contact.contact_type] ?? contact.contact_type}</Eyebrow>
          <p className="mt-1 truncate text-base font-medium text-foreground">{contact.name || "Unnamed"}</p>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            onClick={onEdit}
            aria-label="Edit contact"
            className="flex h-11 w-11 items-center justify-center text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Pencil size={16} strokeWidth={1.5} aria-hidden />
          </button>
          <button
            onClick={onDelete}
            aria-label="Delete contact"
            className="flex h-11 w-11 items-center justify-center text-muted-foreground transition-colors duration-150 ease-bold hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Trash2 size={16} strokeWidth={1.5} aria-hidden />
          </button>
        </div>
      </div>
      <div className="mt-3 space-y-1.5 border-t border-border pt-3 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">Email</span>
          <span className="truncate text-foreground">{contact.email || "--"}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">Phone</span>
          <span className="truncate text-foreground">{contact.phone || "--"}</span>
        </div>
      </div>
    </Card>
  );
}

export function AddContactTile({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex min-h-[112px] flex-col items-center justify-center gap-1.5 border border-dashed border-border text-muted-foreground transition-colors duration-150 ease-bold hover:border-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Plus size={18} strokeWidth={1.5} aria-hidden />
      <span className="font-mono text-xs font-medium uppercase tracking-wider">Add contact</span>
    </button>
  );
}

export function EmptyContactsState({ onAddFirst }: { onAddFirst: () => void }) {
  return (
    <div className="border border-dashed border-border py-8 text-center">
      <p className="text-sm text-muted-foreground">No contacts yet.</p>
      <div className="mt-3 flex justify-center">
        <Button variant="secondary" size="sm" onClick={onAddFirst}>
          Add first contact
        </Button>
      </div>
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 px-4" onClick={onClose}>
      <div
        className="w-full max-w-md border border-border bg-card p-7"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-display text-lg font-semibold tracking-tight text-foreground">{title}</h2>
        {subtitle && <p className="mt-1 font-mono text-xs uppercase tracking-wide text-muted-foreground">{subtitle}</p>}

        <Eyebrow as="label" className="mb-1.5 mt-5 block">Role</Eyebrow>
        <Select
          dense
          disabled={isEdit}
          value={values.contact_type}
          onChange={(e) => setValues((v) => ({ ...v, contact_type: e.target.value }))}
          className="mb-4"
        >
          {availableRoles.map((t) => (
            <option key={t} value={t}>
              {CONTACT_TYPE_LABELS[t]}
            </option>
          ))}
        </Select>

        <Eyebrow as="label" className="mb-1.5 block">Name</Eyebrow>
        <Input
          dense
          autoFocus={!isEdit}
          value={values.name}
          onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
          className="mb-4"
        />
        <Eyebrow as="label" className="mb-1.5 block">Email</Eyebrow>
        <Input
          dense
          type="email"
          value={values.email}
          onChange={(e) => setValues((v) => ({ ...v, email: e.target.value }))}
          className="mb-4"
        />
        <Eyebrow as="label" className="mb-1.5 block">Phone</Eyebrow>
        <Input
          dense
          value={values.phone}
          onChange={(e) => setValues((v) => ({ ...v, phone: e.target.value }))}
          className="mb-4"
        />
        {error && (
          <p className="mb-4 border border-accent px-3 py-2 text-sm text-accent" role="alert">
            {error}
          </p>
        )}
        <div className="flex gap-3">
          <Button variant="secondary" size="sm" onClick={() => onSubmit(values)} disabled={submitting}>
            {submitting ? "Saving…" : "Save"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
