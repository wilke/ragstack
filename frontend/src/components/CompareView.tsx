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
import { getStoredAuthMode } from "../api/config";
import { laneCredential, SIGNED_IN_HINT, type CredentialInput } from "../lib/auth";
import {
  collectionTarget,
  requestCollection,
  targetInfo,
  type CollectionTarget,
} from "../lib/collectionTarget";
import {
  rerankValue,
  rewriteStrategies,
  type Mode,
  type Rerank,
  type Rewrite,
} from "../lib/queryOptions";
import { GlossaryPanel } from "./GlossaryPanel";
import { HelpTip } from "./HelpTip";
import { AgreementBadge } from "./compare/AgreementBadge";
import { AgreementBand, type AgreementBandStats } from "./compare/AgreementBand";
import { LaneConfigChips } from "./compare/LaneConfigChips";
import { LeverPopover } from "./compare/LeverPopover";
import {
  DEFAULT_LEVERS,
  defaultsChips,
  effectiveLevers,
  normalizeOverrides,
  overrideChips,
  type LeverOverrides,
  type Levers,
} from "./compare/levers";

// Compare module: run ONE query across several lanes — each a (collection,
// optional API key) pair — and lay the answers out side by side so retrieval
// strategies (chunkers, embedding models) or tenants can be A/B'd and ranked.
// The collection axis is server-supported via the `collection` param; the tenant
// axis is a per-lane API-key override (tenant is server-derived from the key).
// Backend needs nothing new — it's N independent /v1/query calls.
//
// Levers are held once as shared defaults; a lane carries only a sparse
// override set (components/compare/levers.ts), surfaced as yellow chips.

type LaneResult = {
  status: "pending" | "success" | "error";
  data?: QueryResponse;
  error?: string;
  ms?: number;
};

interface Lane {
  key: string;
  // The explicit collection this lane queries, or null for "whatever the
  // listing reports as MY default". Not "" — that sentinel is what let a lane's
  // header name one collection while the request hit another (#420).
  collection: string | null;
  apiKey: string; // "" → inherit the shared key (same tenant)
  overrides: LeverOverrides; // levers this lane pins; everything else inherits
}

let _seq = 0;
export const newLane = (collection: string | null = null): Lane => ({
  key: `lane-${_seq++}`,
  collection,
  apiKey: "",
  overrides: {},
});

const MAX_LANES = 6;

/**
 * The collections Compare opens with: one lane per listed collection, capped at
 * MAX_LANES, by REAL id. Exported and pure so the seeding can be tested without
 * a DOM — `renderToStaticMarkup` runs no effects, so the seed never fires in a
 * static render and the composition seed → collectionTarget → request/label has
 * to be asserted directly (#420).
 */
export function seedLaneCollections(opts: CollectionInfo[]): string[] {
  return opts.slice(0, MAX_LANES).map((c) => c.id);
}
const GLOBAL_DEFAULT_TOPK = 5;

// The glossary sits at the foot of the page while the agreement band's "What do
// these mean?" toggles it from the top — a fixed id so that remote trigger can
// name what it expands.
const GLOSSARY_REGION_ID = "compare-glossary";

// The groups this page's own vocabulary comes from. Without it the panel falls
// through to all 13 — including sharing and operations terms Compare never says.
const GLOSSARY_GROUPS = [
  "Retrieval mode",
  "Query rewriting",
  "Reranking",
  "Lane levers",
  "Model overrides",
  "Fusion & scoring",
  "Agreement metrics",
  "Chunking",
];

// Lane letters + badge colors: A navy/yellow, B blue/white, C green/white, then
// the remaining brand hues for lanes 4–6.
const LETTERS = "ABCDEF";
const laneLetter = (i: number): string => LETTERS[i] ?? String(i + 1);
const LANE_BADGE = [
  "bg-ink-900 text-accent",
  "bg-link text-white",
  "bg-moss text-white",
  "bg-rust text-white",
  "bg-sky text-white",
  "bg-ink-600 text-accent",
];

// One control cluster for the levers — inside the "Edit defaults" popover and,
// per lane, inside the "edit ▾" popover. ``topKPlaceholder`` shows the
// inherited default when the field is left blank.
//
// Each lever label is a <HelpTip/>, not a native title="": every one of them
// resolves through lib/glossary, so this panel and Explore's Options menu cannot
// describe the same lever differently. The <label> wrappers are gone because a
// HelpTip is a button and must not nest inside one — the controls carry
// aria-label instead.
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
  const sel = "min-w-0 flex-1 rounded border border-line bg-white px-1 py-0.5 text-[11px] text-body";
  const llmModels = models.filter((m) => m.task === "llm");
  const rerankerModels = models.filter((m) => m.task === "reranker");
  return (
    <div className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-[11px] text-dim">
      <div className="flex items-center gap-1">
        <HelpTip term="retrieval mode" label="mode" className="w-9 shrink-0" />
        <select
          aria-label="retrieval mode"
          value={value.mode}
          onChange={(e) => onChange({ mode: e.target.value as Mode })}
          className={sel}
        >
          <option value="hybrid">hybrid</option>
          <option value="vector">vector</option>
          <option value="bm25">bm25</option>
        </select>
      </div>
      <div className="flex items-center gap-1">
        <HelpTip term="query rewriting" label="rewrite" className="w-12 shrink-0" />
        <select
          aria-label="query rewriting"
          value={value.rewrite}
          onChange={(e) => onChange({ rewrite: e.target.value as Rewrite })}
          className={sel}
        >
          <option value="none">none</option>
          <option value="multiquery">multiquery</option>
          <option value="hyde">hyde</option>
        </select>
      </div>
      <div className="flex items-center gap-1">
        <HelpTip term="rerank" label="rerank" className="w-9 shrink-0" />
        <select
          aria-label="reranking"
          value={value.rerank}
          onChange={(e) => onChange({ rerank: e.target.value as Rerank })}
          className={sel}
        >
          <option value="default">default</option>
          <option value="on">on</option>
          <option value="off">off</option>
        </select>
      </div>
      <div className="flex items-center gap-1">
        <HelpTip term="top_k" className="w-12 shrink-0" />
        <input
          type="number"
          aria-label="top_k"
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
      </div>
      <div className="col-span-2 flex items-center gap-1.5">
        <input
          type="checkbox"
          aria-label="use knowledge graph"
          checked={value.useGraph}
          onChange={(e) => onChange({ useGraph: e.target.checked })}
        />
        <HelpTip term="knowledge graph" label="use knowledge graph" />
      </div>
      {llmModels.length > 0 ? (
        <div className="col-span-2 flex items-center gap-1">
          <HelpTip term="llm" className="w-9 shrink-0" />
          <select
            aria-label="answer model"
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
        </div>
      ) : null}
      {rerankerModels.length > 0 ? (
        <div className="col-span-2 flex items-center gap-1">
          <HelpTip term="rr·model" className="w-9 shrink-0" />
          <select
            aria-label="reranker model"
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
        </div>
      ) : null}
    </div>
  );
}

// A neighbouring chunk (prev/next), rendered above/below the matched passage.
function ContextChunk({ chunk, position }: { chunk: ChunkOut; position: "prev" | "next" }) {
  const idx =
    typeof chunk.metadata.chunk_index === "number" ? chunk.metadata.chunk_index : undefined;
  return (
    <div className="my-1 border-l-2 border-line bg-paper py-1 pl-2">
      <div className="font-mono text-[10px] font-medium uppercase tracking-wide text-faint">
        {position === "prev" ? "◀ previous chunk" : "next chunk ▶"}
        {idx !== undefined ? ` · #${idx}` : ""}
      </div>
      <p className="whitespace-pre-wrap text-[11px] leading-snug text-dim">{chunk.content}</p>
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

// Left rule colour by rank: 1 navy, 2–3 sky, the tail neutral. No grey
// below-threshold rule: the API exposes no score threshold, and inventing one
// client-side would grade results the backend didn't (same decision as
// SourceCard's missing off-topic chip).
const rankRule = (rank: number): string =>
  rank === 1 ? "border-l-ink-900" : rank <= 3 ? "border-l-sky" : "border-l-[#d8d7d2]";

function CompareSource({
  rank,
  source,
  collection,
  apiKey,
  letters,
  laneCount,
}: {
  rank: number;
  source: Source;
  // The id the lane's query actually carried; null when it omitted the field.
  collection: string | null;
  // The LANE's credential, already resolved by laneCredential — a lane's own
  // key arrives pinned {mode:"apikey"} so a bearer-mode app can't relabel it
  // (and sendableCredential then drop it), which would 401 the context fetch.
  apiKey: CredentialInput;
  // Which lanes (by letter) retrieved this doc — null until ≥2 lanes answered.
  letters: string[] | null;
  laneCount: number;
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
    <li className={`rounded-[4px] border border-line border-l-[3px] px-[11px] py-2.5 ${rankRule(rank)}`}>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-[10px] font-medium text-faint">{rank}</span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="min-w-0 flex-1 text-left text-[12px] font-medium leading-[1.35] text-strong"
          title={title}
        >
          {title}
        </button>
      </div>
      <div className="mt-[7px] flex items-center gap-1.5">
        {letters ? <AgreementBadge letters={letters} laneCount={laneCount} /> : null}
        <span className="ml-auto font-mono text-[9.5px] text-faint" title={`score ${source.score.toFixed(4)}`}>
          {source.score.toFixed(2)}
        </span>
      </div>

      {open ? (
        <div className="mt-2 min-w-0">
          {ctx && !ctx.loading && !ctx.error && ctx.prev ? (
            <ContextChunk chunk={ctx.prev} position="prev" />
          ) : null}

          <p
            onClick={() => setOpen(false)}
            title={source.content}
            className="cursor-pointer whitespace-pre-wrap break-words text-xs text-dim"
          >
            {source.content}
          </p>

          {ctx && !ctx.loading && !ctx.error && ctx.next ? (
            <ContextChunk chunk={ctx.next} position="next" />
          ) : null}

          <div className="mt-1 flex flex-wrap items-center gap-x-2 font-mono text-[10px] text-faint">
            <button type="button" onClick={() => setOpen(false)} className="hover:text-body">
              ▴ collapse
            </button>
            {idx !== undefined ? <span>· chunk #{idx}</span> : null}
            {hasNbr ? (
              <button
                type="button"
                onClick={loadContext}
                disabled={ctx?.loading}
                className="text-link hover:underline disabled:opacity-50"
              >
                {ctx?.loading
                  ? "· loading…"
                  : ctx && !ctx.error
                    ? "· hide context"
                    : "· ± parent/child"}
              </button>
            ) : (
              <span className="text-faint/70">· no neighbours</span>
            )}
          </div>
          {ctx?.error ? <p className="text-[10px] text-rust">context: {ctx.error}</p> : null}
        </div>
      ) : null}
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
  // Which collection the lane actually queried — same value ⇒ same chunker. The
  // RESOLVED id, so a lane that named its default and one that left it unpicked
  // compare as the same corpus, which they are.
  collection: string | null;
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

// null (not 0) when both sides are empty: two lanes that each returned nothing
// have no disagreement to report, and 0 would render as "total disagreement".
// Callers already guard null (the band hides the figure).
function jaccard<T>(a: Set<T>, b: Set<T>): number | null {
  if (!a.size && !b.size) return null;
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
//
// CONDITIONAL on those shared docs: docs unique to one lane are not in the
// denominator, so lanes sharing a single doc whose spans coincide score 1.0
// however little else they agree on. Renderers must show the base (how many
// docs it was computed over) rather than presenting it as whole-result
// agreement — see `spanBasis` in the agreement band.
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

// n/a, not 0%: a null overlap means there was nothing to compare (both lanes
// empty), which is not the same claim as "these results disagree entirely".
const pct = (x: number | null) => (x === null ? "n/a" : `${Math.round(x * 100)}%`);

// Mean pairwise overlap for the band's headline number: chunk-level Jaccard when
// every lane shares a collection (same chunker → same units), doc-level Jaccard
// otherwise. Same comparability rule as AgreementPanel's matrix.
function meanPairwiseOverlap(entries: LaneEntry[]): number | null {
  const n = entries.length;
  if (n < 2) return null;
  const sameCollection = entries.every((e) => e.collection === entries[0].collection);
  const sets = entries.map((e) =>
    sameCollection
      ? new Set(e.sources.map((s) => s.chunk_id))
      : new Set(e.sources.map((s) => s.doc_id)),
  );
  let sum = 0;
  let pairs = 0;
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++) {
      // Pairs with nothing to compare (both lanes empty) are skipped rather
      // than averaged in as 0, which would drag the mean toward "disagree".
      const j2 = jaccard(sets[i], sets[j]);
      if (j2 === null) continue;
      sum += j2;
      pairs++;
    }
  return pairs ? sum / pairs : null;
}

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
    <details className="mt-6 rounded-card border border-line bg-white">
      <summary className="cursor-pointer list-none px-4 py-3">
        <span className="font-display text-[13px] font-semibold text-ink-900">Agreement detail</span>
        <span className="ml-2 text-xs text-dim">
          pairwise matrices and the per-document recall table behind the band above
        </span>
      </summary>

      <div className="space-y-5 border-t border-lineSoft p-4">
        {/* Comparability banner — what unit the agreement can honestly use */}
        <div
          className={`rounded-md px-3 py-2 text-xs leading-snug ${allChunkLevel ? "bg-mossSoft text-moss" : "bg-accent-soft text-accent-text"}`}
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
                <col className="w-14" />
                {lanes.map((l) => (
                  <col key={l.key} className="w-20" />
                ))}
              </colgroup>
              <thead className="sticky top-0 bg-gray-50">
                <tr>
                  <th className="p-2 text-left font-medium text-gray-500">Document</th>
                  {/* Was a native title="": keyboard and touch never reached it,
                      and this column's whole meaning lived there. */}
                  <th className="p-2 text-center font-medium text-gray-500">
                    <span className="inline-flex items-center gap-1">
                      ×
                      <HelpTip icon side="bottom" term="consensus (×) / coverage (×N)" />
                    </span>
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

// A lane's generated answer: one line until clicked open.
function LaneAnswer({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <button
      type="button"
      onClick={() => setOpen((o) => !o)}
      title={open ? "collapse" : text}
      className={`mb-4 block w-full text-left text-[13px] leading-[1.65] text-body ${
        open ? "whitespace-pre-wrap break-words" : "truncate"
      }`}
    >
      {text}
    </button>
  );
}

export function CompareView({
  apiKey,
  setApiKey,
  seedQuery = null,
}: {
  apiKey: string;
  setApiKey: (v: string) => void;
  // Query text carried over by Explore/Evidence's "Send to Compare". Applied
  // whenever it changes; the view remounts on tab switch, so mount covers the
  // usual path. Editable afterwards — a seed, not a binding.
  seedQuery?: string | null;
}) {
  const collections = useQuery({
    queryKey: ["collections", apiKey],
    queryFn: () => getCollections(apiKey || undefined),
    retry: false,
  });
  const opts: CollectionInfo[] = collections.data?.collections ?? [];

  // Registered llm/reranker models for the lever pickers. When none are
  // registered the selects don't render, so Compare is unchanged.
  const availableModels = useQuery({
    queryKey: ["available-models", apiKey],
    queryFn: () => getAvailableModels(apiKey || undefined),
    retry: false,
  });
  const models: AvailableModel[] = availableModels.data?.models ?? [];

  const [query, setQuery] = useState("");
  useEffect(() => {
    if (seedQuery) setQuery(seedQuery);
  }, [seedQuery]);
  // Shared defaults for every lane. topK carries the global default (a concrete
  // number); a lane override may pin its own.
  const [glob, setGlob] = useState<Levers>({ ...DEFAULT_LEVERS, topK: GLOBAL_DEFAULT_TOPK });
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [results, setResults] = useState<Record<string, LaneResult>>({});
  // ONE preferred lane across the board (replaces per-lane star ratings) —
  // in-session only, like the ratings were.
  const [preferred, setPreferred] = useState<string | null>(null);
  const [ran, setRan] = useState(false);
  const [glossaryOpen, setGlossaryOpen] = useState(false);

  const globalTopK = glob.topK ?? GLOBAL_DEFAULT_TOPK;

  // Seed one lane per collection once the registry loads — real ids, so each
  // lane names exactly what it queries from the first render.
  useEffect(() => {
    if (lanes.length === 0 && opts.length > 0) {
      setLanes(seedLaneCollections(opts).map((id) => newLane(id)));
    }
  }, [opts, lanes.length]);

  // Reconcile lanes when the registry changes (apiKey/tenant switch): a lane
  // pointing at a collection no longer offered would render a <select> with no
  // matching <option>. Reset it to null ("use my default"), never to "".
  // `collectionTarget` already refuses to send a stale id, so this is display
  // hygiene rather than the guard against a phantom request.
  useEffect(() => {
    if (opts.length === 0) return;
    const ok = (c: string | null) => c === null || opts.some((o) => o.id === c);
    setLanes((ls) =>
      ls.every((l) => ok(l.collection))
        ? ls
        : ls.map((l) => (ok(l.collection) ? l : { ...l, collection: null })),
    );
  }, [opts]);

  // One resolution per lane, shared by its request, its header label, its id
  // readout and the chunk fetches under its sources — so a lane cannot describe
  // itself as anything but what it queried (#420).
  const laneTarget = (lane: Lane): CollectionTarget =>
    collectionTarget(collections.data, lane.collection);

  const run = () => {
    const q = query.trim();
    if (!q || lanes.length === 0) return;
    setRan(true);
    setPreferred(null);
    setResults(Object.fromEntries(lanes.map((l) => [l.key, { status: "pending" as const }])));
    for (const lane of lanes) {
      const t0 = performance.now();
      // A lane's effective config = shared defaults + its sparse overrides.
      const eff = effectiveLevers(glob, lane.overrides);
      queryRag(
        {
          query: q,
          top_k: eff.topK ?? globalTopK,
          collection: requestCollection(laneTarget(lane)),
          retrieval_mode: eff.mode,
          rerank: rerankValue(eff.rerank),
          use_graph: eff.useGraph,
          rewrite_strategies: rewriteStrategies(eff.rewrite),
          llm: eff.llm || undefined,
          reranker: eff.reranker || undefined,
        },
        // Exactly ONE credential. A lane key is pinned to X-API-Key even when
        // the app is signed in with a bearer token; a lane without its own key
        // passes the app credential through as the opaque string, so client.ts
        // still checks it against storage before it becomes a header.
        laneCredential(lane.apiKey, apiKey),
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
  // Clear a lane's prior answer + preference — a stored result is attributed to
  // the exact pipeline that produced it, so any lever change must invalidate it.
  const resetLane = (key: string) => {
    setResults((r) => {
      const n = { ...r };
      delete n[key];
      return n;
    });
    setPreferred((p) => (p === key ? null : p));
  };
  const tuneLane = (key: string, patch: Partial<Lane>) => {
    setLane(key, patch);
    resetLane(key);
  };
  // Per-lane lever edit: writes into the override set, dropping entries that
  // land back on the shared default so inheritance is restored automatically.
  const tuneOverrides = (key: string, patch: Partial<Levers>) => {
    setLanes((ls) =>
      ls.map((l) =>
        l.key === key
          ? { ...l, overrides: normalizeOverrides(glob, { ...l.overrides, ...patch }) }
          : l,
      ),
    );
    resetLane(key);
  };
  const clearOverrides = (key: string) => {
    setLanes((ls) => ls.map((l) => (l.key === key ? { ...l, overrides: {} } : l)));
    resetLane(key);
  };
  const resetAll = () => {
    setResults({});
    setPreferred(null);
  };
  // Editing a shared default invalidates every stored result. Overrides win
  // via spread order, so lane pins survive a defaults edit.
  const setGlobalLevers = (patch: Partial<Levers>) => {
    setGlob((g) => ({ ...g, ...patch }));
    resetAll();
  };
  const removeLane = (key: string) => setLanes((ls) => ls.filter((l) => l.key !== key));
  const addLane = () => setLanes((ls) => (ls.length < MAX_LANES ? [...ls, newLane()] : ls));

  // Successful lanes with their letters — feeds the band, the badges, and the
  // detail panel.
  const succ = lanes
    .map((l, i) => ({ lane: l, letter: laneLetter(i), res: results[l.key] }))
    .filter((x) => x.res?.status === "success" && x.res!.data);
  const succCount = succ.length;

  const successEntries: LaneEntry[] = succ.map((x) => {
    const chips = overrideChips(glob, x.lane.overrides);
    return {
      key: x.lane.key,
      label:
        `${x.letter} · ${laneTarget(x.lane).label}` +
        (chips.length ? ` · ${chips.join(" ")}` : ""),
      collection: laneTarget(x.lane).id,
      answer: x.res!.data!.answer ?? "",
      sources: x.res!.data!.sources,
    };
  });

  // doc_id → letters of the lanes that retrieved it (unique docs per lane).
  const docLanes = new Map<string, string[]>();
  for (const x of succ) {
    for (const id of new Set(x.res!.data!.sources.map((s) => s.doc_id))) {
      docLanes.set(id, [...(docLanes.get(id) ?? []), x.letter]);
    }
  }

  // Band stats — doc_id membership counts + mean pairwise overlap + timing.
  let band: AgreementBandStats | null = null;
  if (succCount >= 2) {
    let full = 0;
    let partial = 0;
    let unique = 0;
    docLanes.forEach((letters) => {
      if (letters.length === succCount) full++;
      else if (letters.length > 1) partial++;
      else unique++;
    });
    const overlap = meanPairwiseOverlap(successEntries);
    const fastestLane = succ.reduce((a, b) => ((a.res!.ms ?? Infinity) <= (b.res!.ms ?? Infinity) ? a : b));
    band = {
      full,
      partial,
      unique,
      total: docLanes.size,
      laneCount: succCount,
      overlapPct: overlap === null ? null : Math.round(overlap * 100),
      uniques: succ
        .map((x) => ({
          letter: x.letter,
          count: [...new Set(x.res!.data!.sources.map((s) => s.doc_id))].filter(
            (id) => (docLanes.get(id) ?? []).length === 1,
          ).length,
        }))
        .filter((u) => u.count > 0),
      fastest: fastestLane.res!.ms != null ? { letter: fastestLane.letter, ms: fastestLane.res!.ms! } : null,
    };
  }

  return (
    <div>
      {/* Query row + shared defaults (main's px-[34px] IS the band inset) */}
      <div className="pb-5">
        <div className="mb-3 flex flex-wrap items-center gap-2.5">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="Ask one question, compare across collections…"
            className="h-[46px] min-w-64 flex-1 rounded-pill border-[1.5px] border-ink-900 px-5 text-[15px] text-strong placeholder:text-faint"
          />
          <button
            type="button"
            onClick={run}
            disabled={!query.trim() || lanes.length === 0}
            className="flex h-[46px] items-center gap-2 rounded-pill bg-accent px-[22px] text-[13.5px] font-semibold text-ink-900 disabled:opacity-40"
          >
            Run {lanes.length} lane{lanes.length === 1 ? "" : "s"} <span className="text-[15px]">→</span>
          </button>
          <button
            type="button"
            onClick={addLane}
            disabled={lanes.length >= MAX_LANES}
            className="h-[46px] rounded-pill border border-ink-900 px-[18px] text-[13px] font-medium text-ink-900 disabled:opacity-40"
          >
            + Lane
          </button>
          {/* "Lane" is this screen's central noun and four later tips presume it
              ("Applies to every lane", "only C") — so it is defined here, before
              them, rather than only inside the collapsed glossary. */}
          <HelpTip icon side="bottom" term="lane" />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <HelpTip
            label="Applies to every lane unless overridden"
            side="bottom"
            className="font-mono text-[11px]"
          >
            These levers are sent with every lane&rsquo;s query, so a lane differs
            only where it pins its own value — shown as a yellow chip on that lane
            (a lane with its own API key gets one too). Editing a default here
            clears every lane&rsquo;s stored answer but leaves the pins;
            &ldquo;Use defaults&rdquo; in a lane&rsquo;s edit popover drops them.
          </HelpTip>
          <div className="flex flex-wrap gap-1.5 font-mono text-[10.5px]">
            {defaultsChips(glob).map((c) => (
              <span key={c} className="rounded-[10px] bg-[#f2f1ed] px-[11px] py-1.5 text-[#6a6a64]">
                {c}
              </span>
            ))}
          </div>
          <LeverPopover
            label="Edit defaults"
            buttonClassName="text-[11.5px] font-medium text-link"
          >
            <div className="space-y-2">
              <LeverControls
                value={glob}
                onChange={setGlobalLevers}
                topKPlaceholder={String(GLOBAL_DEFAULT_TOPK)}
                models={models}
              />
              <p className="border-t border-lineSoft pt-2 text-[10px] leading-snug text-dim">
                Changing a default re-arms every lane; lane overrides (yellow chips) keep their pins.
              </p>
            </div>
          </LeverPopover>
          {getStoredAuthMode() === "bearer" ? (
            <span className="ml-auto text-[11px] text-dim">{SIGNED_IN_HINT}</span>
          ) : (
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="API key (optional)"
              className="ml-auto w-40 rounded border border-line px-2 py-1 text-xs"
            />
          )}
        </div>
      </div>

      {/* Agreement band — doc_id + rank based; never cross-lane score comparison. */}
      {band ? (
        <AgreementBand
          stats={band}
          glossaryOpen={glossaryOpen}
          // The panel this opens is at the foot of the page, so opening it from
          // up here also has to take the reader there.
          onToggleGlossary={() => {
            const next = !glossaryOpen;
            setGlossaryOpen(next);
            if (next) {
              requestAnimationFrame(() => document.getElementById(GLOSSARY_REGION_ID)?.focus());
            }
          }}
          glossaryRegionId={GLOSSARY_REGION_ID}
        />
      ) : null}

      {/* Lanes: equal columns with hairline gaps; below ~900px the min column
          width makes the strip horizontally scrollable instead of crushing. */}
      <div className={`-mx-[34px] overflow-x-auto border-b border-line ${band ? "" : "border-t"}`}>
        {lanes.length === 0 ? (
          <p className="px-[34px] py-8 text-sm text-dim">
            No collections available. Configure the registry to compare.
          </p>
        ) : (
          <div
            className="grid min-h-[420px] gap-px bg-line"
            style={{ gridTemplateColumns: `repeat(${lanes.length}, minmax(340px, 1fr))` }}
          >
            {lanes.map((lane, i) => {
              const res = results[lane.key];
              const chips = overrideChips(glob, lane.overrides).concat(
                lane.apiKey.trim() ? ["own key"] : [],
              );
              const isPreferred = preferred === lane.key;
              const target = laneTarget(lane);
              const c = targetInfo(collections.data, target);
              const p = c?.provenance;
              const method = p?.chunk_method ?? c?.chunk_method;
              return (
                <div key={lane.key} className="bg-white px-5 pb-6 pt-[18px]">
                  {/* Lane header: letter badge · collection picker · remove */}
                  <div className="mb-1.5 flex items-center gap-2">
                    <span
                      className={`rounded-[3px] px-2 py-[5px] font-mono text-[11px] font-semibold ${LANE_BADGE[i % LANE_BADGE.length]}`}
                    >
                      {laneLetter(i)}
                    </span>
                    <select
                      value={target.id ?? ""}
                      onChange={(e) => tuneLane(lane.key, { collection: e.target.value })}
                      className="min-w-0 flex-1 cursor-pointer appearance-none truncate border-none bg-transparent p-0 font-display text-[13.5px] font-semibold leading-[1.3] text-ink-900"
                    >
                      {opts.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      aria-label="remove lane"
                      onClick={() => removeLane(lane.key)}
                      className="shrink-0 text-[13px] text-faint hover:text-rust"
                    >
                      ✕
                    </button>
                  </div>

                  {/* Collection facts: id, then model · dims · chunking */}
                  <div className="mb-2.5 font-mono text-[10.5px] leading-[1.6] text-dim">
                    {/* The id this lane really queries — never a guess. "—" when
                        the listing hasn't answered, rather than a plausible
                        wrong name. */}
                    <div className="truncate" title={target.id ?? undefined}>
                      {target.id ?? "—"}
                    </div>
                    {c ? (
                      <div className="truncate">
                        {c.model.split("/").pop()} · {c.dim}d
                        {method ? ` · ${method}${p?.chunk_size ? "/" + p.chunk_size : ""}` : ""}
                        {p ? (p.source === "ingest" ? " · verified" : " · config") : ""}
                      </div>
                    ) : null}
                  </div>

                  {/* Config chips + the per-lane edit popover */}
                  <LaneConfigChips chips={chips}>
                    <div className="space-y-2">
                      <LeverControls
                        value={effectiveLevers(glob, lane.overrides)}
                        onChange={(patch) => tuneOverrides(lane.key, patch)}
                        topKPlaceholder={String(globalTopK)}
                        models={models}
                      />
                      <input
                        type="password"
                        value={lane.apiKey}
                        onChange={(e) => tuneLane(lane.key, { apiKey: e.target.value })}
                        placeholder="lane API key → compare another owner scope (optional)"
                        className="w-full rounded border border-line px-2 py-1 text-xs"
                      />
                      <p className="text-[10px] leading-snug text-dim">
                        Sent as X-API-Key even when the app is signed in with a
                        token, so this lane really queries that key's{" "}
                        <HelpTip term="owner scope" />. The other lanes keep the
                        app's credential.
                      </p>
                      <div className="flex items-center justify-between border-t border-lineSoft pt-2">
                        <span className="text-[10px] text-dim">overrides replace the shared defaults</span>
                        <button
                          type="button"
                          onClick={() => clearOverrides(lane.key)}
                          disabled={Object.keys(lane.overrides).length === 0}
                          className="text-xs text-dim hover:text-body disabled:opacity-40"
                        >
                          Use defaults
                        </button>
                      </div>
                    </div>
                  </LaneConfigChips>

                  {/* Result meta + the ONE Prefer radio across lanes */}
                  {res?.status === "success" && res.data ? (
                    <div
                      className={`mb-3.5 flex items-center gap-2 rounded-row px-[11px] py-[9px] ${
                        isPreferred ? "border border-accent bg-accent-soft" : "bg-paper"
                      }`}
                    >
                      <span
                        className={`flex-1 font-mono text-[10.5px] ${isPreferred ? "text-accent-text" : "text-[#6a6a64]"}`}
                      >
                        {res.ms != null ? `${(res.ms / 1000).toFixed(2)}s · ` : ""}
                        {res.data.sources.length} source{res.data.sources.length === 1 ? "" : "s"}
                      </span>
                      <HelpTip icon side="bottom" term="lane result" />
                      <button
                        type="button"
                        role="radio"
                        aria-checked={isPreferred}
                        onClick={() => setPreferred(lane.key)}
                        className="flex items-center gap-2 text-[10.5px] font-medium text-ink-900"
                      >
                        {isPreferred ? "Preferred" : "Prefer"}
                        <span
                          aria-hidden="true"
                          className={`h-3.5 w-3.5 rounded-full border-[1.5px] ${
                            isPreferred
                              ? "border-ink-900 bg-ink-900 shadow-[inset_0_0_0_3px_#fff]"
                              : "border-faint"
                          }`}
                        />
                      </button>
                    </div>
                  ) : null}

                  {/* Lane body */}
                  {!ran ? (
                    <p className="text-xs text-dim">Run a query to compare.</p>
                  ) : res?.status === "pending" ? (
                    <p className="animate-pulse text-xs text-dim">querying…</p>
                  ) : res?.status === "error" ? (
                    <p className="text-xs text-rust">Error: {res.error}</p>
                  ) : res?.data ? (
                    <>
                      {res.data.answer?.trim() ? (
                        <LaneAnswer text={res.data.answer} />
                      ) : (
                        <p className="mb-4 text-[13px] italic text-faint">no answer generated</p>
                      )}

                      {/* One tip per lane column, on the section heading — the
                          row anatomy (badge, score) is explained once, not per
                          source row. */}
                      <div className="mb-2.5">
                        <HelpTip
                          label="Ranked sources"
                          className="font-mono text-[10px] font-medium uppercase tracking-[.12em]"
                        >
                          In the order this lane returned them. The badge names the
                          lanes that retrieved the same document — &ldquo;all
                          lanes&rdquo;, &ldquo;A · B&rdquo;, or &ldquo;only
                          C&rdquo; — matched on doc_id, so it means the same
                          document, not the same chunk; it appears once two lanes
                          have answered. The number on the right is this lane&rsquo;s
                          retrieval score, comparable inside this lane only.
                        </HelpTip>
                      </div>
                      {res.data.sources.length === 0 ? (
                        <p className="rounded-row bg-accent-soft p-2 text-xs text-accent-text">
                          No sources matched — the answer may be low-confidence.
                        </p>
                      ) : (
                        <ul className="flex flex-col gap-2">
                          {res.data.sources.map((s, k) => (
                            <CompareSource
                              key={s.chunk_id}
                              rank={k + 1}
                              source={s}
                              collection={target.id}
                              apiKey={laneCredential(lane.apiKey, apiKey)}
                              letters={succCount >= 2 ? (docLanes.get(s.doc_id) ?? null) : null}
                              laneCount={succCount}
                            />
                          ))}
                        </ul>
                      )}
                    </>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Deep-dive agreement matrices behind the band's headline numbers. */}
      {ran && successEntries.length >= 2 ? (
        <AgreementPanel entries={successEntries} />
      ) : null}

      {/* Glossary of the levers + metrics used on this page (lib/glossary via
          the shared panel). The same `open` bit is toggled by the agreement
          band's "What do these mean? ▾"; the page bleeds the row edge-to-edge
          out of its 34px gutter. */}
      <GlossaryPanel
        open={glossaryOpen}
        onToggle={() => setGlossaryOpen((o) => !o)}
        regionId={GLOSSARY_REGION_ID}
        groups={GLOSSARY_GROUPS}
        className="-mx-[34px]"
        inset="px-[34px]"
        summary="hybrid · vector · bm25 · rewrite · rerank · cross-encoder · knowledge graph"
      />
    </div>
  );
}
