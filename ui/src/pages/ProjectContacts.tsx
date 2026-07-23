// Same design/concept as Lummus's own Project Contacts page, deliberately
// mirrored per request (2026-07-16): one section per project, a card grid
// of that project's contacts (one per role, colored by role), an "Add
// contact" tile for any unassigned role, and edit/delete on each card.
import { useEffect, useState } from "react";
import {
  listProjectContacts,
  addProjectContact,
  updateProjectContact,
  deleteProjectContact,
  CONTACT_TYPES,
  type ProjectContactGroup,
  type Contact,
} from "../api";
import { useSession } from "../context/SessionContext";
import { ContactCard, AddContactTile, EmptyContactsState, ContactFormModal, type ContactFormValues } from "../components/ContactCard";

type ModalState = { projectNumber: string; contact: Contact | null } | null;

export default function ProjectContacts() {
  const { token } = useSession();
  const [groups, setGroups] = useState<ProjectContactGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function load() {
    if (!token) return;
    listProjectContacts(token).then(setGroups).catch((e) => setError(String(e)));
  }

  useEffect(load, [token]);

  async function handleSubmit(values: ContactFormValues) {
    if (!token || !modal) return;
    setSubmitting(true);
    setFormError(null);
    try {
      if (modal.contact) {
        await updateProjectContact(token, modal.contact.contact_id, {
          name: values.name,
          email: values.email,
          phone: values.phone,
        });
      } else {
        await addProjectContact(token, {
          project_number: modal.projectNumber,
          contact_type: values.contact_type,
          name: values.name,
          email: values.email,
          phone: values.phone,
        });
      }
      setModal(null);
      load();
    } catch (e) {
      setFormError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(contactId: string) {
    if (!token) return;
    if (!window.confirm("Remove this contact?")) return;
    await deleteProjectContact(token, contactId);
    load();
  }

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
      </div>
    );
  }
  if (!groups) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-[13px] text-zinc-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">Project Contacts</h1>
        <p className="mt-1 text-[14px] text-zinc-400">
          Who to reach for each project -- one contact per role. New projects start from the{" "}
          <a href="/settings" className="font-medium text-zinc-600 underline underline-offset-2">
            default contacts
          </a>{" "}
          template (Settings tab).
        </p>
      </div>

      <div className="space-y-6">
        {groups.map((group) => {
          const usedRoles = group.contacts.map((c) => c.contact_type);
          const hasUnassignedRole = usedRoles.length < CONTACT_TYPES.length;
          return (
            <div key={group.project_number} className="overflow-hidden rounded-2xl border border-zinc-200/70 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
              <div className="flex items-center justify-between gap-3 border-b border-zinc-100 bg-zinc-50/60 px-5 py-3.5">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-[12px] font-semibold text-white">
                    {(group.project_name ?? group.project_number).slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-[14px] font-medium text-zinc-900">{group.project_name ?? group.project_number}</p>
                    <p className="text-[12px] text-zinc-400">{group.project_number}</p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-[11px] font-medium text-zinc-500">
                    {group.contacts.length} contact{group.contacts.length === 1 ? "" : "s"}
                  </span>
                  {hasUnassignedRole && (
                    <button
                      onClick={() => setModal({ projectNumber: group.project_number, contact: null })}
                      className="rounded-full border border-zinc-200 px-3 py-1.5 text-[12px] font-medium text-zinc-700 transition-colors hover:bg-white"
                    >
                      Add contact
                    </button>
                  )}
                </div>
              </div>

              <div className="p-5">
                {group.contacts.length === 0 ? (
                  <EmptyContactsState onAddFirst={() => setModal({ projectNumber: group.project_number, contact: null })} />
                ) : (
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {group.contacts.map((c, i) => (
                      <ContactCard
                        key={c.contact_id}
                        contact={c}
                        accentIndex={i}
                        onEdit={() => setModal({ projectNumber: group.project_number, contact: c })}
                        onDelete={() => handleDelete(c.contact_id)}
                      />
                    ))}
                    {hasUnassignedRole && (
                      <AddContactTile onClick={() => setModal({ projectNumber: group.project_number, contact: null })} />
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {groups.length === 0 && <p className="text-[13px] text-zinc-400">No projects found.</p>}
      </div>

      {modal && (
        <ContactFormModal
          title={modal.contact ? "Edit contact" : "Add contact"}
          subtitle={groups.find((g) => g.project_number === modal.projectNumber)?.project_name ?? modal.projectNumber}
          initial={{
            contact_type: modal.contact?.contact_type ?? CONTACT_TYPES.find((t) => !groups.find((g) => g.project_number === modal.projectNumber)?.contacts.some((c) => c.contact_type === t)) ?? CONTACT_TYPES[0],
            name: modal.contact?.name ?? "",
            email: modal.contact?.email ?? "",
            phone: modal.contact?.phone ?? "",
          }}
          usedRoles={groups.find((g) => g.project_number === modal.projectNumber)?.contacts.map((c) => c.contact_type) ?? []}
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
