import { useState } from "react";
import {
  CHUNK_METHODS,
  CHUNK_METHOD_INFO,
  DEFAULT_CHUNK_FORM,
  SEMANTIC_PARAMS,
  buildChunkConfig,
  isChunkMethod,
  validateChunkForm,
  type ChunkConfigBody,
  type ChunkForm,
} from "../lib/chunkers";

// Inline "New library" panel: name + chunk strategy.
//
// Chunking is build-time identity for a collection — it can't be edited later —
// so it is chosen HERE rather than inherited from a server default the user
// can't see. The defaults reproduce the previous hardcoded behaviour
// (fixed_token / 512 / 64), so the common path is still: type a name, Create.
//
// Size/overlap are shown only for the methods that measure by them; the semantic
// methods surface their own tunables instead and send no size/overlap at all
// (see lib/chunkers.ts::buildChunkConfig).
export function NewLibraryForm({
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

  const info = CHUNK_METHOD_INFO[form.method];
  const sized = info.unit !== null;
  const chunkProblem = validateChunkForm(form);
  const nameProblem = name.trim() === "" ? "Give the library a name." : null;
  const problem = nameProblem ?? chunkProblem;

  const submit = () => {
    setTouched(true);
    if (problem) return;
    onCreate(name.trim(), buildChunkConfig(form));
  };

  const setParam = (key: string, value: string) =>
    setForm((f) => ({ ...f, params: { ...f.params, [key]: value } }));

  return (
    <div className="mb-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-800">New library</h3>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="new-lib-name" className="mb-1 block text-xs font-medium text-gray-500">
            Name
          </label>
          <input
            id="new-lib-name"
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
        </div>

        <div>
          <label htmlFor="new-lib-method" className="mb-1 block text-xs font-medium text-gray-500">
            Chunker
          </label>
          <select
            id="new-lib-method"
            value={form.method}
            onChange={(e) => {
              const m = e.target.value;
              if (isChunkMethod(m)) setForm((f) => ({ ...f, method: m }));
            }}
            className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
          >
            {CHUNK_METHODS.map((m) => (
              <option key={m} value={m}>
                {CHUNK_METHOD_INFO[m].label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="mt-2 text-xs text-gray-500">{info.blurb}</p>

      {sized ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="new-lib-size" className="mb-1 block text-xs font-medium text-gray-500">
              Chunk size ({info.unit})
            </label>
            <input
              id="new-lib-size"
              type="number"
              inputMode="numeric"
              value={form.size}
              onChange={(e) => setForm((f) => ({ ...f, size: e.target.value }))}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
            />
          </div>
          <div>
            <label
              htmlFor="new-lib-overlap"
              className="mb-1 block text-xs font-medium text-gray-500"
            >
              Overlap ({info.unit})
            </label>
            <input
              id="new-lib-overlap"
              type="number"
              inputMode="numeric"
              value={form.overlap}
              onChange={(e) => setForm((f) => ({ ...f, overlap: e.target.value }))}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
            />
          </div>
          {info.allowsWholeDoc ? (
            <p className="text-xs text-gray-400 sm:col-span-2">
              Size -1 keeps each document as a single chunk.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="mt-3">
          <p className="mb-2 text-xs text-gray-500">
            Boundaries come from the text itself, so there is no fixed size or overlap. Leave a
            field blank to use the server&apos;s configured default.
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            {SEMANTIC_PARAMS.map((spec) => (
              <div key={spec.key}>
                <label
                  htmlFor={`new-lib-${spec.key}`}
                  className="mb-1 block text-xs font-medium text-gray-500"
                  title={spec.help}
                >
                  {spec.label}
                </label>
                <input
                  id={`new-lib-${spec.key}`}
                  type="number"
                  inputMode="numeric"
                  value={form.params[spec.key] ?? ""}
                  placeholder={`${spec.serverDefault} (default)`}
                  onChange={(e) => setParam(spec.key, e.target.value)}
                  className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
                />
                <p className="mt-1 text-[11px] leading-snug text-gray-400">{spec.help}</p>
              </div>
            ))}
          </div>
        </div>
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
          {pending ? "Creating…" : "Create library"}
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
          Chunking is fixed when the library is created — it can&apos;t be changed later.
        </span>
      </div>
    </div>
  );
}
