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
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { ErrorState, describeError } from "../components/ui/ErrorState";

type ModalState = { bu: string | null; contact: Contact | null } | null;

export default function DefaultProjectContacts() {
  const { token } = useSession();
  const [scopes, setScopes] = useState<DefaultContactScope[] | null>(null);
  // Business units only feed the "Add BU override" picker -- supporting
  // data the page works without (that action just stays unavailable), so
  // its own failure degrades quietly rather than blocking the page.
  const [businessUnits, setBusinessUnits] = useState<BusinessUnit[]>([]);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [modal, setModal] = useState<ModalState>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [showAddBu, setShowAddBu] = useState(false);

  function load() {
    if (!token) return;
    setLoading(true);
    setError(null);
    listDefaultProjectContacts(token)
      .then(setScopes)
      .catch((e) => setError(describeError(e, "We couldn't load default project contacts.")))
      .finally(() => setLoading(false));
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
      setFormError(describeError(e, "That didn't save. Please try again.").message);
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

  const overriddenBus = new Set((scopes ?? []).filter((s) => s.bu).map((s) => s.bu));
  const availableBusForOverride = businessUnits.filter((bu) => !overriddenBus.has(bu.bu_id));

  // Header/title (plus the "Add BU override" action, disabled while
  // there's nothing loaded to override) always renders below, whether
  // loading, errored, or loaded -- the page never loses its identity.
  const header = (
    <div className="mb-8 flex items-start justify-between gap-4 border-b border-border pb-6">
      <div>
        <Eyebrow as="p" tone="accent" className="mb-2">Settings</Eyebrow>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">Default Project Contacts</h1>
        <p className="mt-2 max-w-2xl text-sm leading-normal text-muted-foreground">
          Applied to every new project unless a business unit override exists below.
        </p>
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="shrink-0"
        disabled={!scopes || availableBusForOverride.length === 0}
        onClick={() => setShowAddBu(true)}
      >
        Add BU override
      </Button>
    </div>
  );

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        {header}
        <ErrorState message={error.message} detail={error.detail} onRetry={load} retrying={loading} />
      </div>
    );
  }
  if (!scopes) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-10">
        {header}
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      {header}

      <div className="space-y-8">
        {scopes.map((scope) => {
          const usedRoles = scope.contacts.map((c) => c.contact_type);
          const hasUnassignedRole = usedRoles.length < CONTACT_TYPES.length;
          const isGlobal = scope.bu === null;
          return (
            <div key={scope.bu ?? "global"} className="border border-border">
              <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
                <div className="flex min-w-0 items-center gap-3">
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center border font-mono text-xs font-semibold uppercase ${
                      isGlobal ? "border-foreground text-foreground" : "border-accent text-accent"
                    }`}
                    aria-hidden
                  >
                    {isGlobal ? "G" : (scope.bu_name ?? scope.bu ?? "?").slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-base font-medium text-foreground">{isGlobal ? "Global" : scope.bu_name ?? `BU ${scope.bu}`}</p>
                    <p className="text-sm text-muted-foreground">
                      {isGlobal ? "Applied to all new projects unless a BU override exists." : `Overrides global defaults for BU ${scope.bu}`}
                    </p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <Badge tone="neutral">
                    {scope.contacts.length} contact{scope.contacts.length === 1 ? "" : "s"}
                  </Badge>
                  {hasUnassignedRole && (
                    <Button variant="ghost" size="sm" onClick={() => setModal({ bu: scope.bu, contact: null })}>
                      Add contact
                    </Button>
                  )}
                  {!isGlobal && (
                    <button
                      onClick={() => handleRemoveOverride(scope)}
                      className="min-h-11 border border-accent px-4 font-mono text-xs font-semibold uppercase tracking-wider text-accent transition-colors duration-150 ease-bold hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background"
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
                    {scope.contacts.map((c) => (
                      <ContactCard
                        key={c.contact_id}
                        contact={c}
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 px-4" onClick={() => setShowAddBu(false)}>
          <div className="w-full max-w-sm border border-border bg-card p-7" onClick={(e) => e.stopPropagation()}>
            <h2 className="font-display text-lg font-semibold tracking-tight text-foreground">Add BU override</h2>
            <p className="mt-1 text-sm text-muted-foreground">Pick a business unit to override the global defaults for.</p>
            <div className="mt-4 space-y-2">
              {availableBusForOverride.map((bu) => (
                <button
                  key={bu.bu_id}
                  onClick={() => {
                    setShowAddBu(false);
                    setModal({ bu: bu.bu_id, contact: null });
                  }}
                  className="min-h-11 w-full border border-border px-3.5 py-2.5 text-left text-sm text-foreground transition-colors duration-150 ease-bold hover:border-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {bu.bu_name ?? bu.bu_id}
                </button>
              ))}
              {availableBusForOverride.length === 0 && (
                <p className="text-sm text-muted-foreground">Every business unit already has an override.</p>
              )}
            </div>
            <div className="mt-4">
              <Button variant="ghost" size="sm" onClick={() => setShowAddBu(false)}>
                Cancel
              </Button>
            </div>
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
