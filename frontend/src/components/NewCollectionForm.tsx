import { useState } from "react";
import {
  DEFAULT_CHUNK_FORM,
  buildChunkConfig,
  validateChunkForm,
  type ChunkConfigBody,
  type ChunkForm,
} from "../lib/chunkers";
import { ChunkStrategyPicker } from "./ChunkStrategyPicker";

// Inline "New collection" panel: name + chunk strategy.
//
// NAMING (docs/ARCHITECTURE.md §3): this creates a **collection** — a registry
// entry binding (embedding model + dim + chunker) to a physical index. "Library"
// is not a separate concept; ADR-0003 makes it one-to-one with a collection, so
// "collection" is the only correct word. This form used to be called
// NewLibraryForm while posting to /v1/collections; do not bring that name back.
//
// Chunking is build-time identity for a collection — it can't be edited later.
// The DEFAULT choice is "Server default": no `chunk` field is sent at all, and
// the server resolves its configured default build spec into the collection
// (ADR-0003 — supplying a concrete `chunk` is an admin-only override, so the
// default path is also the one every authenticated user is allowed to take).
// The form does NOT pretend to know what the server default is (the client-side
// DEFAULT_CHUNK_FORM fixed_token/512/64 is a guess, not the server's fixed/512/64);
// the created collection reports its resolved chunking after the fact.
//
// Picking a concrete strategy shows the shared ChunkStrategyPicker, which the
// Ops admin panel also uses — one definition of what a collection's chunking is.
export function NewCollectionForm({
  onCreate,
  onCancel,
  pending,
  error,
}: {
  // `chunk` is undefined when the user kept "Server default" — the caller must
  // then OMIT the field from the request body entirely.
  onCreate: (name: string, chunk?: ChunkConfigBody) => void;
  onCancel: () => void;
  pending: boolean;
  error: string | null;
}) {
  const [name, setName] = useState("");
  const [useServerDefault, setUseServerDefault] = useState(true);
  const [form, setForm] = useState<ChunkForm>(DEFAULT_CHUNK_FORM);
  const [touched, setTouched] = useState(false);

  const chunkProblem = useServerDefault ? null : validateChunkForm(form);
  const nameProblem = name.trim() === "" ? "Give the collection a name." : null;
  const problem = nameProblem ?? chunkProblem;

  const submit = () => {
    setTouched(true);
    if (problem) return;
    onCreate(name.trim(), useServerDefault ? undefined : buildChunkConfig(form));
  };

  return (
    <div className="mb-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-800">New collection</h3>

      <div className="mb-3">
        <label htmlFor="new-col-name" className="mb-1 block text-xs font-medium text-gray-500">
          Name
        </label>
        <input
          id="new-col-name"
          type="text"
          value={name}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="e.g. andy"
          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <p className="mt-1 text-[11px] leading-snug text-gray-400">
          Also the collection id, so this collection gets a physical store of its own — documents
          uploaded here can&apos;t show up in another collection.
        </p>
      </div>

      <div className="mb-3">
        <label htmlFor="new-col-chunk-mode" className="mb-1 block text-xs font-medium text-gray-500">
          Chunking
        </label>
        <select
          id="new-col-chunk-mode"
          value={useServerDefault ? "server" : "custom"}
          onChange={(e) => setUseServerDefault(e.target.value === "server")}
          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
        >
          <option value="server">Server default (recommended)</option>
          <option value="custom">Choose a strategy (admin only)</option>
        </select>
        {useServerDefault ? (
          <p className="mt-1 text-[11px] leading-snug text-gray-400">
            The server&apos;s configured chunker is used and recorded in the collection&apos;s
            build spec. Anyone can create a collection this way; picking a specific strategy
            is an admin-only override.
          </p>
        ) : null}
      </div>

      {useServerDefault ? null : (
        <ChunkStrategyPicker idPrefix="new-col" form={form} onChange={setForm} />
      )}

      {touched && problem ? (
        <p role="alert" className="mt-3 rounded bg-amber-50 p-2 text-sm text-amber-800">
          {problem}
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="mt-3 rounded bg-red-50 p-2 text-sm text-red-700">
          {error}
        </p>
      ) : null}

      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={pending}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white transition-opacity hover:bg-blue-700 disabled:opacity-50"
        >
          {pending ? "Creating…" : "Create collection"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={pending}
          className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-white disabled:opacity-50"
        >
          Cancel
        </button>
        <span className="text-xs text-gray-400">
          Chunking is fixed when the collection is created — it can&apos;t be changed later.
        </span>
      </div>
    </div>
  );
}
