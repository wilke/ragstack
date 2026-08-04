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
// NAMING (docs/libraries-spec.md §0): this creates a **collection** — a registry
// entry binding (embedding model + dim + chunker) to a physical index. It is NOT
// a "library" (a user-owned, access-controlled document set *inside* a collection,
// isolated by `library_id`), which is not implemented yet — see #230. This form
// used to be called NewLibraryForm while posting to /v1/collections; the two
// concepts must not collide again.
//
// Chunking is build-time identity for a collection — it can't be edited later —
// so it is chosen HERE rather than inherited from a server default the user
// can't see. The defaults reproduce the previous hardcoded behaviour
// (fixed_token / 512 / 64), so the common path is still: type a name, Create.
//
// The chunk controls come from the shared ChunkStrategyPicker, which the Ops
// admin panel also uses — one definition of what a collection's chunking is.
export function NewCollectionForm({
  onCreate,
  onCancel,
  pending,
  error,
}: {
  onCreate: (name: string, chunk: ChunkConfigBody) => void;
  onCancel: () => void;
  pending: boolean;
  error: string | null;
}) {
  const [name, setName] = useState("");
  const [form, setForm] = useState<ChunkForm>(DEFAULT_CHUNK_FORM);
  const [touched, setTouched] = useState(false);

  const chunkProblem = validateChunkForm(form);
  const nameProblem = name.trim() === "" ? "Give the collection a name." : null;
  const problem = nameProblem ?? chunkProblem;

  const submit = () => {
    setTouched(true);
    if (problem) return;
    onCreate(name.trim(), buildChunkConfig(form));
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

      <ChunkStrategyPicker idPrefix="new-col" form={form} onChange={setForm} />

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
