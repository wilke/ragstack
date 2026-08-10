import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import {
  ApiError,
  createCollection,
  getCollections,
  getIngestJob,
  isTerminalIngestStatus,
  uploadPdfs,
  type CollectionInfo,
  type IngestResponse,
} from "../api/client";
import { getStoredAuthMode } from "../api/config";
import { SIGNED_IN_HINT } from "../lib/auth";
import { describeChunking, type ChunkConfigBody } from "../lib/chunkers";
import { collectionCreateMessage } from "../lib/collections";
import { NewCollectionForm } from "./NewCollectionForm";
import { ShareDialog } from "./ShareDialog";

// "Collection" view: get PDFs into a collection, in two steps.
//   1. Select  - pick the target collection, or create a new one inline.
//   2. Upload  - drag/drop or pick PDFs, POST /v1/ingest/upload (multipart),
//                then poll GET /v1/ingest/{job_id} every ~1.5s until terminal.
// Querying lives in the Explore tab — this view is only about ingest, so it
// deliberately has no search box.
// The target collection is PICKED from a dropdown of existing collections
// (GET /v1/collections) - never free-typed, so a user can't aim an upload at a
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

// No embedding model is sent for a new collection: the server resolves its
// default build spec (ADR-0003 — supplying `embedding` is an admin-only
// override, and the old hardcoded "sfr" ref 404'd on servers without that
// registered model). The Ops admin panel is where an admin picks a specific
// registered embedding model. The CHUNK strategy comes from NewCollectionForm,
// which defaults to "Server default" (no chunk field sent at all).

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
    if (error.status === 401) return "Check your API key.";
    if (error.status === 403)
      return "Only the collection's owner (or an admin) can upload into it.";
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
  // "Share": open the inline ShareDialog for the selected collection. Owner-or-
  // admin is enforced server-side (#243); GET /v1/collections does not expose
  // ownership, so the button shows for any selected collection and a non-owner's
  // actions fail with a 403 the dialog explains (the smaller correct change —
  // no is_owner field was added to the collections list).
  const [sharing, setSharing] = useState(false);
  const create = useMutation<CollectionInfo, Error, { name: string; chunk?: ChunkConfigBody }>({
    mutationFn: ({ name, chunk }) =>
      createCollection(
        // `chunk` is only present when the user actively picked a concrete
        // strategy (admin-only override); omitted → server default build spec.
        { id: name, label: name, ...(chunk ? { chunk } : {}) },
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

  const resetJob = () => {
    setJobId(null);
    setFiles([]);
    upload.reset();
  };

  return (
    <div className="space-y-8">
      {/* ---- Step 1: Select or create the target collection ---- */}
      {/* "Done" once files are staged (or a job launched): adding files is the
          act that commits the selection, since uploads go wherever the picker
          points at that moment. */}
      <section aria-labelledby="select-heading">
        <div className="mb-3">
          {stageBadge(
            1,
            "Select or create a collection",
            files.length === 0 && jobId == null,
            files.length > 0 || jobId != null,
          )}
        </div>

        {/* Bound to the app's single credential slot — hidden while a bearer
            token is active so typing here can't switch auth kinds mid-flow. */}
        {getStoredAuthMode() === "bearer" ? (
          <p className="mb-3 text-xs text-gray-500">{SIGNED_IN_HINT}</p>
        ) : (
          <input
            type="password"
            placeholder="X-API-Key (leave blank if the API is keyless in dev)"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="mb-3 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            aria-label="API key"
            autoComplete="off"
          />
        )}

        {/* Select an existing collection OR create a new one — never both at
            once, so the picker (and its Share control) hides while the create
            form is open rather than sitting beside it as a second answer. */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {!creating && (
            <>
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
            </>
          )}
          <button
            type="button"
            onClick={() => {
              // Entering create mode closes the share dialog: its toggle hides
              // with the picker, so leaving it open would strand it on screen.
              setCreating((v) => !v);
              setSharing(false);
            }}
            disabled={create.isPending}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
          >
            {create.isPending ? "Creating…" : creating ? "Cancel" : "＋ New collection"}
          </button>
          {!creating && (
            <>
              <button
                type="button"
                onClick={() => setSharing((v) => !v)}
                disabled={!selected}
                title={selected ? undefined : "Pick a collection to share"}
                className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
              >
                {sharing ? "Cancel" : "Share"}
              </button>
              <span className="text-xs text-gray-400">upload target</span>
            </>
          )}
        </div>

        {/* How the selected collection was built. `provenance` (when present) is the
            manifest a real ingest wrote; otherwise these are the registry's
            declared values. Rendered as text, never markup. */}
        {selected && !creating ? (
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

        {sharing && selected && (
          <ShareDialog
            key={selected.id}
            collectionId={selected.id}
            collectionLabel={selected.label}
            apiKey={apiKey}
            onClose={() => setSharing(false)}
          />
        )}
      </section>

      {/* ---- Step 2: Upload PDFs into it ---- */}
      <section aria-labelledby="upload-heading">
        <div className="mb-3">
          {stageBadge(2, "Upload PDFs", files.length > 0 && jobId == null, jobId != null)}
        </div>

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
        {/* Ingest progress — feedback on the upload, not a third step. */}
        {jobId != null && (
          <div className="mt-6">
            <h3 className="mb-3 text-sm font-medium text-gray-700">Ingest progress</h3>

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

            {terminal && (
              <p className="mt-3 text-xs text-gray-400">
                Ask the corpus from the Explore tab — this view only ingests.
              </p>
            )}
          </div>
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
