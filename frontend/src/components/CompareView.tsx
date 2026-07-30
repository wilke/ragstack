import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  fetchChunks,
  getAvailableModels,
  getCollections,
  queryRag,
  type AvailableModel,
  type ChunkOut,
  type CollectionInfo,
  type QueryResponse,
  type Source,
} from "../api/client";

// Compare module: run ONE query across several lanes — each a (collection,
// optional API key) pair — and lay the answers out side by side so retrieval
// strategies (chunkers, embedding models) or tenants can be A/B'd and ranked.
// The collection axis is server-supported via the `collection` param; the tenant
// axis is a per-lane API-key override (tenant is server-derived from the key).
// Backend needs nothing new — it's N independent /v1/query calls.

type LaneResult = {
  status: "pending" | "success" | "error";
  data?: QueryResponse;
  error?: string;
  ms?: number;
};

type Mode = "hybrid" | "vector" | "bm25";
type Rerank = "default" | "on" | "off"; // → server default | force on | force off
type Rewrite = "none" | "multiquery" | "hyde";

// The pipeline levers — each maps to a /v1/query field so a single question can
// be compared across retrieval *strategies*, not just corpora. Held both as a
// global template (shared by every lane) and, when overrides are on, per-lane.
interface Levers {
  mode: Mode; // retrieval_mode
  rerank: Rerank; // rerank: null | true | false
  useGraph: boolean; // use_graph
  rewrite: Rewrite; // rewrite_strategies
  topK: number | null; // top_k; null → inherit the global default
  llm: string; // registered model id for generation; "" → server default
  reranker: string; // registered model id for reranking; "" → server default
}

const DEFAULT_LEVERS: Levers = {
  mode: "hybrid",
  rerank: "default",
  useGraph: true,
  rewrite: "none",
  topK: null,
  llm: "",
  reranker: "",
};

interface Lane {
  key: string;
  collection: string; // "" → default collection
  apiKey: string; // "" → inherit the shared key (same tenant)
  levers: Levers; // used only when per-lane overrides are on
}

let _seq = 0;
const newLane = (collection = "", apiKey = "", levers = DEFAULT_LEVERS): Lane => ({
  key: `lane-${_seq++}`,
  collection,
  apiKey,
  levers: { ...levers },
});

const MAX_LANES = 6;
const GLOBAL_DEFAULT_TOPK = 5;

// The non-default levers, as short chips — so two lanes on the same collection
// but different pipelines are distinguishable in the header/leaderboard.
const leverTags = (v: Levers): string[] => {
  const t: string[] = [];
  if (v.mode !== "hybrid") t.push(v.mode);
  if (v.rewrite !== "none") t.push(v.rewrite);
  if (v.rerank !== "default") t.push(`rerank:${v.rerank}`);
  if (!v.useGraph) t.push("no-graph");
  if (v.topK != null) t.push(`k=${v.topK}`);
  if (v.llm) t.push(`llm:${v.llm}`);
  if (v.reranker) t.push(`rr:${v.reranker}`);
  return t;
};

const rewriteStrategies = (r: Rewrite): string[] =>
  r === "none" ? ["passthrough"] : ["passthrough", r];

const rerankValue = (r: Rerank): boolean | null =>
  r === "default" ? null : r === "on";

// Hover copy for the lever labels (native title tooltips). The Glossary below
// carries the per-term detail.
const LABEL_TIP: Record<string, string> = {
  mode: "Which retrieval legs run. hybrid = dense vectors + BM25 keyword (fused); vector = dense only; bm25 = keyword only.",
  rewrite:
    "Expand the query before retrieving. none = as-is; multiquery = LLM paraphrases; hyde = retrieve on a hypothetical answer.",
  rerank: "Cross-encoder re-scoring of the results. default = server setting; on / off = force for this lane.",
  top_k: "How many results to return per lane.",
  graph: "Also retrieve from the knowledge graph (entities & relations) as an extra leg.",
  llm: "Which registered model generates the answer for this lane. default = the server's assigned LLM. Retrieval is unchanged, so this is a clean A/B of generation.",
  rerankerModel: "Which registered cross-encoder reranks this lane's results. default = the server's assigned reranker.",
};

// Grouped definitions rendered by <Glossary/> at the foot of the page.
const GLOSSARY: { group: string; items: { term: string; def: string }[] }[] = [
  {
    group: "Retrieval mode",
    items: [
      { term: "hybrid", def: "Both retrieval legs — dense vectors + BM25 keyword — fused with RRF. The default; best recall." },
      { term: "vector", def: "Dense-embedding (semantic) retrieval only. Finds meaning-similar text even without shared words." },
      { term: "bm25", def: "Keyword / lexical retrieval only (Elasticsearch BM25). Fast, needs no embedding; rewards exact term matches." },
    ],
  },
  {
    group: "Query rewriting",
    items: [
      { term: "none", def: "No rewriting — the query is sent unchanged (passthrough only)." },
      { term: "passthrough", def: "The original query, unmodified. Always included even when another strategy runs." },
      { term: "multiquery", def: "The LLM generates several paraphrases of your question; each one retrieves and the lists are fused — widens recall." },
      { term: "hyde", def: "Hypothetical Document Embeddings: the LLM drafts a fake answer, then retrieves documents similar to that draft. Helps vague queries." },
    ],
  },
  {
    group: "Reranking",
    items: [
      { term: "rerank: default", def: "Use the server default — rerank only if a cross-encoder is wired." },
      { term: "rerank: on / off", def: "Force reranking on or off for this lane, overriding the server default." },
      { term: "cross-encoder", def: "A model that re-scores candidates by reading query + document together — more accurate ordering than first-stage retrieval." },
    ],
  },
  {
    group: "Lane levers",
    items: [
      { term: "top_k", def: "Number of results returned per lane." },
      { term: "knowledge graph", def: "An extra retrieval leg over an entity/relationship graph, added on top of the chosen mode." },
      { term: "collection", def: "One indexed corpus — a fixed build of (embedding model + chunking strategy). The main axis you compare." },
      { term: "tenant", def: "Data-isolation scope derived from the API key. A lane can supply its own key to compare tenants." },
    ],
  },
  {
    group: "Model overrides",
    items: [
      { term: "llm", def: "A registered model used to generate the answer for this lane only — the corpus and retrieval stay fixed, so it's a clean A/B of generation. 'default' uses the server's assigned LLM." },
      { term: "rr·model", def: "A registered cross-encoder used to rerank this lane only. Distinct from the rerank on/off lever, which just gates whether reranking runs." },
      { term: "registered model", def: "A model (URL + name) an admin has registered for a task (llm/reranker); the pickers list only these, curated and SSRF-checked." },
    ],
  },
  {
    group: "Fusion & scoring",
    items: [
      { term: "RRF", def: "Reciprocal Rank Fusion — merges multiple ranked lists by rank position (k=60), not by raw scores, so different scales combine safely." },
      { term: "score", def: "A lane's relevance score. Not comparable across lanes (different models/fusions), which is why agreement is measured on rank." },
    ],
  },
  {
    group: "Agreement metrics",
    items: [
      { term: "chunk overlap", def: "Jaccard on retrieved chunk_ids — used when lanes share a collection (same chunker), so chunks are the same units. The exact retrieval-agreement measure." },
      { term: "passage-span overlap", def: "Across shared docs, intersection ÷ union of the retrieved char-ranges. Granularity-independent, so it's the honest cross-chunker signal: did the lanes surface the same passage, not just the same document?" },
      { term: "document overlap", def: "Jaccard on doc_ids. A recall-robustness signal (is this doc found regardless of chunker?), but confounded by chunk granularity — a coarser chunker returns more unique docs per top-k, so cross-chunker doc overlap reads low for reasons unrelated to relevance." },
      { term: "Kendall τ (order)", def: "Rank-order agreement over the items two lanes share. +1 = identical order, 0 = unrelated, −1 = reversed." },
      { term: "answer agreement", def: "Bag-of-words overlap of the lanes' generated answers — the outcome retrieval agreement approximates. Lexical, so paraphrases read lower than they truly agree." },
      { term: "consensus (×) / coverage (×N)", def: "× = how many lanes retrieved a doc (recall). ×N on a rank badge = how many of that lane's chunks came from the doc (why finer chunkers list fewer unique docs)." },
    ],
  },
  {
    group: "Chunking (in collection names)",
    items: [
      { term: "fixed_token", def: "Fixed-size windows measured in model tokens (e.g. 256 / 512), with overlap. Sizes are consistent for the embedder." },
      { term: "fixed (char)", def: "Fixed-size windows measured in characters. Simpler, but token counts vary by text." },
      { term: "semantic", def: "Splits where the topic shifts, detected by embedding successive buffers and cutting at similarity drops." },
      { term: "semantic_pooled", def: "Embeds each sentence once and mean-pools — a cheaper, reproducible variant of semantic chunking." },
    ],
  },
];

// One control cluster for the levers — reused by the global panel and, when
// overrides are on, by each lane. ``topKPlaceholder`` shows the inherited
// default when the field is left blank.
function LeverControls({
  value,
  onChange,
  topKPlaceholder,
  models = [],
}: {
  value: Levers;
  onChange: (patch: Partial<Levers>) => void;
  topKPlaceholder: string;
  models?: AvailableModel[];
}) {
  const sel = "min-w-0 flex-1 rounded border border-gray-200 px-1 py-0.5";
  const llmModels = models.filter((m) => m.task === "llm");
  const rerankerModels = models.filter((m) => m.task === "reranker");
  return (
    <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px] text-gray-500">
      <label className="flex items-center gap-1" title={LABEL_TIP.mode}>
        <span className="w-9 shrink-0 cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
          mode
        </span>
        <select
          value={value.mode}
          onChange={(e) => onChange({ mode: e.target.value as Mode })}
          className={sel}
        >
          <option value="hybrid">hybrid</option>
          <option value="vector">vector</option>
          <option value="bm25">bm25</option>
        </select>
      </label>
      <label className="flex items-center gap-1" title={LABEL_TIP.rewrite}>
        <span className="w-12 shrink-0 cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
          rewrite
        </span>
        <select
          value={value.rewrite}
          onChange={(e) => onChange({ rewrite: e.target.value as Rewrite })}
          className={sel}
        >
          <option value="none">none</option>
          <option value="multiquery">multiquery</option>
          <option value="hyde">hyde</option>
        </select>
      </label>
      <label className="flex items-center gap-1" title={LABEL_TIP.rerank}>
        <span className="w-9 shrink-0 cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
          rerank
        </span>
        <select
          value={value.rerank}
          onChange={(e) => onChange({ rerank: e.target.value as Rerank })}
          className={sel}
        >
          <option value="default">default</option>
          <option value="on">on</option>
          <option value="off">off</option>
        </select>
      </label>
      <label className="flex items-center gap-1" title={LABEL_TIP.top_k}>
        <span className="w-12 shrink-0 cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
          top_k
        </span>
        <input
          type="number"
          min={1}
          max={20}
          value={value.topK ?? ""}
          placeholder={topKPlaceholder}
          onChange={(e) => {
            const v = e.target.value.trim();
            onChange({ topK: v === "" ? null : Math.max(1, Math.min(20, Number(v) || 1)) });
          }}
          className={`${sel} tabular-nums`}
        />
      </label>
      <label className="col-span-2 flex items-center gap-1.5" title={LABEL_TIP.graph}>
        <input
          type="checkbox"
          checked={value.useGraph}
          onChange={(e) => onChange({ useGraph: e.target.checked })}
        />
        <span className="cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
          use knowledge graph
        </span>
      </label>
      {llmModels.length > 0 ? (
        <label className="col-span-2 flex items-center gap-1" title={LABEL_TIP.llm}>
          <span className="w-9 shrink-0 cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
            llm
          </span>
          <select
            value={value.llm}
            onChange={(e) => onChange({ llm: e.target.value })}
            className={sel}
          >
            <option value="">default</option>
            {llmModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {rerankerModels.length > 0 ? (
        <label className="col-span-2 flex items-center gap-1" title={LABEL_TIP.rerankerModel}>
          <span className="w-9 shrink-0 cursor-help text-gray-400 underline decoration-dotted underline-offset-2">
            rr·model
          </span>
          <select
            value={value.reranker}
            onChange={(e) => onChange({ reranker: e.target.value })}
            className={sel}
          >
            <option value="">default</option>
            {rerankerModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}

function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex items-center gap-0.5" role="radiogroup" aria-label="rating">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          aria-label={`${n} star${n > 1 ? "s" : ""}`}
          aria-checked={value === n}
          onClick={() => onChange(value === n ? 0 : n)}
          className={`text-lg leading-none ${n <= value ? "text-amber-500" : "text-gray-300 hover:text-amber-300"}`}
        >
          ★
        </button>
      ))}
    </div>
  );
}

// A neighbouring chunk (prev/next), rendered above/below the matched passage.
function ContextChunk({ chunk, position }: { chunk: ChunkOut; position: "prev" | "next" }) {
  const idx =
    typeof chunk.metadata.chunk_index === "number" ? chunk.metadata.chunk_index : undefined;
  return (
    <div className="my-1 border-l-2 border-gray-200 bg-gray-50 py-1 pl-2">
      <div className="text-[10px] font-medium uppercase tracking-wide text-gray-400">
        {position === "prev" ? "◀ previous chunk" : "next chunk ▶"}
        {idx !== undefined ? ` · #${idx}` : ""}
      </div>
      <p className="whitespace-pre-wrap text-[11px] leading-snug text-gray-500">{chunk.content}</p>
    </div>
  );
}

type Ctx = {
  loading: boolean;
  error?: string;
  prev?: ChunkOut; // absent if there's no prev id, or it wasn't found/visible
  next?: ChunkOut;
};

// A human label for a document. `doc_id` is global across lanes (so it's the
// right join key), but it's an opaque uuid — and not every PDF had a `title`
// extracted at ingest. Fall back through filename → source_path basename → doi
// before showing the raw id, so the Compare rows are identifiable.
function docLabel(m: Source["metadata"], docId: string): string {
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : "");
  return (
    str(m.title) ||
    str(m.filename) ||
    (str(m.source_path) ? str(m.source_path).split("/").pop()! : "") ||
    (str(m.doi) ? `doi:${str(m.doi)}` : "") ||
    docId
  );
}

// One shared column template for the sources header and every row, so rank /
// document / score line up exactly even across side-by-side lanes. The document
// column is minmax(0,1fr) so long titles truncate instead of widening the grid.
const SOURCE_GRID = "grid grid-cols-[1.5rem_minmax(0,1fr)_3.25rem] items-baseline gap-x-2";

function CompareSource({
  rank,
  source,
  collection,
  apiKey,
}: {
  rank: number;
  source: Source;
  collection: string;
  apiKey: string;
}) {
  const [open, setOpen] = useState(false);
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const title = docLabel(source.metadata, source.doc_id);
  const m = source.metadata;
  const prevId = m.prev_chunk_id || undefined;
  const nextId = m.next_chunk_id || undefined;
  const hasNbr = Boolean(prevId || nextId);
  const idx = typeof m.chunk_index === "number" ? m.chunk_index : undefined;

  const loadContext = async () => {
    if (ctx?.loading) return;
    // Toggle off if already loaded.
    if (ctx && !ctx.error) {
      setCtx(null);
      return;
    }
    setCtx({ loading: true });
    const ids = [prevId, nextId].filter(Boolean) as string[];
    try {
      const r = await fetchChunks(ids, collection || undefined, apiKey || undefined);
      const byId = new Map(r.chunks.map((c) => [c.chunk_id, c]));
      setCtx({
        loading: false,
        prev: prevId ? byId.get(prevId) : undefined,
        next: nextId ? byId.get(nextId) : undefined,
      });
    } catch (e) {
      setCtx({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    // The row IS the grid, so its cells sit in the same columns as the header;
    // the expanded detail spans the doc+score columns, indented past the rank.
    <li className={`${SOURCE_GRID} border-t border-gray-100 py-1.5`}>
      <span className="text-right text-[11px] tabular-nums text-gray-400">{rank}.</span>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="min-w-0 truncate text-left text-xs font-medium text-gray-700"
        title={title}
      >
        {title}
      </button>
      <span className="text-right tabular-nums text-[11px] text-gray-400">
        {source.score.toFixed(4)}
      </span>

      <div className="col-span-2 col-start-2 min-w-0">
        {ctx && !ctx.loading && !ctx.error && ctx.prev ? (
          <ContextChunk chunk={ctx.prev} position="prev" />
        ) : null}

        <p
          onClick={() => setOpen((o) => !o)}
          title={source.content}
          className={`mt-0.5 cursor-pointer whitespace-pre-wrap break-words text-xs text-gray-500 ${open ? "" : "line-clamp-2"}`}
        >
          {source.content}
        </p>

        {ctx && !ctx.loading && !ctx.error && ctx.next ? (
          <ContextChunk chunk={ctx.next} position="next" />
        ) : null}

        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-gray-400">
          <button type="button" onClick={() => setOpen((o) => !o)} className="hover:text-gray-600">
            {open ? "▴ collapse" : "▾ full text"}
          </button>
          {idx !== undefined ? <span>· chunk #{idx}</span> : null}
          {hasNbr ? (
            <button
              type="button"
              onClick={loadContext}
              disabled={ctx?.loading}
              className="text-blue-600 hover:underline disabled:opacity-50"
            >
              {ctx?.loading
                ? "· loading…"
                : ctx && !ctx.error
                  ? "· hide context"
                  : "· ± parent/child"}
            </button>
          ) : (
            <span className="text-gray-300">· no neighbours</span>
          )}
        </div>
        {ctx?.error ? <p className="text-[10px] text-red-500">context: {ctx.error}</p> : null}
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------- //
// Agreement analysis — how much do the lanes actually converge?
//
// Lanes are joined by `doc_id` (stable across chunkers/models, unlike chunk_id),
// so this works whether lanes vary by corpus, model, or pipeline. Each lane's
// sources are de-duplicated to unique docs in retrieval order; comparisons are
// rank-based because raw scores aren't commensurable across different embedding
// models / fusions.
// --------------------------------------------------------------------------- //

interface LaneEntry {
  key: string;
  label: string;
  collection: string; // which collection the lane queried — same value ⇒ same chunker
  answer: string; // the lane's generated answer, for answer-agreement
  sources: Source[];
}

// Chunks are the real retrieval unit; chunk_id is comparable only WITHIN a
// collection (same chunker). Ranked by retrieval order.
function laneChunks(sources: Source[]): { id: string; rank: number }[] {
  return sources.map((s, i) => ({ id: s.chunk_id, rank: i + 1 }));
}

type Span = [number, number];
interface DocAgg {
  doc_id: string;
  rank: number; // by aggregate (max) chunk relevance within the lane
  chunkCount: number; // how many of the lane's chunks came from this doc (coverage)
  spans: Span[]; // char ranges into the ORIGINAL doc, for cross-chunker span overlap
  title: string;
}

// Collapse a lane's chunks to unique docs, aggregating relevance and coverage.
// Ranked by best chunk score (== retrieval order for a score-sorted list), but we
// also keep chunkCount so the granularity confound is visible, and spans so
// cross-chunker passage overlap is measurable.
function laneDocs(sources: Source[]): DocAgg[] {
  const by = new Map<string, DocAgg & { agg: number }>();
  sources.forEach((s) => {
    let d = by.get(s.doc_id);
    if (!d) {
      d = {
        doc_id: s.doc_id, rank: 0, chunkCount: 0, spans: [],
        title: docLabel(s.metadata, s.doc_id), agg: s.score,
      };
      by.set(s.doc_id, d);
    }
    d.agg = Math.max(d.agg, s.score);
    d.chunkCount += 1;
    const st = s.metadata.start_char;
    const en = s.metadata.end_char;
    if (typeof st === "number" && typeof en === "number" && en > st) d.spans.push([st, en]);
  });
  return [...by.values()]
    .sort((a, b) => b.agg - a.agg)
    .map((d, i) => ({ doc_id: d.doc_id, rank: i + 1, chunkCount: d.chunkCount, spans: d.spans, title: d.title }));
}

function jaccard<T>(a: Set<T>, b: Set<T>): number {
  if (!a.size && !b.size) return 0;
  let inter = 0;
  a.forEach((x) => {
    if (b.has(x)) inter++;
  });
  return inter / (a.size + b.size - inter);
}

// Total covered length of a set of (possibly overlapping) spans.
function mergedLength(spans: Span[]): number {
  if (!spans.length) return 0;
  const sorted = [...spans].sort((p, q) => p[0] - q[0]);
  let total = 0;
  let [cs, ce] = sorted[0];
  for (let k = 1; k < sorted.length; k++) {
    const [s, e] = sorted[k];
    if (s <= ce) ce = Math.max(ce, e);
    else {
      total += ce - cs;
      [cs, ce] = [s, e];
    }
  }
  return total + (ce - cs);
}

// Passage-span overlap between two lanes: over the docs they share, how much of
// the retrieved source text coincides (intersection ÷ union of char ranges).
// Granularity-independent — the meaningful cross-chunker signal. null when the
// shared docs carry no offsets.
function spanIoU(
  spansA: Map<string, Span[]>,
  spansB: Map<string, Span[]>,
  sharedDocs: string[],
): number | null {
  let inter = 0;
  let union = 0;
  let sawSpans = false;
  for (const doc of sharedDocs) {
    const a = spansA.get(doc) ?? [];
    const b = spansB.get(doc) ?? [];
    if (!a.length || !b.length) continue;
    sawSpans = true;
    const la = mergedLength(a);
    const lb = mergedLength(b);
    const both = mergedLength([...a, ...b]); // union
    const overlap = la + lb - both; // inclusion-exclusion
    inter += overlap;
    union += both;
  }
  if (!sawSpans || union === 0) return null;
  return inter / union;
}

// Lexical answer similarity: Jaccard over content-word tokens (≥3 chars). A crude
// but honest proxy for "did the lanes say the same thing" — the outcome retrieval
// agreement only approximates.
function answerSim(a: string, b: string): number | null {
  const tok = (s: string) => new Set(s.toLowerCase().match(/[a-z0-9]{3,}/g) ?? []);
  const A = tok(a);
  const B = tok(b);
  if (!A.size || !B.size) return null;
  return jaccard(A, B);
}

// Kendall's τ-b-ish rank correlation over the docs two lanes have in common.
// +1 identical order, −1 reversed, 0 none; null when fewer than 2 shared docs.
function kendallTau(a: Map<string, number>, b: Map<string, number>): number | null {
  const common = [...a.keys()].filter((k) => b.has(k));
  const n = common.length;
  if (n < 2) return null;
  let concordant = 0;
  let discordant = 0;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const s =
        Math.sign(a.get(common[i])! - a.get(common[j])!) *
        Math.sign(b.get(common[i])! - b.get(common[j])!);
      if (s > 0) concordant++;
      else if (s < 0) discordant++;
    }
  }
  const total = (n * (n - 1)) / 2;
  return total ? (concordant - discordant) / total : null;
}

// Rank → badge colour: best ranks are strongest, tapering to grey.
function rankClass(rank: number): string {
  if (rank <= 1) return "bg-emerald-600 text-white";
  if (rank <= 3) return "bg-emerald-300 text-emerald-900";
  if (rank <= 5) return "bg-emerald-100 text-emerald-800";
  return "bg-gray-100 text-gray-500";
}

function jaccardClass(j: number): string {
  if (j >= 0.8) return "bg-emerald-600 text-white";
  if (j >= 0.5) return "bg-emerald-300 text-emerald-900";
  if (j >= 0.25) return "bg-emerald-100 text-emerald-800";
  if (j > 0) return "bg-gray-100 text-gray-500";
  return "bg-gray-50 text-gray-300";
}

const pct = (x: number) => `${Math.round(x * 100)}%`;

function AgreementPanel({ entries }: { entries: LaneEntry[] }) {
  const lanes = entries.map((e) => ({
    ...e,
    chunks: laneChunks(e.sources),
    docs: laneDocs(e.sources),
  }));
  const n = lanes.length;

  const chunkSet = lanes.map((l) => new Set(l.chunks.map((c) => c.id)));
  const chunkRank = lanes.map((l) => new Map(l.chunks.map((c) => [c.id, c.rank])));
  const docSet = lanes.map((l) => new Set(l.docs.map((d) => d.doc_id)));
  const docRank = lanes.map((l) => new Map(l.docs.map((d) => [d.doc_id, d.rank])));
  const docSpans = lanes.map((l) => new Map(l.docs.map((d) => [d.doc_id, d.spans])));
  const coverOf = lanes.map((l) => new Map(l.docs.map((d) => [d.doc_id, d.chunkCount])));

  // Same collection ⇒ same chunker ⇒ chunk_ids are comparable → measure at the
  // chunk level (the exact unit). Different collection ⇒ chunk_ids don't line up:
  // fall back to document overlap (recall, granularity-confounded) + passage-span
  // overlap (precision, granularity-independent).
  const pair = (i: number, j: number) => {
    if (lanes[i].collection === lanes[j].collection) {
      return {
        mode: "chunk" as const,
        overlap: jaccard(chunkSet[i], chunkSet[j]),
        tau: kendallTau(chunkRank[i], chunkRank[j]),
        span: null as number | null,
      };
    }
    const shared = [...docSet[i]].filter((d) => docSet[j].has(d));
    return {
      mode: "doc" as const,
      overlap: jaccard(docSet[i], docSet[j]),
      tau: kendallTau(docRank[i], docRank[j]),
      span: spanIoU(docSpans[i], docSpans[j], shared),
    };
  };
  const allChunkLevel = lanes.every((l) => l.collection === lanes[0].collection);

  // Document recall robustness — union of docs, per-lane rank + coverage + count.
  const titleOf = new Map<string, string>();
  for (const l of lanes) for (const d of l.docs) if (!titleOf.has(d.doc_id)) titleOf.set(d.doc_id, d.title);
  const rows = [...titleOf.keys()].map((doc) => {
    const ranks = docRank.map((m) => m.get(doc));
    const covers = coverOf.map((m) => m.get(doc));
    const present = ranks.filter((r): r is number => r !== undefined);
    return {
      doc, title: titleOf.get(doc)!, ranks, covers,
      count: present.length,
      avgRank: present.reduce((a, b) => a + b, 0) / (present.length || 1),
    };
  });
  rows.sort((a, b) => b.count - a.count || a.avgRank - b.avgRank);
  const full = rows.filter((r) => r.count === n).length;
  const unique = rows.filter((r) => r.count === 1).length;

  // Answer agreement (lexical) — only when ≥2 lanes actually generated an answer.
  const answers = lanes.map((l) => l.answer || "");
  const hasAnswers = answers.filter((a) => a.trim().length > 0).length >= 2;

  const short = (s: string) => (s.length > 22 ? `${s.slice(0, 21)}…` : s);

  return (
    <details open className="mt-6 rounded-lg border border-gray-200 bg-white">
      <summary className="cursor-pointer list-none px-4 py-3">
        <span className="text-sm font-semibold text-gray-700">Agreement</span>
        <span className="ml-2 text-xs text-gray-400">
          how much the {n} lanes converge — evidence and answers
        </span>
      </summary>

      <div className="space-y-5 border-t border-gray-100 p-4">
        {/* Comparability banner — what unit the agreement can honestly use */}
        <div
          className={`rounded-md px-3 py-2 text-xs leading-snug ${allChunkLevel ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"}`}
        >
          {allChunkLevel
            ? "All lanes share a collection → agreement is measured at the chunk level (the exact retrieval unit)."
            : "Lanes use different chunkers → chunk ids aren't comparable. Document overlap is a recall-robustness signal (confounded by chunk granularity — a coarser chunker surfaces more unique docs per top-k); passage-span overlap is the granularity-independent precision signal."}
        </div>

        {/* Retrieval agreement — chunk-level where possible, else doc + span */}
        <div>
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            Retrieval agreement · pairwise
          </div>
          <div className="overflow-x-auto">
            <table className="border-collapse text-[11px]">
              <thead>
                <tr>
                  <th className="p-1"></th>
                  {lanes.map((l) => (
                    <th key={l.key} className="max-w-24 truncate p-1 text-left font-medium text-gray-500" title={l.label}>
                      {short(l.label)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lanes.map((row, i) => (
                  <tr key={row.key}>
                    <td className="max-w-24 truncate p-1 pr-2 font-medium text-gray-500" title={row.label}>
                      {short(row.label)}
                    </td>
                    {lanes.map((_, j) => {
                      if (i === j)
                        return (
                          <td key={j} className="p-0.5">
                            <div className="flex h-11 w-20 items-center justify-center rounded bg-gray-50 text-gray-300">—</div>
                          </td>
                        );
                      const p = pair(i, j);
                      const primary = p.mode === "chunk" ? p.overlap : p.span;
                      const primaryLabel = p.mode === "chunk" ? "chunk" : "span";
                      const cls = primary === null ? "bg-gray-100 text-gray-400" : jaccardClass(primary);
                      const title =
                        p.mode === "chunk"
                          ? `chunk overlap ${pct(p.overlap)} · order τ ${p.tau === null ? "n/a" : p.tau.toFixed(2)}`
                          : `passage-span overlap ${p.span === null ? "n/a" : pct(p.span)} · document overlap ${pct(p.overlap)} · order τ ${p.tau === null ? "n/a" : p.tau.toFixed(2)}`;
                      return (
                        <td key={j} className="p-0.5">
                          <div className={`flex h-11 w-20 flex-col items-center justify-center rounded tabular-nums ${cls}`} title={title}>
                            <span className="font-semibold leading-none">{primary === null ? "n/a" : pct(primary)}</span>
                            <span className="mt-0.5 text-[9px] uppercase leading-none opacity-70">{primaryLabel}</span>
                            <span className="mt-0.5 text-[10px] leading-none opacity-80">
                              {p.mode === "chunk" ? `τ ${p.tau === null ? "–" : p.tau.toFixed(2)}` : `doc ${pct(p.overlap)}`}
                            </span>
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1.5 text-[11px] leading-snug text-gray-400">
            {allChunkLevel
              ? "chunk = Jaccard overlap of retrieved chunk_ids; τ = Kendall rank agreement. Same units, so this is exact."
              : "span = passage overlap (intersection ÷ union of retrieved char-ranges over shared docs) — same evidence, not just same document; doc = document-set Jaccard. n/a span = the corpus carries no char offsets."}
          </p>
        </div>

        {/* Answer agreement — the outcome metric (lexical) */}
        {hasAnswers ? (
          <div>
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
              Answer agreement · lexical · pairwise
            </div>
            <div className="overflow-x-auto">
              <table className="border-collapse text-[11px]">
                <thead>
                  <tr>
                    <th className="p-1"></th>
                    {lanes.map((l) => (
                      <th key={l.key} className="max-w-24 truncate p-1 text-left font-medium text-gray-500" title={l.label}>
                        {short(l.label)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lanes.map((row, i) => (
                    <tr key={row.key}>
                      <td className="max-w-24 truncate p-1 pr-2 font-medium text-gray-500" title={row.label}>
                        {short(row.label)}
                      </td>
                      {lanes.map((_, j) => {
                        if (i === j)
                          return (
                            <td key={j} className="p-0.5">
                              <div className="flex h-9 w-16 items-center justify-center rounded bg-gray-50 text-gray-300">—</div>
                            </td>
                          );
                        const sim = answerSim(answers[i], answers[j]);
                        return (
                          <td key={j} className="p-0.5">
                            <div
                              className={`flex h-9 w-16 items-center justify-center rounded tabular-nums ${sim === null ? "bg-gray-100 text-gray-400" : jaccardClass(sim)}`}
                              title={sim === null ? "one lane produced no answer" : `lexical answer overlap ${pct(sim)}`}
                            >
                              {sim === null ? "n/a" : pct(sim)}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-1.5 text-[11px] leading-snug text-gray-400">
              Bag-of-words overlap of the generated answers — the outcome that retrieval agreement only
              approximates. Lexical, so genuine paraphrases read lower than they truly agree.
            </p>
          </div>
        ) : null}

        {/* Per-document recall table with coverage */}
        <div>
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            Document recall overlap · rank (×chunks) by lane · sorted by consensus
          </div>
          <div className="mb-2 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-emerald-100 px-2.5 py-1 font-medium text-emerald-800">{full} in all {n}</span>
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-gray-600">{unique} unique to one</span>
            {lanes.map((l, i) => (
              <span
                key={l.key}
                className="rounded-full bg-gray-100 px-2.5 py-1 text-gray-500"
                title={`${l.label}: ${docSet[i].size} unique docs from ${l.chunks.length} chunks`}
              >
                {short(l.label)}: {docSet[i].size} docs
              </span>
            ))}
          </div>
          <div className="max-h-96 overflow-auto rounded border border-gray-100">
            {/* table-fixed so the Document column can't be widened by a long
                title — it truncates in place and the lane columns stay aligned. */}
            <table className="w-full table-fixed border-collapse text-xs">
              <colgroup>
                <col />
                <col className="w-10" />
                {lanes.map((l) => (
                  <col key={l.key} className="w-20" />
                ))}
              </colgroup>
              <thead className="sticky top-0 bg-gray-50">
                <tr>
                  <th className="p-2 text-left font-medium text-gray-500">Document</th>
                  <th className="p-2 text-center font-medium text-gray-500" title="lanes that retrieved this doc (recall robustness)">
                    ×
                  </th>
                  {lanes.map((l) => (
                    <th key={l.key} className="truncate p-2 text-center font-medium text-gray-500" title={l.label}>
                      {short(l.label)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.doc} className="border-t border-gray-100">
                    <td className="truncate p-2 text-gray-700" title={r.title}>
                      {r.title}
                    </td>
                    <td className="p-2 text-center tabular-nums text-gray-400">{r.count}</td>
                    {r.ranks.map((rank, k) => (
                      <td key={k} className="p-1 text-center">
                        {rank === undefined ? (
                          <span className="text-gray-200">·</span>
                        ) : (
                          <span
                            className={`inline-flex items-baseline gap-0.5 rounded px-1.5 py-0.5 font-medium tabular-nums ${rankClass(rank)}`}
                            title={`rank ${rank} · ${r.covers[k]} chunk${r.covers[k] === 1 ? "" : "s"} retrieved from this doc`}
                          >
                            {rank}
                            {r.covers[k] && r.covers[k]! > 1 ? (
                              <span className="text-[9px] font-normal opacity-70">×{r.covers[k]}</span>
                            ) : null}
                          </span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] leading-snug text-gray-400">
            Cell = the doc's rank in that lane (1 = top); ×N = how many of the lane's chunks came from this
            doc (coverage — a finer chunker packs more chunks per doc, so it lists fewer unique docs). "In
            all {n}" means every lane surfaced the doc regardless of chunker — a recall signal, not
            precision. For whether the lanes used the same passages, see span overlap above.
          </p>
        </div>
      </div>
    </details>
  );
}

function Glossary() {
  return (
    <details className="mt-6 rounded-lg border border-gray-200 bg-white">
      <summary className="cursor-pointer list-none px-4 py-3">
        <span className="text-sm font-semibold text-gray-700">Glossary</span>
        <span className="ml-2 text-xs text-gray-400">what the terms on this page mean</span>
      </summary>
      <div className="grid gap-x-6 gap-y-5 border-t border-gray-100 p-4 sm:grid-cols-2 lg:grid-cols-3">
        {GLOSSARY.map((g) => (
          <div key={g.group}>
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
              {g.group}
            </div>
            <dl className="space-y-1.5">
              {g.items.map((i) => (
                <div key={i.term}>
                  <dt className="text-xs font-medium text-gray-700">{i.term}</dt>
                  <dd className="text-xs leading-snug text-gray-500">{i.def}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </details>
  );
}

export function CompareView({
  apiKey,
  setApiKey,
}: {
  apiKey: string;
  setApiKey: (v: string) => void;
}) {
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts: CollectionInfo[] = collections.data?.collections ?? [];

  // Registered llm/reranker models for the per-lane override pickers. When none
  // are registered the selects don't render, so Compare is unchanged.
  const availableModels = useQuery({
    queryKey: ["available-models", apiKey],
    queryFn: () => getAvailableModels(apiKey || undefined),
    retry: false,
  });
  const models: AvailableModel[] = availableModels.data?.models ?? [];

  const [query, setQuery] = useState("");
  // Global pipeline template shared by every lane. topK carries the global
  // default (a concrete number); lanes may leave their own topK null to inherit.
  const [glob, setGlob] = useState<Levers>({ ...DEFAULT_LEVERS, topK: GLOBAL_DEFAULT_TOPK });
  // When false (default) all lanes share `glob` — one setting, consistent
  // everywhere. When true, each lane's own `levers` take over and its controls
  // appear in the card.
  const [perLane, setPerLane] = useState(false);
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [results, setResults] = useState<Record<string, LaneResult>>({});
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [ran, setRan] = useState(false);

  const globalTopK = glob.topK ?? GLOBAL_DEFAULT_TOPK;

  // Seed one lane per collection once the registry loads.
  useEffect(() => {
    if (lanes.length === 0 && opts.length > 0) {
      setLanes(opts.slice(0, MAX_LANES).map((c) => newLane(c.default ? "" : c.id)));
    }
  }, [opts, lanes.length]);

  // Reconcile lanes when the registry changes (apiKey/tenant switch): a lane
  // pointing at a collection no longer offered would submit a phantom id (backend
  // 404) and render a <select> with no matching <option> — reset it to default.
  useEffect(() => {
    if (opts.length === 0) return;
    const valid = new Set(opts.map((c) => (c.default ? "" : c.id)));
    setLanes((ls) =>
      ls.every((l) => valid.has(l.collection))
        ? ls
        : ls.map((l) => (valid.has(l.collection) ? l : { ...l, collection: "" })),
    );
  }, [opts]);

  const collOf = (collection: string): CollectionInfo | undefined =>
    opts.find((o) => (o.default ? "" : o.id) === collection);
  const collLabel = (collection: string): string =>
    collOf(collection)?.label ?? (collection || "default");

  const run = () => {
    const q = query.trim();
    if (!q || lanes.length === 0) return;
    setRan(true);
    setResults(Object.fromEntries(lanes.map((l) => [l.key, { status: "pending" as const }])));
    for (const lane of lanes) {
      const t0 = performance.now();
      // Global mode → every lane runs the shared template; per-lane mode → the
      // lane's own levers, still inheriting the global top_k when blank.
      const eff = perLane ? lane.levers : glob;
      queryRag(
        {
          query: q,
          top_k: eff.topK ?? globalTopK,
          collection: lane.collection || undefined,
          retrieval_mode: eff.mode,
          rerank: rerankValue(eff.rerank),
          use_graph: eff.useGraph,
          rewrite_strategies: rewriteStrategies(eff.rewrite),
          llm: eff.llm || undefined,
          reranker: eff.reranker || undefined,
        },
        lane.apiKey || apiKey || undefined,
      )
        .then((data) =>
          setResults((r) => ({
            ...r,
            [lane.key]: { status: "success", data, ms: performance.now() - t0 },
          })),
        )
        .catch((e: Error) =>
          setResults((r) => ({
            ...r,
            [lane.key]: { status: "error", error: e.message, ms: performance.now() - t0 },
          })),
        );
    }
  };

  const setLane = (key: string, patch: Partial<Lane>) =>
    setLanes((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  // Clear a lane's prior answer + rating — a stored result is attributed to the
  // exact pipeline that produced it, so any lever change must invalidate it.
  const resetLane = (key: string) => {
    setResults((r) => {
      const n = { ...r };
      delete n[key];
      return n;
    });
    setRatings((r) => {
      const n = { ...r };
      delete n[key];
      return n;
    });
  };
  const tuneLane = (key: string, patch: Partial<Lane>) => {
    setLane(key, patch);
    resetLane(key);
  };
  // Per-lane lever edit (only reachable when overrides are on).
  const tuneLevers = (key: string, patch: Partial<Levers>) => {
    setLanes((ls) =>
      ls.map((l) => (l.key === key ? { ...l, levers: { ...l.levers, ...patch } } : l)),
    );
    resetLane(key);
  };
  const resetAll = () => {
    setResults({});
    setRatings({});
  };
  // A global lever change updates the template and mirrors into every lane, so
  // flipping overrides on later starts from the current global — and so the
  // global controls double as a "set all" even while overrides are on.
  const setGlobalLevers = (patch: Partial<Levers>) => {
    setGlob((g) => ({ ...g, ...patch }));
    setLanes((ls) => ls.map((l) => ({ ...l, levers: { ...l.levers, ...patch } })));
    resetAll();
  };
  const togglePerLane = (v: boolean) => {
    // Seed each lane from the current global on enabling, so overrides begin
    // consistent rather than from a stale per-lane state.
    if (v) setLanes((ls) => ls.map((l) => ({ ...l, levers: { ...glob } })));
    setPerLane(v);
    resetAll();
  };
  const removeLane = (key: string) => setLanes((ls) => ls.filter((l) => l.key !== key));
  const addLane = () =>
    setLanes((ls) => (ls.length < MAX_LANES ? [...ls, newLane("", "", glob)] : ls));

  // Leaderboard: lanes with a rating, best first.
  const ranked = lanes
    .filter((l) => (ratings[l.key] ?? 0) > 0)
    .sort((a, b) => (ratings[b.key] ?? 0) - (ratings[a.key] ?? 0));
  const topKey = ranked[0]?.key;

  // Successful lanes, labelled (collection + non-default levers), for the
  // agreement analysis below.
  const successEntries: LaneEntry[] = lanes
    .map((l) => ({ lane: l, res: results[l.key] }))
    .filter((x) => x.res?.status === "success" && x.res.data)
    .map((x) => ({
      key: x.lane.key,
      label:
        collLabel(x.lane.collection) +
        (perLane && leverTags(x.lane.levers).length
          ? ` · ${leverTags(x.lane.levers).join(" ")}`
          : ""),
      collection: x.lane.collection,
      answer: x.res!.data!.answer ?? "",
      sources: x.res!.data!.sources,
    }));

  return (
    <div>
      {/* Toolbar */}
      <div className="mb-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="Ask one question, compare across collections…"
            className="min-w-64 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={run}
            disabled={!query.trim() || lanes.length === 0}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:opacity-40"
          >
            Run {lanes.length}
          </button>
          <button
            type="button"
            onClick={addLane}
            disabled={lanes.length >= MAX_LANES}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40"
          >
            + Lane
          </button>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="API key (optional)"
            className="w-40 rounded-md border border-gray-300 px-2 py-1 text-xs"
          />
        </div>

        {/* Leaderboard */}
        {ranked.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-gray-500">Ranking:</span>
            {ranked.map((l, i) => (
              <span
                key={l.key}
                className={`rounded-full px-2 py-0.5 ${i === 0 ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}
              >
                {i === 0 ? "🥇 " : `${i + 1}. `}
                {collLabel(l.collection)}
                {perLane && leverTags(l.levers).length
                  ? ` · ${leverTags(l.levers).join(" ")}`
                  : ""}{" "}
                · {ratings[l.key]}★
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {/* Global pipeline panel + lanes */}
      <div className="flex gap-4">
        <aside className="sticky top-4 w-52 shrink-0 self-start space-y-3 rounded-lg border border-gray-200 bg-white p-3">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            Pipeline
          </div>
          <label className="flex cursor-pointer items-center justify-between gap-2">
            <span className="text-xs font-medium text-gray-600">Per-lane overrides</span>
            <input
              type="checkbox"
              checked={perLane}
              onChange={(e) => togglePerLane(e.target.checked)}
            />
          </label>
          <LeverControls
            value={glob}
            onChange={setGlobalLevers}
            topKPlaceholder="5"
            models={models}
          />
          <p className="text-[11px] leading-snug text-gray-400">
            {perLane
              ? "Each lane below can differ. Changing a control here applies it to every lane."
              : "All lanes share these settings."}
          </p>
        </aside>

        {/* Lanes */}
        <div className="flex gap-4 overflow-x-auto pb-4">
          {lanes.map((lane) => {
          const res = results[lane.key];
          const isTop = lane.key === topKey;
          return (
            <div
              key={lane.key}
              className={`flex w-80 shrink-0 flex-col rounded-lg border bg-white ${isTop ? "border-amber-300 ring-1 ring-amber-200" : "border-gray-200"}`}
            >
              {/* Lane header */}
              <div className="space-y-2 border-b border-gray-100 p-3">
                <div className="flex items-center gap-2">
                  <select
                    value={lane.collection}
                    onChange={(e) => tuneLane(lane.key, { collection: e.target.value })}
                    className="min-w-0 flex-1 rounded-md border border-gray-300 px-2 py-1 text-sm"
                  >
                    {opts.map((c) => (
                      <option key={c.id} value={c.default ? "" : c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    aria-label="remove lane"
                    onClick={() => removeLane(lane.key)}
                    className="shrink-0 text-gray-400 hover:text-red-500"
                  >
                    ✕
                  </button>
                </div>
                {(() => {
                  const c = collOf(lane.collection);
                  const p = c?.provenance;
                  const method = p?.chunk_method ?? c?.chunk_method;
                  return c ? (
                    <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
                      <span>{c.model.split("/").pop()} · {c.dim}d</span>
                      {method ? (
                        <span>· {method}{p?.chunk_size ? "/" + p.chunk_size : ""}</span>
                      ) : null}
                      {p ? (
                        <span className={p.source === "ingest" ? "text-green-600" : "text-gray-400"}>
                          · {p.source === "ingest" ? "verified" : "config"}
                        </span>
                      ) : null}
                    </div>
                  ) : null;
                })()}

                {/* Per-lane levers appear only when overrides are enabled;
                    otherwise every lane follows the global panel. */}
                {perLane ? (
                  <LeverControls
                    value={lane.levers}
                    onChange={(p) => tuneLevers(lane.key, p)}
                    topKPlaceholder={String(globalTopK)}
                    models={models}
                  />
                ) : null}

                <input
                  type="password"
                  value={lane.apiKey}
                  onChange={(e) => setLane(lane.key, { apiKey: e.target.value })}
                  placeholder="lane API key → compare a tenant (optional)"
                  className="w-full rounded-md border border-gray-200 px-2 py-1 text-xs"
                />
                <div className="flex items-center justify-between">
                  <Stars
                    value={ratings[lane.key] ?? 0}
                    onChange={(v) => setRatings((r) => ({ ...r, [lane.key]: v }))}
                  />
                  {res?.ms != null ? (
                    <span className="tabular-nums text-[11px] text-gray-400">
                      {res.ms.toFixed(0)} ms
                      {res.data?.sources?.length
                        ? ` · top ${res.data.sources[0].score.toFixed(4)}`
                        : ""}
                    </span>
                  ) : null}
                </div>
              </div>

              {/* Lane body */}
              <div className="flex-1 space-y-3 p-3">
                {!ran ? (
                  <p className="text-xs text-gray-400">Run a query to compare.</p>
                ) : res?.status === "pending" ? (
                  <p className="animate-pulse text-xs text-gray-400">querying…</p>
                ) : res?.status === "error" ? (
                  <p className="text-xs text-red-600">Error: {res.error}</p>
                ) : res?.data ? (
                  <>
                    <section
                      aria-label="answer"
                      className="rounded-md border border-gray-200 bg-gray-50 p-2"
                    >
                      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                        Answer
                      </div>
                      {/* content is untrusted → rendered as React text (auto-escaped). */}
                      <p className="whitespace-pre-wrap break-words text-sm text-gray-800">
                        {res.data.answer}
                      </p>
                    </section>

                    <section aria-label="sources">
                      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                        Sources ({res.data.sources.length})
                      </div>
                      {res.data.sources.length === 0 ? (
                        <p className="rounded bg-amber-50 p-2 text-xs text-amber-800">
                          No sources matched — the answer may be low-confidence.
                        </p>
                      ) : (
                        <>
                          <div
                            className={`${SOURCE_GRID} border-b border-gray-200 pb-1 text-[10px] font-medium uppercase tracking-wide text-gray-400`}
                          >
                            <span className="text-right">#</span>
                            <span>Document</span>
                            <span className="text-right">Score</span>
                          </div>
                          <ul>
                            {res.data.sources.map((s, i) => (
                              <CompareSource
                                key={s.chunk_id}
                                rank={i + 1}
                                source={s}
                                collection={lane.collection}
                                apiKey={lane.apiKey || apiKey}
                              />
                            ))}
                          </ul>
                        </>
                      )}
                    </section>
                  </>
                ) : null}
              </div>
            </div>
          );
        })}

          {lanes.length === 0 ? (
            <p className="text-sm text-gray-400">
              No collections available. Configure the registry to compare.
            </p>
          ) : null}
        </div>
      </div>

      {/* Agreement analysis across the lanes' results. */}
      {ran && successEntries.length >= 2 ? <AgreementPanel entries={successEntries} /> : null}

      {/* Glossary of the levers + metrics used on this page. */}
      <Glossary />
    </div>
  );
}
