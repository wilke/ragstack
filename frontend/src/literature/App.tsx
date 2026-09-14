// The literature demo's one screen: a structured query form over ragstack
// retrieval, with BV-BRC Copilot generation underneath.
//
// THE TWO-LEG SHAPE, which is the thing this demo exists to show:
//
//   1. RETRIEVAL is ragstack. The form's fields become a query string AND a
//      `filters` object; the contract governs both, and the request is
//      reproducible — the same body replays in curl, in Compare, in conformance.
//   2. GENERATION is BV-BRC's Copilot. The prompt is built client-side from the
//      retrieved sources, because that service owns the model and the domain
//      vocabulary. ragstack never sees it.
//
// That split is deliberate: ragstack is text and information retrieval, not
// answer interpretation. The "View / edit prompt" control makes the seam
// visible on stage — you can see exactly what was sent, and re-send it changed.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  generate,
  listCollections,
  listModels,
  retrieve,
  tenant,
  type CollectionInfo,
  type ModelInfo,
  type Source,
} from "./api";
import {
  DATA_TYPES,
  buildFilters,
  buildPrompt,
  buildQuery,
  dataType,
  collectionDetail,
  collectionUnavailable,
  sortedCollections,
  DOC_TYPES,
  YEAR_COVERAGE_NOTE,
  parseTable,
  toTsv,
  type Format,
  type ParsedTable,
  type QueryFields,
} from "./extraction";
import { getToken, setToken, subjectOf } from "./session";
import { LoginGate } from "./LoginGate";
import { AnswerPanel } from "./AnswerPanel";
import { ResultsTable } from "./ResultsTable";
import { SourceCard } from "./SourceCard";

// The dev tenant's literature corpus: "OA JATS prototype (dev) — mixed
// prose+table/figure units", 24,263 chunks, SFR-Embedding-Mistral 4096-d. Used
// as the initial selection when it is present in the caller's readable
// collections; otherwise the picker just opens on whatever they can read.
const PREFERRED_COLLECTION = "oa-dev";

const INPUT =
  "w-full rounded-panel border border-line px-3 py-2 text-sm text-strong placeholder:text-faint focus:border-ink-900 focus:outline-none";
const LABEL = "mb-1 block text-[11px] font-medium uppercase tracking-wide text-dim";

export function App() {
  // --- credential ---------------------------------------------------------
  const [token, setTokenState] = useState(getToken);
  const subject = useMemo(() => subjectOf(token), [token]);

  const saveToken = useCallback((t: string) => {
    setToken(t);
    setTokenState(t);
  }, []);

  // --- form ---------------------------------------------------------------
  const [fields, setFields] = useState<QueryFields>({
    organism: "",
    genes: "",
    otherTerms: "",
    dataTypeId: "none",
  });
  const [format, setFormat] = useState<Format>("raw");
  const [topK, setTopK] = useState(10);
  const [useGraph, setUseGraph] = useState(false);
  const [collection, setCollection] = useState("");
  const [year, setYear] = useState("");
  const [docType, setDocType] = useState("");
  const [journal, setJournal] = useState("");
  const [model, setModel] = useState("");

  const dt = dataType(fields.dataTypeId);
  // A type with no columns has no table to render, so the format control is
  // meaningless — pin it to prose, exactly as the original widget does.
  useEffect(() => {
    if (!dt.columns && format === "table") setFormat("raw");
  }, [dt.columns, format]);

  // --- discovery (needs the token, so it re-runs when one arrives) ---------
  const [collections, setCollections] = useState<CollectionInfo[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [discoveryNote, setDiscoveryNote] = useState("");

  useEffect(() => {
    if (!token) {
      setCollections([]);
      setModels([]);
      return;
    }
    let live = true;
    setDiscoveryNote("");
    void (async () => {
      try {
        const cols = await listCollections(token);
        if (!live) return;
        setCollections(cols);
        // Seed with a collection that can actually answer. The preferred one
        // wins if it is queryable; otherwise the largest queryable one; and only
        // if nothing is queryable do we fall back to the first entry, so the
        // picker still shows something rather than going blank.
        setCollection((cur) => {
          if (cur) return cur;
          const usable = sortedCollections(cols).filter((c) => !collectionUnavailable(c));
          const preferred = usable.find((c) => c.id === PREFERRED_COLLECTION);
          return preferred?.id ?? usable[0]?.id ?? cols[0]?.id ?? "";
        });
      } catch (e) {
        if (live) setDiscoveryNote(describe(e, "collections"));
      }
      try {
        const ms = await listModels(token);
        if (!live) return;
        setModels(ms);
        setModel((cur) => cur || ms[0]?.model || "");
      } catch (e) {
        // A Copilot outage must not block retrieval — the sources are still
        // useful on their own, so this is a note, not a failure.
        if (live) setDiscoveryNote((n) => n || describe(e, "models"));
      }
    })();
    return () => {
      live = false;
    };
  }, [token]);

  // --- results ------------------------------------------------------------
  const [sources, setSources] = useState<Source[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [answer, setAnswer] = useState("");
  const [generating, setGenerating] = useState(false);
  const [answerError, setAnswerError] = useState("");
  // Set when generation succeeded, but not on the model the user picked.
  const [answerNote, setAnswerNote] = useState("");
  const [prompt, setPrompt] = useState("");
  const [promptOpen, setPromptOpen] = useState(false);
  const [lastFormat, setLastFormat] = useState<Format>("raw");
  // Guards against an older, slower search overwriting a newer one's results.
  const runRef = useRef(0);

  const table: ParsedTable | null = useMemo(
    () => (lastFormat === "table" && answer ? parseTable(answer) : null),
    [lastFormat, answer],
  );

  const runGeneration = useCallback(
    async (text: string, fmt: Format, run: number) => {
      if (!model) {
        setAnswerError("No model selected — the Copilot model list is empty or unavailable.");
        return;
      }
      setGenerating(true);
      setAnswerError("");
      setAnswerNote("");
      setLastFormat(fmt);

      // A model can be ADVERTISED AND BROKEN. The Copilot list marks every model
      // `active: true`, but a model whose registry row names an endpoint that now
      // serves something else 500s on every call — as of 2026-09-14 that is
      // Llama-4-Scout non-FP8, whose mango:8004 endpoint runs Qwen3.6-35B-A3B.
      // Seeding the form with the default (api.ts listModels) keeps that out of
      // the common path, but the model is still SELECTABLE, and picking it is a
      // guaranteed failure that looks like the app is broken.
      //
      // So a 5xx on a non-default model falls back to the default once and says
      // it did. Deliberately not a hardcoded list of known-bad ids: which model
      // is broken is a deployment fact that changes without us, and the fallback
      // works whichever one it is. Only 5xx — a 401 is a credential problem and
      // retrying on another model would just produce a second, confusing 401.
      const fallback = models.find((m) => m.isDefault);
      try {
        const out = await generate(text, model, token, subject);
        if (runRef.current === run) setAnswer(out);
      } catch (e) {
        const retryable = e instanceof ApiError && e.status >= 500 && fallback && fallback.model !== model;
        if (!retryable) {
          if (runRef.current === run) setAnswerError(describe(e, "generation"));
        } else {
          try {
            const out = await generate(text, fallback.model, token, subject);
            if (runRef.current === run) {
              setAnswer(out);
              setAnswerNote(
                `${models.find((m) => m.model === model)?.label ?? model} is not responding; ` +
                  `answered with ${fallback.label} instead.`,
              );
            }
          } catch (e2) {
            if (runRef.current === run) setAnswerError(describe(e2, "generation"));
          }
        }
      } finally {
        if (runRef.current === run) setGenerating(false);
      }
    },
    [model, token, subject, models],
  );

  const search = useCallback(async () => {
    if (!fields.organism.trim()) return;
    const run = ++runRef.current;
    setSearching(true);
    setSearchError("");
    setAnswer("");
    setAnswerError("");
    setAnswerNote("");
    setPrompt("");
    setSources([]);

    const filters = buildFilters({ year, docType, journal });
    try {
      const res = await retrieve(
        {
          query: buildQuery(fields),
          top_k: topK,
          use_graph: useGraph,
          ...(collection ? { collection } : {}),
          ...(Object.keys(filters).length ? { filters } : {}),
        },
        token,
      );
      if (runRef.current !== run) return;
      setSources(res.sources ?? []);
      setSearching(false);

      if (!res.sources?.length) return;
      const text = buildPrompt(fields, format, res.sources);
      setPrompt(text);
      void runGeneration(text, format, run);
    } catch (e) {
      if (runRef.current !== run) return;
      setSearching(false);
      setSearchError(describe(e, "retrieval"));
    }
  }, [fields, year, docType, journal, topK, useGraph, collection, token, format, runGeneration]);

  const downloadTsv = useCallback(() => {
    if (!table) return;
    const blob = new Blob([toTsv(table)], { type: "text/tab-separated-values" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${fields.dataTypeId}-${Date.now()}.tsv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [table, fields.dataTypeId]);

  const modelLabel = models.find((m) => m.model === model)?.label;
  const selectedCollection = collections.find((c) => c.id === collection);

  // --- sign-in gate -------------------------------------------------------
  if (!token) return <LoginGate onToken={saveToken} />;

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-3">
        <h1 className="text-lg font-semibold text-strong">BV-BRC Literature Search</h1>
        <p className="text-xs text-dim">
          {subject || "signed in"} · corpus <code>{tenant()}</code>
          {" · "}
          <button onClick={() => saveToken("")} className="underline hover:text-strong">
            sign out
          </button>
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
        className="space-y-4 rounded-panel border border-line p-4"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="lit-organism">
              Organism of interest
            </label>
            <input
              id="lit-organism"
              className={INPUT}
              value={fields.organism}
              onChange={(e) => setFields({ ...fields, organism: e.target.value })}
              placeholder="e.g. SARS-CoV-2, Mycobacterium tuberculosis"
            />
          </div>
          <div>
            <label className={LABEL} htmlFor="lit-genes">
              Genes of interest
            </label>
            <input
              id="lit-genes"
              className={INPUT}
              value={fields.genes}
              onChange={(e) => setFields({ ...fields, genes: e.target.value })}
              placeholder="e.g. Spike, NSP13, ACE2"
            />
          </div>
        </div>

        <div>
          <label className={LABEL} htmlFor="lit-other">
            Other terms
          </label>
          <input
            id="lit-other"
            className={INPUT}
            value={fields.otherTerms}
            onChange={(e) => setFields({ ...fields, otherTerms: e.target.value })}
            placeholder="e.g. antibiotic resistance, viral entry"
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <label className={LABEL} htmlFor="lit-datatype">
              Data type
            </label>
            <select
              id="lit-datatype"
              className={INPUT}
              value={fields.dataTypeId}
              onChange={(e) => setFields({ ...fields, dataTypeId: e.target.value })}
            >
              {DATA_TYPES.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={LABEL} htmlFor="lit-format">
              Format
            </label>
            <select
              id="lit-format"
              className={INPUT}
              value={format}
              disabled={!dt.columns}
              onChange={(e) => setFormat(e.target.value as Format)}
            >
              <option value="raw">Prose</option>
              <option value="table">Table</option>
            </select>
          </div>
          <div>
            <label className={LABEL} htmlFor="lit-model">
              Model
            </label>
            <select
              id="lit-model"
              className={INPUT}
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              {models.length === 0 && <option value="">unavailable</option>}
              {models.map((m) => (
                <option key={m.model} value={m.model}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* The structured half: these become `filters` on /v1/retrieve rather
            than words in the query string, so they constrain the search instead
            of merely nudging the embedding. */}
        <fieldset className="grid gap-3 border-t border-line pt-4 sm:grid-cols-3">
          <legend className="sr-only">Retrieval filters</legend>
          <div>
            <label className={LABEL} htmlFor="lit-collection">
              Collection
            </label>
            <select
              id="lit-collection"
              className={INPUT}
              value={collection}
              onChange={(e) => setCollection(e.target.value)}
            >
              {collections.length === 0 && <option value="">default</option>}
              {sortedCollections(collections).map((c) => {
                const why = collectionUnavailable(c);
                return (
                  // Kept in the list but DISABLED when it cannot answer, rather
                  // than dropped: a user who knows a collection exists and
                  // cannot find it has no way to learn why it is missing.
                  <option key={c.id} value={c.id} disabled={why !== null}>
                    {c.label || c.id}
                    {why ? ` — ${why}` : ""}
                  </option>
                );
              })}
            </select>
            {selectedCollection && (
              <p className="mt-1 text-[11px] text-faint">{collectionDetail(selectedCollection)}</p>
            )}
          </div>
          <div>
            <label className={LABEL} htmlFor="lit-journal">
              Journal
            </label>
            <input
              id="lit-journal"
              className={INPUT}
              value={journal}
              onChange={(e) => setJournal(e.target.value)}
              placeholder="e.g. Viruses, PLoS Pathogens"
            />
          </div>
          <div>
            <label className={LABEL} htmlFor="lit-doctype">
              Document type
            </label>
            <select
              id="lit-doctype"
              className={INPUT}
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
            >
              <option value="">any</option>
              {DOC_TYPES.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={LABEL} htmlFor="lit-year">
              Year
            </label>
            <input
              id="lit-year"
              className={INPUT}
              value={year}
              inputMode="numeric"
              onChange={(e) => setYear(e.target.value)}
              placeholder="e.g. 2021"
            />
            {year.trim() && <p className="mt-1 text-[11px] text-faint">{YEAR_COVERAGE_NOTE}</p>}
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-4 border-t border-line pt-4">
          <label className="flex items-center gap-2 text-xs text-dim">
            Sources
            <input
              type="range"
              min={1}
              max={25}
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
            />
            <span className="w-6 font-mono text-strong">{topK}</span>
          </label>
          <label className="flex items-center gap-2 text-xs text-dim">
            <input type="checkbox" checked={useGraph} onChange={(e) => setUseGraph(e.target.checked)} />
            Knowledge graph
          </label>
          <div className="ml-auto flex gap-2">
            {prompt && (
              <button
                type="button"
                onClick={() => setPromptOpen(true)}
                className="rounded-pill border border-line px-4 py-2 text-sm text-strong hover:border-ink-900"
              >
                View / edit prompt
              </button>
            )}
            <button
              type="submit"
              disabled={searching || !fields.organism.trim()}
              className="rounded-pill bg-accent px-5 py-2 text-sm font-semibold text-ink-900 disabled:opacity-50"
            >
              {searching ? "Searching…" : "Search & generate"}
            </button>
          </div>
        </div>
      </form>

      {discoveryNote && <p className="mt-3 text-xs text-dim">{discoveryNote}</p>}
      {searchError && <p className="mt-4 text-sm text-strong">{searchError}</p>}

      {(generating || answer || answerError) && (
        <div className="mt-6">
          {table ? (
            <>
              {answerNote && <p className="mb-2 text-[11px] text-faint">{answerNote}</p>}
              <ResultsTable table={table} onDownload={downloadTsv} />
            </>
          ) : (
            <>
              {answerNote && <p className="mb-2 text-[11px] text-faint">{answerNote}</p>}
              <AnswerPanel
                text={answer}
                modelLabel={modelLabel}
                pending={generating}
                error={answerError}
              />
            </>
          )}
        </div>
      )}

      {sources.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 text-xs font-medium uppercase tracking-wide text-dim">
            {sources.length} source{sources.length === 1 ? "" : "s"}
          </h2>
          <div className="space-y-3">
            {sources.map((s, i) => (
              <SourceCard key={s.chunk_id || i} source={s} rank={i + 1} />
            ))}
          </div>
        </section>
      )}

      {promptOpen && (
        <PromptDialog
          value={prompt}
          onChange={setPrompt}
          onClose={() => setPromptOpen(false)}
          onRegenerate={(edited) => {
            setPrompt(edited);
            setPromptOpen(false);
            void runGeneration(edited, lastFormat, runRef.current);
          }}
        />
      )}
    </div>
  );
}

/**
 * The prompt inspector. Worth keeping from the original widget: it makes the
 * retrieval/generation seam visible — you can read exactly what the sources
 * turned into, edit it, and re-send without re-retrieving.
 */
function PromptDialog({
  value,
  onChange,
  onClose,
  onRegenerate,
}: {
  value: string;
  onChange: (v: string) => void;
  onClose: () => void;
  onRegenerate: (edited: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="View or edit prompt"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-panel bg-white p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-2 text-sm font-semibold text-strong">Prompt sent to the model</h2>
        <textarea
          value={draft}
          spellCheck={false}
          onChange={(e) => {
            setDraft(e.target.value);
            onChange(e.target.value);
          }}
          className="min-h-[50vh] flex-1 rounded-panel border border-line p-3 font-mono text-xs text-strong"
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-pill border border-line px-4 py-2 text-sm text-strong"
          >
            Close
          </button>
          <button
            onClick={() => draft.trim() && onRegenerate(draft)}
            className="rounded-pill bg-accent px-4 py-2 text-sm font-semibold text-ink-900"
          >
            Regenerate
          </button>
        </div>
      </div>
    </div>
  );
}

/** Turn a thrown value into something a person on a stage can act on. */
function describe(e: unknown, what: string): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return `Not authorized for ${what} — the token may have expired. Sign out and paste a fresh one.`;
    if (e.status === 404) return `No such collection, or it is not readable by this account (${what}).`;
    if (e.status === 0) return `Could not reach the ${what} service: ${e.message}`;
    return `${what} failed (HTTP ${e.status}). ${e.message}`;
  }
  return `${what} failed: ${String(e)}`;
}
