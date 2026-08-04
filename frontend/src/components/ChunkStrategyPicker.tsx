import {
  CHUNK_METHODS,
  CHUNK_METHOD_INFO,
  SEMANTIC_PARAMS,
  isChunkMethod,
  type ChunkForm,
} from "../lib/chunkers";

// The chunk-strategy control, shared by every place that mints a collection:
// the demo's NewCollectionForm and the Ops admin panel. Chunking is build-time
// *identity* for a collection (it can't be edited afterwards), so the same
// choices — and the same explanations of them — must appear wherever a
// collection is created; two divergent copies of this picker would be two
// different definitions of what a collection is.
//
// Purely presentational: it owns no state. The parent holds the `ChunkForm` and
// validates it with `validateChunkForm` before calling `buildChunkConfig`.
//
// Size/overlap are shown only for the methods that measure by them, and are
// labelled with the unit that method actually counts in (characters for
// fixed/sentence/words, tokens for fixed_token). The semantic methods surface
// their own tunables instead and send no size/overlap at all
// (see lib/chunkers.ts::buildChunkConfig).
export function ChunkStrategyPicker({
  idPrefix,
  form,
  onChange,
}: {
  // Namespaces the input ids so two pickers can coexist on one page without
  // colliding label→control associations.
  idPrefix: string;
  form: ChunkForm;
  onChange: (next: ChunkForm) => void;
}) {
  const info = CHUNK_METHOD_INFO[form.method];
  const sized = info.unit !== null;

  const setParam = (key: string, value: string) =>
    onChange({ ...form, params: { ...form.params, [key]: value } });

  return (
    <>
      <div>
        <label
          htmlFor={`${idPrefix}-method`}
          className="mb-1 block text-xs font-medium text-gray-500"
        >
          Chunker
        </label>
        <select
          id={`${idPrefix}-method`}
          value={form.method}
          onChange={(e) => {
            const m = e.target.value;
            if (isChunkMethod(m)) onChange({ ...form, method: m });
          }}
          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
        >
          {CHUNK_METHODS.map((m) => (
            <option key={m} value={m}>
              {CHUNK_METHOD_INFO[m].label}
            </option>
          ))}
        </select>
        <p className="mt-2 text-xs text-gray-500">{info.blurb}</p>
      </div>

      {sized ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label
              htmlFor={`${idPrefix}-size`}
              className="mb-1 block text-xs font-medium text-gray-500"
            >
              Chunk size ({info.unit})
            </label>
            <input
              id={`${idPrefix}-size`}
              type="number"
              inputMode="numeric"
              value={form.size}
              onChange={(e) => onChange({ ...form, size: e.target.value })}
              className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
            />
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-overlap`}
              className="mb-1 block text-xs font-medium text-gray-500"
            >
              Overlap ({info.unit})
            </label>
            <input
              id={`${idPrefix}-overlap`}
              type="number"
              inputMode="numeric"
              value={form.overlap}
              onChange={(e) => onChange({ ...form, overlap: e.target.value })}
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
                  htmlFor={`${idPrefix}-${spec.key}`}
                  className="mb-1 block text-xs font-medium text-gray-500"
                  title={spec.help}
                >
                  {spec.label}
                </label>
                <input
                  id={`${idPrefix}-${spec.key}`}
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
    </>
  );
}
