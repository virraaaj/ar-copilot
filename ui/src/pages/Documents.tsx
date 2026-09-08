// Project folders (added 2026-07-23, project-folder restructuring): every
// document belongs to exactly one project going forward. Two-level view --
// a folder grid (one card per project, from listProjects + a doc-count
// computed client-side from listDocuments, same "group by project" pattern
// Dashboard already uses for invoices) and, once a folder is opened, the
// original upload/search/list UI scoped to that project via project_number.
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Upload } from "lucide-react";
import {
  listDocuments,
  searchDocuments,
  uploadDocument,
  listProjects,
  type DocumentRef,
  type DocumentSearchResult,
  type Project,
} from "../api";
import { useSession } from "../context/SessionContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Eyebrow } from "../components/ui/Eyebrow";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Input, Select } from "../components/ui/Input";
import { Divider } from "../components/ui/Divider";
import { ErrorState, describeError } from "../components/ui/ErrorState";

const DOC_TYPES = ["vendor_certification", "customer_manual", "quote", "terms_and_conditions", "equipment_manual"];

const UNFILED = "_unfiled";

export default function Documents() {
  const { token } = useSession();
  const [projects, setProjects] = useState<Project[] | null>(null);
  // allDocs only feeds the per-folder count badges -- supporting data the
  // grid works fine without, so a failure here degrades quietly to "no
  // counts yet" instead of blocking the page (see loadDocs' catch below).
  const [allDocs, setAllDocs] = useState<DocumentRef[]>([]);
  const [openFolder, setOpenFolder] = useState<{ project_number: string; project_name: string | null } | null>(null);
  // The project list IS this page's reason for existing (it's the folder
  // grid), so unlike allDocs, a failure here blocks the content region.
  const [projectsError, setProjectsError] = useState<{ message: string; detail?: string } | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(false);

  function loadProjects() {
    if (!token) return;
    setProjectsLoading(true);
    setProjectsError(null);
    listProjects(token)
      .then(setProjects)
      .catch((e) => setProjectsError(describeError(e, "We couldn't load your projects.")))
      .finally(() => setProjectsLoading(false));
  }

  function loadDocs() {
    if (!token) return;
    listDocuments(token).then(setAllDocs).catch(() => setAllDocs([]));
  }

  useEffect(() => {
    loadProjects();
    loadDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (openFolder) {
    return (
      <ProjectDocuments
        projectNumber={openFolder.project_number}
        projectName={openFolder.project_name}
        onBack={() => {
          setOpenFolder(null);
          loadDocs();
        }}
      />
    );
  }

  if (!projects && !projectsError) {
    return (
      <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
        <PageHeader
          eyebrow="Library"
          title="Documents"
          description="Certifications, manuals, quotes, and T&Cs — organized by project."
        />
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  const countFor = (projectNumber: string | null) => allDocs.filter((d) => d.project_number === projectNumber).length;
  const unfiledCount = countFor(null);

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
      <PageHeader
        eyebrow="Library"
        title="Documents"
        description="Certifications, manuals, quotes, and T&Cs — organized by project."
      />

      {projectsError && (
        <ErrorState
          eyebrow="Couldn't load documents"
          message={projectsError.message}
          detail={projectsError.detail}
          onRetry={loadProjects}
          retrying={projectsLoading}
        />
      )}

      {projects && (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {projects.map((p) => {
          const count = countFor(p.project_number);
          return (
            <button
              key={p.project_number}
              onClick={() => setOpenFolder(p)}
              className="flex items-center gap-3 border border-border p-4 text-left transition-colors duration-150 ease-bold hover:border-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center border border-border font-mono text-xs font-semibold text-foreground">
                {(p.project_name ?? p.project_number).slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{p.project_name ?? p.project_number}</p>
                <p className="font-mono text-xs text-muted-foreground">{p.project_number}</p>
              </div>
              <Badge tone="neutral" className="shrink-0">
                {count} doc{count === 1 ? "" : "s"}
              </Badge>
            </button>
          );
        })}

        {unfiledCount > 0 && (
          <button
            onClick={() => setOpenFolder({ project_number: UNFILED, project_name: "Unfiled" })}
            className="flex items-center gap-3 border border-dashed border-border p-4 text-left transition-colors duration-150 ease-bold hover:border-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center border border-border font-mono text-xs font-semibold text-muted-foreground">
              ?
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">Unfiled</p>
              <p className="font-mono text-xs text-muted-foreground">Uploaded before project folders existed</p>
            </div>
            <Badge tone="neutral" className="shrink-0">
              {unfiledCount} doc{unfiledCount === 1 ? "" : "s"}
            </Badge>
          </button>
        )}

        {projects.length === 0 && unfiledCount === 0 && (
          <p className="text-sm text-muted-foreground">No projects found.</p>
        )}
      </div>
      )}
    </div>
  );
}

function ProjectDocuments({
  projectNumber,
  projectName,
  onBack,
}: {
  projectNumber: string;
  projectName: string | null;
  onBack: () => void;
}) {
  const { token, setCurrentProject } = useSession();
  const navigate = useNavigate();
  const isUnfiled = projectNumber === UNFILED;
  const [docs, setDocs] = useState<DocumentRef[]>([]);
  const [docType, setDocType] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DocumentSearchResult[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [docsLoading, setDocsLoading] = useState(false);
  // The document list is this folder view's reason for existing, so a
  // failure here blocks that region of content (below); an upload failure
  // is a one-off action error and stays a small inline notice instead.
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const [uploadError, setUploadError] = useState<{ message: string; detail?: string } | null>(null);

  function refresh() {
    if (!token) return;
    setDocsLoading(true);
    setError(null);
    listDocuments(token, docType || undefined, projectNumber)
      .then(setDocs)
      .catch((e) => setError(describeError(e, "We couldn't load the documents in this folder.")))
      .finally(() => setDocsLoading(false));
  }

  useEffect(refresh, [token, docType, projectNumber]);

  async function handleUpload(e: FormEvent<HTMLInputElement>) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    if (!file || !token || isUnfiled) return;
    setUploading(true);
    setUploadError(null);
    try {
      await uploadDocument(token, file, projectNumber, docType || undefined);
      refresh();
    } catch (err) {
      setUploadError(describeError(err, "That upload didn't go through."));
    } finally {
      setUploading(false);
      input.value = "";
    }
  }

  async function handleSearch(e: FormEvent) {
    e.preventDefault();
    if (!token || !query.trim()) return;
    setResults(await searchDocuments(token, query, docType || undefined, projectNumber));
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 sm:px-12">
      <button
        onClick={onBack}
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors duration-150 ease-bold hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <ArrowLeft size={14} strokeWidth={1.5} aria-hidden />
        All projects
      </button>

      <div className="mb-8 flex flex-col items-start justify-between gap-4 border-b border-border pb-8 sm:flex-row sm:items-end">
        <div>
          <Eyebrow as="p" className="mb-3">{isUnfiled ? "Unfiled" : "Project"}</Eyebrow>
          <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            {projectName ?? projectNumber}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {isUnfiled ? "Documents uploaded before project folders existed." : `Documents for project ${projectNumber}.`}
          </p>
        </div>
        {!isUnfiled && (
          <Button
            variant="secondary"
            size="sm"
            className="shrink-0"
            onClick={() => {
              setCurrentProject({ project_number: projectNumber, project_name: projectName });
              navigate("/chat");
            }}
          >
            Chat about this project
          </Button>
        )}
      </div>

      {uploadError && (
        <p className="mb-4 border border-border px-4 py-3 text-sm text-foreground" role="alert" title={uploadError.detail}>
          {uploadError.message}
        </p>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select
          dense
          value={docType}
          onChange={(e) => {
            setDocType(e.target.value);
            setResults(null);
          }}
          className="w-auto"
        >
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </Select>

        {!isUnfiled && (
          <label>
            <span className="inline-flex h-11 cursor-pointer items-center gap-2 border border-foreground px-4 font-sans text-xs font-semibold uppercase tracking-wider text-foreground transition-all duration-150 ease-bold hover:bg-foreground hover:text-background">
              <Upload size={14} strokeWidth={1.5} aria-hidden />
              {uploading ? "Uploading…" : "Upload PDF"}
            </span>
            <input type="file" accept="application/pdf" onChange={handleUpload} disabled={uploading} className="hidden" />
          </label>
        )}
      </div>

      <form onSubmit={handleSearch} className="mb-8 flex gap-3">
        <Input
          dense
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search document content…"
          className="flex-1"
        />
        <Button type="submit" variant="secondary" size="sm">
          Search
        </Button>
      </form>

      {results && (
        <div className="mb-10 space-y-3">
          <Eyebrow as="p">{results.length} result{results.length === 1 ? "" : "s"}</Eyebrow>
          {results.map((r, i) => (
            <div key={i} className="border border-border p-4">
              <p className="text-sm font-medium text-foreground">
                <span className="font-mono">{r.filename}</span>{" "}
                <span className="font-mono font-normal text-muted-foreground">&middot; page {r.page}</span>
              </p>
              <p className="mt-2 text-sm leading-normal text-muted-foreground">{r.excerpt}</p>
            </div>
          ))}
        </div>
      )}

      <Eyebrow as="p" className="mb-3">Documents in this folder</Eyebrow>
      {error ? (
        <ErrorState message={error.message} detail={error.detail} onRetry={refresh} retrying={docsLoading} />
      ) : (
        <div className="border border-border">
          {docs.map((d, i) => (
            <div key={d.filename}>
              {i > 0 && <Divider />}
              <div className="flex items-center justify-between gap-4 px-5 py-3.5">
                <span className="truncate font-mono text-sm text-foreground">{d.filename}</span>
                <Badge tone="neutral" className="shrink-0">
                  {d.doc_type?.replace(/_/g, " ") ?? "untyped"}
                </Badge>
              </div>
            </div>
          ))}
          {docs.length === 0 && (
            <div className="px-5 py-16 text-center">
              <p className="font-display text-2xl font-semibold tracking-tight text-foreground">No documents yet.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
