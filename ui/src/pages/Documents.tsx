import { useEffect, useState, type FormEvent } from "react";
import { listDocuments, searchDocuments, uploadDocument, type DocumentRef, type DocumentSearchResult } from "../api";
import { useSession } from "../context/SessionContext";

const DOC_TYPES = ["vendor_certification", "customer_manual", "quote", "terms_and_conditions", "equipment_manual"];

const selectClass =
  "rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-[13px] text-zinc-600 outline-none transition-shadow focus:border-zinc-400 focus:ring-4 focus:ring-zinc-900/5";

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
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-8">
        <h1 className="font-display text-[26px] font-semibold tracking-tight text-zinc-900">Documents</h1>
        <p className="mt-1 text-[14px] text-zinc-400">Certifications, manuals, quotes, and T&amp;Cs — searchable.</p>
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

        <label className="cursor-pointer rounded-lg border border-zinc-200 bg-white px-3.5 py-1.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-50">
          {uploading ? "Uploading..." : "Upload PDF"}
          <input type="file" accept="application/pdf" onChange={handleUpload} disabled={uploading} className="hidden" />
        </label>
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

      <p className="mb-2 text-[12px] font-medium uppercase tracking-wide text-zinc-400">All documents</p>
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
