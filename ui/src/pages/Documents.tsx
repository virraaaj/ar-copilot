import { useEffect, useState, type FormEvent } from "react";
import { listDocuments, searchDocuments, uploadDocument, type DocumentRef, type DocumentSearchResult } from "../api";
import { useSession } from "../context/SessionContext";

const DOC_TYPES = ["vendor_certification", "customer_manual", "quote", "terms_and_conditions", "equipment_manual"];

export default function Documents() {
  const { token } = useSession();
  const [docs, setDocs] = useState<DocumentRef[]>([]);
  const [docType, setDocType] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DocumentSearchResult[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    if (!token) return;
    listDocuments(token, docType || undefined).then(setDocs).catch((e) => setError(String(e)));
  }

  useEffect(refresh, [token, docType]);

  async function handleUpload(e: FormEvent<HTMLInputElement>) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    if (!file || !token) return;
    setUploading(true);
    setError(null);
    try {
      await uploadDocument(token, file, docType || undefined);
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
    setResults(await searchDocuments(token, query, docType || undefined));
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="mb-4 text-xl font-semibold text-slate-800">Documents</h1>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      <div className="mb-4 flex items-center gap-3">
        <select
          value={docType}
          onChange={(e) => {
            setDocType(e.target.value);
            setResults(null);
          }}
          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </select>

        <label className="cursor-pointer rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
          {uploading ? "Uploading..." : "Upload PDF"}
          <input type="file" accept="application/pdf" onChange={handleUpload} disabled={uploading} className="hidden" />
        </label>
      </div>

      <form onSubmit={handleSearch} className="mb-4 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search document content..."
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <button type="submit" className="rounded-md bg-slate-800 px-4 py-2 text-sm text-white hover:bg-slate-700">
          Search
        </button>
      </form>

      {results && (
        <div className="mb-6 space-y-2">
          <p className="text-xs text-slate-500">{results.length} result(s)</p>
          {results.map((r, i) => (
            <div key={i} className="rounded-lg border border-slate-200 bg-white p-3">
              <p className="text-sm font-medium text-slate-800">
                {r.filename} <span className="font-normal text-slate-400">(page {r.page})</span>
              </p>
              <p className="mt-1 text-sm text-slate-600">{r.excerpt}</p>
            </div>
          ))}
        </div>
      )}

      <h2 className="mb-2 text-sm font-medium text-slate-500">All documents</h2>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
        {docs.map((d) => (
          <li key={d.filename} className="flex items-center justify-between px-3 py-2 text-sm">
            <span className="text-slate-800">{d.filename}</span>
            <span className="text-slate-400">{d.doc_type ?? "untyped"}</span>
          </li>
        ))}
        {docs.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate-400">No documents yet.</li>}
      </ul>
    </div>
  );
}
