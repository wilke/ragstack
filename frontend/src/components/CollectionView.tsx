import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import {
  ApiError,
  createCollection,
  getCollections,
  getIngestJob,
  isTerminalIngestStatus,
  queryRag,
  uploadPdfs,
  type CollectionInfo,
  type IngestResponse,
  type QueryResponse,
} from "../api/client";
import { describeChunking, type ChunkConfigBody } from "../lib/chunkers";
import { collectionCreateMessage } from "../lib/collections";
import { NewCollectionForm } from "./NewCollectionForm";
import { ResultsPanel } from "./ResultsPanel";
import { SearchForm } from "./SearchForm";
import { EmptyState } from "./states/EmptyState";

// Demo "Collection" view: the full upload -> ingest -> query loop on one page.
//   1. Upload   - drag/drop or pick PDFs, POST /v1/ingest/upload (multipart).
//   2. Progress - poll GET /v1/ingest/{job_id} every ~1.5s until terminal.
//   3. Query    - ask the corpus, reusing the Explore answer/source components.
// The target collection is PICKED from a dropdown of existing collections
// (GET /v1/collections) - never free-typed, so a user can't aim upload/query at a
// non-existent id. A "+ New collection" control opens an inline form (name + chunk
// strategy) that mints a fresh named collection (POST /v1/collections) and
// selects it.
//
// NAMING (docs/ARCHITECTURE.md §3): index = the physical Qdrant collection + ES
// index; **collection** = the registry entry binding (model + dim + chunker) to an
// index, which is what this view creates and selects. "Library" is not a separate
// concept — ADR-0003 makes it one-to-one with a collection. This view was called
// "Library" while calling POST /v1/collections; every user-facing string here says
// "collection" now, and should keep saying it.
//
// Collection administration proper - choosing the embedding model, inspecting a
// collection's build spec/provenance, deleting a registry binding - lives in the
// Ops view's Collections section, since those are admin-gated operations.

const MAX_MB = 50; // advisory only; the server is authoritative (returns 413).

// Embedding model for a new collection - matches the demo's SFR collection so a
// user-created collection is queryable with the same embedder as the default.
// (The Ops admin panel lets an admin pick any registered embedding model; this
// demo path keeps one fewer decision in the way.) The CHUNK strategy is no longer
// hardcoded here: it is chosen per collection in NewCollectionForm (defaults still
// fixed_token/512/64).
const NEW_COLLECTION_EMBEDDING = "sfr";

// Create errors are worded once, in lib/collections.ts, so this view and the Ops
// admin panel explain the same 409/404/400 the same way.
function createErrorMessage(error: Error): string {
  const status = error instanceof ApiError ? error.status : null;
  return collectionCreateMessage(status, error.message);
}

function stageBadge(n: number, label: string, active: boolean, done: boolean) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
          done
            ? "bg-green-600 text-white"
            : active
              ? "bg-blue-600 text-white"
              : "bg-gray-200 text-gray-500"
        }`}
      >
        {done ? "✓" : n}
      </span>
      <span className={`text-sm font-medium ${active || done ? "text-gray-900" : "text-gray-400"}`}>
        {label}
      </span>
    </div>
  );
}

function uploadErrorMessage(error: Error): string {
  if (error instanceof ApiError) {
    if (error.status === 415) return "Rejected: one or more files are not PDFs (415).";
    if (error.status === 413) return `Rejected: a file is too large or too many files (413).`;
    if (error.status === 503) return "Ingest is disabled on this server (no INGEST_ROOT).";
    if (error.status === 401 || error.status === 403) return "Check your API key.";
    return `Upload failed (error ${error.status}).`;
  }
  return "Upload failed — could not reach the API.";
}

export function CollectionView({
  apiKey,
  setApiKey,
}: {
  apiKey: string;
  setApiKey: (v: string) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [collection, setCollection] = useState(""); // "" → server default (demo)
  const [jobId, setJobId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  // Existing collections populate the picker (any authenticated caller can read
  // this). The picker is the ONLY way to choose a target — no free-text ids.
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts: CollectionInfo[] = collections.data?.collections ?? [];

  // "＋ New collection": open the inline form, create with the chosen name + chunk
  // strategy, then select it once the registry refetch has it as an option.
  // Errors surface via createErrorMessage (which unwraps the server's `detail`).
  const [creating, setCreating] = useState(false);
  const create = useMutation<CollectionInfo, Error, { name: string; chunk: ChunkConfigBody }>({
    mutationFn: ({ name, chunk }) =>
      createCollection(
        { embedding: NEW_COLLECTION_EMBEDDING, chunk, id: name, label: name },
        apiKey || undefined,
      ),
    onSuccess: async (info) => {
      await queryClient.invalidateQueries({ queryKey: ["collections", apiKey] });
      setCollection(info.id); // info.id is server-echoed; option now exists post-refetch
      setCreating(false);
    },
  });

  // The collection currently selected as upload target / query source, so its build
  // config (model + chunker) can be shown next to the picker.
  const selected = opts.find((c) => (c.default ? "" : c.id) === collection) ?? null;
  const selectedChunking = selected ? describeChunking(selected) : "";

  // Query stage state.
  const [query, setQuery] = useState("");

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    // De-dupe by name+size so re-dropping the same file doesn't stack.
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...incoming.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  };

  const removeFile = (idx: number) => setFiles((prev) => prev.filter((_, i) => i !== idx));

  const upload = useMutation<IngestResponse, Error, void>({
    mutationFn: () => uploadPdfs(files, collection || undefined, apiKey || undefined),
    onSuccess: (res) => setJobId(res.job_id),
  });

  // Poll the job until a terminal status, then stop refetching.
  const job = useQuery<IngestResponse, Error>({
    queryKey: ["ingest-job", jobId, apiKey],
    queryFn: () => getIngestJob(jobId as string, apiKey || undefined),
    enabled: jobId != null,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s && isTerminalIngestStatus(s) ? false : 1500;
    },
    retry: false,
  });

  const jobStatus = job.data?.status;
  const terminal = jobStatus != null && isTerminalIngestStatus(jobStatus);
  const items = job.data?.items ?? null;

  const run = useMutation<QueryResponse, Error, string>({
    mutationFn: (q) =>
      queryRag({ query: q, top_k: 5, collection: collection || undefined }, apiKey || undefined),
  });
  const submitQuery = () => {
    const q = query.trim();
    if (q) run.mutate(q);
  };
  const queryStatus = run.isPending ? "pending" : run.isError ? "error" : "success";

  const resetJob = () => {
    setJobId(null);
    setFiles([]);
    upload.reset();
  };

  return (
    <div className="space-y-8">
      {/* ---- Stage 1: Upload ---- */}
      <section aria-labelledby="upload-heading">
        <div className="mb-3">
          {stageBadge(1, "Upload PDFs", jobId == null, jobId != null)}
        </div>

        <input
          type="password"
          placeholder="X-API-Key (leave blank if the API is keyless in dev)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          aria-label="API key"
          autoComplete="off"
        />

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label htmlFor="target-collection" className="text-xs font-medium text-gray-500">
            Collection
          </label>
          <select
            id="target-collection"
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            disabled={opts.length === 0}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm disabled:bg-gray-100 disabled:text-gray-400"
          >
            {opts.length === 0 ? (
              <option value="">
                {collections.isLoading ? "Loading…" : "No collections available"}
              </option>
            ) : (
              opts.map((c) => {
                // Show how each collection was built right in the picker: chunking
                // is build-time identity, so "which chunker is this?" is part of
                // choosing a target, not a detail buried in the ops dashboard.
                const built = describeChunking(c);
                return (
                  <option key={c.id} value={c.default ? "" : c.id}>
                    {c.label}
                    {c.count != null ? ` (${c.count.toLocaleString()})` : ""}
                    {built ? ` · ${built}` : ""}
                  </option>
                );
              })
            )}
          </select>
          <button
            type="button"
            onClick={() => setCreating((v) => !v)}
            disabled={create.isPending}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
          >
            {create.isPending ? "Creating…" : creating ? "Cancel" : "＋ New collection"}
          </button>
          <span className="text-xs text-gray-400">upload target &amp; query source</span>
        </div>

        {/* How the selected collection was built. `provenance` (when present) is the
            manifest a real ingest wrote; otherwise these are the registry's
            declared values. Rendered as text, never markup. */}
        {selected ? (
          <p className="mb-3 text-xs text-gray-400">
            <span className="font-mono text-gray-500">{selected.model}</span>
            <span> · {selected.dim}d</span>
            {selectedChunking ? <span> · {selectedChunking}</span> : null}
            {selected.provenance ? (
              <span
                className={
                  selected.provenance.source === "ingest" ? "text-green-600" : "text-gray-400"
                }
              >
                {" "}
                · {selected.provenance.source === "ingest" ? "verified" : "declared"}
              </span>
            ) : null}
          </p>
        ) : null}

        {creating && (
          <NewCollectionForm
            pending={create.isPending}
            error={create.isError && create.error ? createErrorMessage(create.error) : null}
            onCancel={() => {
              create.reset();
              setCreating(false);
            }}
            onCreate={(name, chunk) => create.mutate({ name, chunk })}
          />
        )}

        {!creating && create.isError && create.error && (
          <div role="alert" className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700">
            {createErrorMessage(create.error)}
          </div>
        )}

        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragActive(false);
            addFiles(e.dataTransfer.files);
          }}
          onClick={() => fileInput.current?.click()}
          className={`cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
            dragActive ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-gray-400"
          }`}
        >
          <p className="text-sm text-gray-600">
            Drag &amp; drop PDF files here, or <span className="font-medium text-blue-600">browse</span>
          </p>
          <p className="mt-1 text-xs text-gray-400">PDF only · up to ~{MAX_MB} MB each</p>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            className="hidden"
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = ""; // allow re-selecting the same file
            }}
          />
        </div>

        {files.length > 0 && (
          <ul className="mt-3 space-y-1">
            {files.map((f, i) => {
              const tooBig = f.size > MAX_MB * 1024 * 1024;
              return (
                <li
                  key={`${f.name}:${f.size}`}
                  className="flex items-center justify-between rounded border border-gray-200 px-3 py-1.5 text-sm"
                >
                  <span className="truncate">
                    {f.name}{" "}
                    <span className={tooBig ? "text-red-600" : "text-gray-400"}>
                      ({(f.size / 1024 / 1024).toFixed(1)} MB{tooBig ? " — likely too large" : ""})
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => removeFile(i)}
                    className="ml-3 shrink-0 text-xs text-gray-400 hover:text-red-600"
                    aria-label={`Remove ${f.name}`}
                  >
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            disabled={files.length === 0 || upload.isPending || jobId != null}
            onClick={() => upload.mutate()}
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
          >
            {upload.isPending ? "Uploading…" : `Ingest ${files.length || ""} file${files.length === 1 ? "" : "s"}`}
          </button>
          {jobId != null && (
            <button
              type="button"
              onClick={resetJob}
              className="rounded border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
            >
              Upload more
            </button>
          )}
        </div>

        {upload.isError && upload.error && (
          <div role="alert" className="mt-3 rounded bg-red-50 p-3 text-sm text-red-700">
            {uploadErrorMessage(upload.error)}
          </div>
        )}
      </section>

      {/* ---- Stage 2: Ingest progress ---- */}
      {jobId != null && (
        <section aria-labelledby="progress-heading">
          <div className="mb-3">{stageBadge(2, "Ingest progress", !terminal, terminal)}</div>

          <div className="rounded-lg border border-gray-200 p-4">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs text-gray-400">
                Job <span className="font-mono text-gray-600">{jobId}</span>
              </span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                  jobStatus === "completed"
                    ? "bg-green-100 text-green-700"
                    : jobStatus === "failed"
                      ? "bg-red-100 text-red-700"
                      : "bg-blue-100 text-blue-700"
                }`}
              >
                {job.isError ? "error polling" : (jobStatus ?? "starting…")}
              </span>
            </div>

            {items && items.total != null ? (
              <>
                <ProgressBar
                  total={items.total}
                  completed={items.completed ?? 0}
                  failed={items.failed ?? 0}
                />
                <div className="mt-2 grid grid-cols-4 gap-2 text-center text-sm">
                  <Counter label="Total" value={items.total} />
                  <Counter label="Completed" value={items.completed ?? 0} tone="green" />
                  <Counter label="Failed" value={items.failed ?? 0} tone={(items.failed ?? 0) > 0 ? "red" : undefined} />
                  <Counter label="Pending" value={items.pending ?? 0} />
                </div>
              </>
            ) : (
              <p className="text-sm text-gray-500">Enumerating documents…</p>
            )}

            {terminal && (
              <p className="mt-3 text-sm font-medium text-gray-700">
                {jobStatus === "completed" && items
                  ? `Done — ${items.completed ?? 0} of ${items.total ?? 0} indexed${
                      (items.failed ?? 0) > 0 ? `, ${items.failed} failed` : ""
                    }.`
                  : jobStatus === "completed"
                    ? "Done."
                    : jobStatus === "failed"
                      ? "Ingest failed. See the server logs for detail."
                      : "Job not found (unknown id)."}
              </p>
            )}
            {job.isError && (
              <p className="mt-2 text-sm text-red-600">Lost contact with the job while polling.</p>
            )}
          </div>
        </section>
      )}

      {/* ---- Stage 3: Query ---- */}
      <section aria-labelledby="query-heading">
        <div className="mb-3">
          {stageBadge(3, "Ask the corpus", jobId == null || terminal, false)}
        </div>
        <p className="mb-3 text-xs text-gray-400">
          Query the {collection ? <span className="font-mono">{collection}</span> : "demo"} collection.
          {jobId != null && !terminal ? " (You can ask now against the pre-loaded corpus.)" : ""}
        </p>

        <SearchForm
          apiKey={apiKey}
          setApiKey={setApiKey}
          query={query}
          setQuery={setQuery}
          onSubmit={submitQuery}
          pending={run.isPending}
        />

        {run.isIdle ? (
          <EmptyState />
        ) : (
          <ResultsPanel
            status={queryStatus}
            query={run.variables ?? query}
            data={run.data}
            error={run.error}
            onRetry={() => run.variables && run.mutate(run.variables)}
          />
        )}
      </section>
    </div>
  );
}

function ProgressBar({
  total,
  completed,
  failed,
}: {
  total: number;
  completed: number;
  failed: number;
}) {
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0);
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-gray-100">
      <div className="bg-green-500" style={{ width: `${pct(completed)}%` }} />
      <div className="bg-red-500" style={{ width: `${pct(failed)}%` }} />
    </div>
  );
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "green" | "red";
}) {
  const color = tone === "green" ? "text-green-600" : tone === "red" ? "text-red-600" : "text-gray-900";
  return (
    <div className="rounded bg-gray-50 py-1.5">
      <div className={`text-lg font-semibold ${color}`}>{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}
