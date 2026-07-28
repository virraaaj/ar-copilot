// Same design/concept as Lummus's own Default Project Contacts page,
// deliberately mirrored per request (2026-07-16). This is a *template*,
// not a live link: Lummus applies these to seed a new project's contacts
// at creation time (one Global scope, falling back per role, plus
// optional per-business-unit override scopes) -- editing a default here
// never retroactively changes any existing project's contacts.
import { useEffect, useState } from "react";
import {
  listDefaultProjectContacts,
  addDefaultProjectContact,
  updateDefaultProjectContact,
  deleteDefaultProjectContact,
  listBusinessUnits,
  CONTACT_TYPES,
  type DefaultContactScope,
  type BusinessUnit,
  type Contact,
} from "../api";
import { useSession } from "../context/SessionContext";
import { ContactCard, AddContactTile, EmptyContactsState, ContactFormModal, type ContactFormValues } from "../components/ContactCard";

type ModalState = { bu: string | null; contact: Contact | null } | null;

export default function DefaultProjectContacts() {
  const { token } = useSession();
  const [scopes, setScopes] = useState<DefaultContactScope[] | null>(null);
  const [businessUnits, setBusinessUnits] = useState<BusinessUnit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [showAddBu, setShowAddBu] = useState(false);

  function load() {
    if (!token) return;
    listDefaultProjectContacts(token).then(setScopes).catch((e) => setError(String(e)));
    listBusinessUnits(token).then(setBusinessUnits).catch(() => setBusinessUnits([]));
  }

  useEffect(load, [token]);

  async function handleSubmit(values: ContactFormValues) {
    if (!token || !modal) return;
    setSubmitting(true);
    setFormError(null);
    try {
      if (modal.contact) {
        await updateDefaultProjectContact(token, modal.contact.contact_id, {
          name: values.name,
          email: values.email,
          phone: values.phone,
        });
      } else {
        await addDefaultProjectContact(token, {
          contact_type: values.contact_type,
          bu: modal.bu,
          name: values.name,
          email: values.email,
          phone: values.phone,
        });
      }
      setModal(null);
      setShowAddBu(false);
      load();
    } catch (e) {
      setFormError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(contactId: string) {
    if (!token) return;
    if (!window.confirm("Remove this default contact?")) return;
    await deleteDefaultProjectContact(token, contactId);
    load();
  }

  async function handleRemoveOverride(scope: DefaultContactScope) {
    if (!token) return;
    if (!window.confirm(`Remove the ${scope.bu_name ?? scope.bu} override? This deletes all ${scope.contacts.length} contact(s) in it.`)) return;
    for (const c of scope.contacts) {
      await deleteDefaultProjectContact(token, c.contact_id);
    }
    load();
  }

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 dark:bg-rose-950/40 px-3 py-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>
      </div>
    );
  }
  if (!scopes) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Loading...</p>
      </div>
    );
  }

  const overriddenBus = new Set(scopes.filter((s) => s.bu).map((s) => s.bu));
  const availableBusForOverride = businessUnits.filter((bu) => !overriddenBus.has(bu.bu_id));

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Default Project Contacts</h1>
          <p className="mt-1 text-[14px] text-zinc-400 dark:text-zinc-500">
            Applied to every new project unless a business unit override exists below.
          </p>
        </div>
        <button
          onClick={() => setShowAddBu(true)}
          disabled={availableBusForOverride.length === 0}
          className="shrink-0 rounded-full border border-zinc-200 dark:border-zinc-700 px-4 py-2 text-[13px] font-medium text-zinc-700 dark:text-zinc-300 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Add BU override
        </button>
      </div>

      <div className="space-y-6">
        {scopes.map((scope) => {
          const usedRoles = scope.contacts.map((c) => c.contact_type);
          const hasUnassignedRole = usedRoles.length < CONTACT_TYPES.length;
          const isGlobal = scope.bu === null;
          return (
            <div
              key={scope.bu ?? "global"}
              className="overflow-hidden rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
            >
              <div className="flex items-center justify-between gap-3 border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50/60 dark:bg-zinc-800/40 px-5 py-3.5">
                <div className="flex min-w-0 items-center gap-3">
                  <span
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-white ${
                      isGlobal ? "bg-zinc-900 dark:bg-zinc-700" : "bg-indigo-500"
                    }`}
                  >
                    {isGlobal ? "G" : (scope.bu_name ?? scope.bu ?? "?").slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-[14px] font-medium text-zinc-900 dark:text-zinc-100">{isGlobal ? "Global" : scope.bu_name ?? `BU ${scope.bu}`}</p>
                    <p className="text-[12px] text-zinc-400 dark:text-zinc-500">
                      {isGlobal ? "Applied to all new projects unless a BU override exists." : `Overrides global defaults for BU ${scope.bu}`}
                    </p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2.5 py-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                    {scope.contacts.length} contact{scope.contacts.length === 1 ? "" : "s"}
                  </span>
                  {hasUnassignedRole && (
                    <button
                      onClick={() => setModal({ bu: scope.bu, contact: null })}
                      className="rounded-full border border-zinc-200 dark:border-zinc-700 px-3 py-1.5 text-[12px] font-medium text-zinc-700 dark:text-zinc-300 transition-colors hover:bg-white dark:hover:bg-zinc-800"
                    >
                      Add contact
                    </button>
                  )}
                  {!isGlobal && (
                    <button
                      onClick={() => handleRemoveOverride(scope)}
                      className="rounded-full border border-rose-200 dark:border-rose-800 px-3 py-1.5 text-[12px] font-medium text-rose-600 dark:text-rose-400 transition-colors hover:bg-rose-50 dark:hover:bg-rose-950/40"
                    >
                      Remove override
                    </button>
                  )}
                </div>
              </div>

              <div className="p-5">
                {scope.contacts.length === 0 ? (
                  <EmptyContactsState onAddFirst={() => setModal({ bu: scope.bu, contact: null })} />
                ) : (
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {scope.contacts.map((c, i) => (
                      <ContactCard
                        key={c.contact_id}
                        contact={c}
                        accentIndex={i}
                        onEdit={() => setModal({ bu: scope.bu, contact: c })}
                        onDelete={() => handleDelete(c.contact_id)}
                      />
                    ))}
                    {hasUnassignedRole && <AddContactTile onClick={() => setModal({ bu: scope.bu, contact: null })} />}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {showAddBu && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/30 dark:bg-black/60 px-4" onClick={() => setShowAddBu(false)}>
          <div
            className="w-full max-w-sm rounded-2xl border border-zinc-200/70 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-7 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.15)]"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="font-display text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Add BU override</h2>
            <p className="mt-1 text-[12px] text-zinc-400 dark:text-zinc-500">Pick a business unit to override the global defaults for.</p>
            <div className="mt-4 space-y-1.5">
              {availableBusForOverride.map((bu) => (
                <button
                  key={bu.bu_id}
                  onClick={() => {
                    setShowAddBu(false);
                    setModal({ bu: bu.bu_id, contact: null });
                  }}
                  className="w-full rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-2.5 text-left text-[14px] text-zinc-800 dark:text-zinc-200 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                >
                  {bu.bu_name ?? bu.bu_id}
                </button>
              ))}
              {availableBusForOverride.length === 0 && (
                <p className="text-[13px] text-zinc-400 dark:text-zinc-500">Every business unit already has an override.</p>
              )}
            </div>
            <button
              onClick={() => setShowAddBu(false)}
              className="mt-4 rounded-full px-4 py-2 text-[13px] font-medium text-zinc-500 dark:text-zinc-400 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {modal && (
        <ContactFormModal
          title={modal.contact ? "Edit default contact" : "Add default contact"}
          subtitle={modal.bu ? `BU ${modal.bu} override` : "Global default"}
          initial={{
            contact_type:
              modal.contact?.contact_type ??
              CONTACT_TYPES.find((t) => !scopes.find((s) => s.bu === modal.bu)?.contacts.some((c) => c.contact_type === t)) ??
              CONTACT_TYPES[0],
            name: modal.contact?.name ?? "",
            email: modal.contact?.email ?? "",
            phone: modal.contact?.phone ?? "",
          }}
          usedRoles={scopes.find((s) => s.bu === modal.bu)?.contacts.map((c) => c.contact_type) ?? []}
          isEdit={!!modal.contact}
          submitting={submitting}
          error={formError}
          onClose={() => setModal(null)}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
