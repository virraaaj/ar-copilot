// Project folders (added 2026-07-23, project-folder restructuring): every
// document belongs to exactly one project going forward. Two-level view --
// a folder grid (one card per project, from listProjects + a doc-count
// computed client-side from listDocuments, same "group by project" pattern
// Dashboard already uses for invoices) and, once a folder is opened, the
// original upload/search/list UI scoped to that project via project_number.
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
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

const DOC_TYPES = ["vendor_certification", "customer_manual", "quote", "terms_and_conditions", "equipment_manual"];

const selectClass =
  "rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[13px] text-zinc-600 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5";

const UNFILED = "_unfiled";

export default function Documents() {
  const { token } = useSession();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [allDocs, setAllDocs] = useState<DocumentRef[]>([]);
  const [openFolder, setOpenFolder] = useState<{ project_number: string; project_name: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listProjects(token).then(setProjects).catch((e) => setError(String(e)));
    listDocuments(token).then(setAllDocs).catch((e) => setError(String(e)));
  }, [token]);

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>
      </div>
    );
  }

  if (openFolder) {
    return (
      <ProjectDocuments
        projectNumber={openFolder.project_number}
        projectName={openFolder.project_name}
        onBack={() => {
          setOpenFolder(null);
          if (token) listDocuments(token).then(setAllDocs).catch((e) => setError(String(e)));
        }}
      />
    );
  }

  if (!projects) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-[13px] text-zinc-400">Loading...</p>
      </div>
    );
  }

  const countFor = (projectNumber: string | null) => allDocs.filter((d) => d.project_number === projectNumber).length;
  const unfiledCount = countFor(null);

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">Documents</h1>
        <p className="mt-1 text-[14px] text-zinc-400">Certifications, manuals, quotes, and T&amp;Cs — organized by project.</p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {projects.map((p) => {
          const count = countFor(p.project_number);
          return (
            <button
              key={p.project_number}
              onClick={() => setOpenFolder(p)}
              className="flex items-center gap-3 rounded-2xl border border-zinc-200/70 bg-white p-4 text-left shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-colors hover:bg-zinc-50"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-zinc-900 text-[13px] font-semibold text-white">
                {(p.project_name ?? p.project_number).slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[14px] font-medium text-zinc-900">{p.project_name ?? p.project_number}</p>
                <p className="text-[12px] text-zinc-400">{p.project_number}</p>
              </div>
              <span className="shrink-0 rounded-full bg-zinc-100 px-2.5 py-1 text-[11px] font-medium text-zinc-500">
                {count} doc{count === 1 ? "" : "s"}
              </span>
            </button>
          );
        })}

        {unfiledCount > 0 && (
          <button
            onClick={() => setOpenFolder({ project_number: UNFILED, project_name: "Unfiled" })}
            className="flex items-center gap-3 rounded-2xl border border-dashed border-zinc-300 bg-white p-4 text-left shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-colors hover:bg-zinc-50"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-zinc-300 text-[13px] font-semibold text-white">
              ?
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[14px] font-medium text-zinc-900">Unfiled</p>
              <p className="text-[12px] text-zinc-400">Uploaded before project folders existed</p>
            </div>
            <span className="shrink-0 rounded-full bg-zinc-100 px-2.5 py-1 text-[11px] font-medium text-zinc-500">
              {unfiledCount} doc{unfiledCount === 1 ? "" : "s"}
            </span>
          </button>
        )}

        {projects.length === 0 && unfiledCount === 0 && (
          <p className="text-[13px] text-zinc-400">No projects found.</p>
        )}
      </div>
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
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    if (!token) return;
    listDocuments(token, docType || undefined, projectNumber)
      .then(setDocs)
      .catch((e) => setError(String(e)));
  }

  useEffect(refresh, [token, docType, projectNumber]);

  async function handleUpload(e: FormEvent<HTMLInputElement>) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    if (!file || !token || isUnfiled) return;
    setUploading(true);
    setError(null);
    try {
      await uploadDocument(token, file, projectNumber, docType || undefined);
      refresh();
    } catch (err) {
      setError(String(err));
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
    <div className="mx-auto max-w-3xl px-6 py-10">
      <button
        onClick={onBack}
        className="mb-4 text-[13px] font-medium text-zinc-500 transition-colors hover:text-zinc-900"
      >
        ← All projects
      </button>

      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">
            {projectName ?? projectNumber}
          </h1>
          <p className="mt-1 text-[14px] text-zinc-400">
            {isUnfiled ? "Documents uploaded before project folders existed." : `Documents for project ${projectNumber}.`}
          </p>
        </div>
        {!isUnfiled && (
          <button
            onClick={() => {
              setCurrentProject({ project_number: projectNumber, project_name: projectName });
              navigate("/chat");
            }}
            className="shrink-0 rounded-full bg-zinc-900 px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-zinc-800"
          >
            Chat about this project
          </button>
        )}
      </div>

      {error && <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-600">{error}</p>}

      <div className="mb-4 flex items-center gap-2">
        <select
          value={docType}
          onChange={(e) => {
            setDocType(e.target.value);
            setResults(null);
          }}
          className={selectClass}
        >
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>

        {!isUnfiled && (
          <label className="cursor-pointer rounded-lg border border-zinc-200 bg-white px-3.5 py-1.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50">
            {uploading ? "Uploading..." : "Upload PDF"}
            <input type="file" accept="application/pdf" onChange={handleUpload} disabled={uploading} className="hidden" />
          </label>
        )}
      </div>

      <form onSubmit={handleSearch} className="mb-6 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search document content..."
          className="flex-1 rounded-xl border border-zinc-200 bg-white px-4 py-2.5 text-[14px] outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5"
        />
        <button
          type="submit"
          className="rounded-xl bg-zinc-900 px-5 py-2.5 text-[14px] font-medium text-white transition-colors hover:bg-zinc-800"
        >
          Search
        </button>
      </form>

      {results && (
        <div className="mb-8 space-y-2">
          <p className="text-[12px] font-medium uppercase tracking-wide text-zinc-400">{results.length} result(s)</p>
          {results.map((r, i) => (
            <div
              key={i}
              className="rounded-xl border border-zinc-200/70 bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]"
            >
              <p className="text-[13px] font-medium text-zinc-900">
                {r.filename} <span className="font-normal text-zinc-400">· page {r.page}</span>
              </p>
              <p className="mt-1.5 text-[13px] leading-relaxed text-zinc-500">{r.excerpt}</p>
            </div>
          ))}
        </div>
      )}

      <p className="mb-2 text-[12px] font-medium uppercase tracking-wide text-zinc-400">Documents in this folder</p>
      <div className="overflow-hidden rounded-2xl border border-zinc-200/70 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
        {docs.map((d) => (
          <div
            key={d.filename}
            className="flex items-center justify-between border-b border-zinc-50 px-5 py-3 text-[13px] last:border-0"
          >
            <span className="font-medium text-zinc-800">{d.filename}</span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-[12px] font-medium capitalize text-zinc-500">
              {d.doc_type?.replace(/_/g, " ") ?? "untyped"}
            </span>
          </div>
        ))}
        {docs.length === 0 && (
          <div className="px-5 py-12 text-center text-[13px] text-zinc-400">No documents yet.</div>
        )}
      </div>
    </div>
  );
}
