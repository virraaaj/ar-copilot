// Same design/concept as Lummus's own Project Contacts page, deliberately
// mirrored per request (2026-07-16): one section per project, a card grid
// of that project's contacts (one per role), an "Add contact" tile for any
// unassigned role, and edit/delete on each card.
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
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";

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
        <p className="border border-accent px-4 py-3 text-sm text-accent" role="alert">{error}</p>
      </div>
    );
  }
  if (!groups) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="mb-8 border-b border-border pb-6">
        <Eyebrow as="p" tone="accent" className="mb-2">Settings</Eyebrow>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">Project Contacts</h1>
        <p className="mt-2 max-w-2xl text-sm leading-normal text-muted-foreground">
          Who to reach for each project -- one contact per role. New projects start from the{" "}
          <a href="/settings" className="text-foreground underline underline-offset-2 hover:text-accent">
            default contacts
          </a>{" "}
          template (Settings tab).
        </p>
      </div>

      <div className="space-y-8">
        {groups.map((group) => {
          const usedRoles = group.contacts.map((c) => c.contact_type);
          const hasUnassignedRole = usedRoles.length < CONTACT_TYPES.length;
          return (
            <div key={group.project_number} className="border border-border">
              <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
                <div className="min-w-0">
                  <p className="truncate text-base font-medium text-foreground">{group.project_name ?? group.project_number}</p>
                  <p className="font-mono text-xs uppercase tracking-wide text-muted-foreground">{group.project_number}</p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <Badge tone="neutral">
                    {group.contacts.length} contact{group.contacts.length === 1 ? "" : "s"}
                  </Badge>
                  {hasUnassignedRole && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setModal({ projectNumber: group.project_number, contact: null })}
                    >
                      Add contact
                    </Button>
                  )}
                </div>
              </div>

              <div className="p-5">
                {group.contacts.length === 0 ? (
                  <EmptyContactsState onAddFirst={() => setModal({ projectNumber: group.project_number, contact: null })} />
                ) : (
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {group.contacts.map((c) => (
                      <ContactCard
                        key={c.contact_id}
                        contact={c}
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
        {groups.length === 0 && <p className="text-sm text-muted-foreground">No projects found.</p>}
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
