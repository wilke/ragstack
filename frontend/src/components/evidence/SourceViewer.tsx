// The selected source in situ: provenance line, title/authors, the chunk with
// the matched passage highlighted, neighbour context, citation actions, score.
//
// Two ways "in situ" is honoured:
// - WALKING: prev/next_chunk_id (when the ingester stamped them) are fetchable
//   ids, so the header row pages through the document one chunk at a time.
//   Visited chunks are cached in state so back/forward is instant; "back to
//   match" restores the retrieved chunk. This walk is WITHIN one source's
//   document — the between-sources pager above the card is separate.
// - LEXICAL MARKS: the API emits no chunk-relative match offsets yet (handoff
//   "Backend gaps"), so the WHOLE retrieved passage keeps the yellow wash +
//   4px spread frame from the mockup, and lib/answerMatch marks the sentences
//   inside the displayed chunk that lexically overlap the answer — a
//   client-side approximation, captioned as such, never presented as
//   model-attributed grounding. The retrieval score belongs to the matched
//   chunk only; walked neighbours render without it.
//
// All fields are untrusted ingested text → React text nodes only. Highlights
// SLICE the string into text/<mark> nodes (each auto-escaped), the same XSS
// guardrail as lib/highlight.ts — never injected HTML.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fetchChunks, type ChunkOut, type Source } from "../../api/client";
import { matchSpans, type MatchSpan } from "../../lib/answerMatch";
import {
  clipboardAvailable,
  copyToClipboard,
  doiUrl,
  formatCitation,
  isValidDoi,
} from "../../lib/citation";
import { lookupTerm } from "../../lib/glossary";
import { HelpTip } from "../HelpTip";

function authorsText(a: Source["metadata"]["authors"]): string {
  if (Array.isArray(a)) return a.filter(Boolean).join(", ");
  return typeof a === "string" ? a : "";
}

// `doc_type · year · chunk n` from whatever metadata exists; the mockup's
// "chunk 41/88" total and unit kind are not in the response, so they are not
// shown. Falls back to the doc id so the line is never empty.
function provenance(chunk: Pick<Source, "doc_id" | "metadata">): string {
  const m = chunk.metadata;
  const parts = [
    m.doc_type,
    m.year,
    m.chunk_index != null ? `chunk ${m.chunk_index}` : null,
  ].filter((v): v is string | number => v != null && v !== "");
  return parts.length ? parts.map(String).join(" · ") : chunk.doc_id;
}

const btn =
  "rounded-[3px] border border-white/35 px-2 py-[5px] text-[10px] text-[#dce8f3] hover:bg-white/5 disabled:opacity-40";
const walkBtn =
  "shrink-0 rounded-[3px] border border-white/35 px-1.5 py-[2px] font-mono text-[9.5px] text-[#dce8f3] hover:bg-white/5 disabled:opacity-40";
// Same wash + spread as the whole-passage frame, so a mark inside the frame
// deepens it and a mark in a walked neighbour reads as the same treatment.
const mark =
  "rounded-[2px] bg-[rgba(255,209,0,0.17)] text-white shadow-[0_0_0_4px_rgba(255,209,0,0.17)]";

// Slice `text` on the (sorted, non-overlapping) spans into alternating
// text/<mark> nodes. Spans out of order or overlapping cannot occur per the
// matchSpans contract; an empty list returns the text untouched.
function markedNodes(text: string, spans: MatchSpan[]): ReactNode {
  if (spans.length === 0) return text;
  const out: ReactNode[] = [];
  let pos = 0;
  spans.forEach((s, i) => {
    if (s.start > pos) out.push(text.slice(pos, s.start));
    out.push(
      <mark key={i} className={mark}>
        {text.slice(s.start, s.end)}
      </mark>,
    );
    pos = s.end;
  });
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}

export type WalkStatus = "ready" | "loading" | "error";

// Presentational card, exported for the render tests: the walked/loading/error
// states are only reachable through clicks, which the static-markup test
// harness (renderToStaticMarkup, no DOM) cannot perform.
export function ChunkCard({
  source,
  chunk,
  atMatch,
  walkStatus,
  answer,
  prevCtx,
  nextCtx,
  onWalk,
  onBackToMatch,
}: {
  source: Source; // the retrieved chunk — identity, score, citation metadata
  chunk: ChunkOut | null; // the DISPLAYED chunk; null while loading / on error
  atMatch: boolean;
  walkStatus: WalkStatus;
  answer: string; // the run's answer text, for the lexical marks
  prevCtx?: string; // neighbour context paragraphs (match view only)
  nextCtx?: string;
  onWalk: (chunkId: string) => void;
  onBackToMatch: () => void;
}) {
  const m = source.metadata;
  const title = (m.title && String(m.title)) || source.doc_id;
  const authors = authorsText(m.authors);

  // Further steps come from the DISPLAYED chunk's own metadata; an absent id
  // is an honest "no earlier/later chunk", not a dead button with no reason.
  const nav = chunk?.metadata;
  const prevId =
    typeof nav?.prev_chunk_id === "string" && nav.prev_chunk_id ? nav.prev_chunk_id : undefined;
  const nextId =
    typeof nav?.next_chunk_id === "string" && nav.next_chunk_id ? nav.next_chunk_id : undefined;

  const spans = useMemo(
    () => (chunk ? matchSpans(answer, chunk.content) : []),
    [answer, chunk],
  );

  const provLine =
    walkStatus === "loading"
      ? "loading chunk…"
      : walkStatus === "error"
        ? "chunk unavailable"
        : atMatch
          ? provenance(source)
          : `${provenance(chunk!)} · walked from match`;

  // Copy feedback, timer cancelled on unmount (same pattern as CitationActions).
  const [status, setStatus] = useState("");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const canCopy = clipboardAvailable();
  const url = doiUrl(m.doi);
  const copy = async (text: string, label: string) => {
    const ok = await copyToClipboard(text);
    setStatus(ok ? `${label} copied` : "Copy unavailable");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setStatus(""), 2000);
  };

  return (
    <div className="overflow-hidden rounded-panel border border-white/10 bg-ink-500">
      <div className="border-b border-white/10 px-4 py-3.5">
        <div className="mb-[7px] flex items-center gap-[5px]">
          <span className="min-w-0 truncate font-mono text-[10px] text-[#8fb3d4]">
            {provLine}
          </span>
          <span className="ml-auto" />
          {!atMatch ? (
            <button
              type="button"
              onClick={onBackToMatch}
              className="shrink-0 rounded-[3px] px-1.5 py-[2px] font-mono text-[9.5px] text-accent hover:bg-white/5"
            >
              ← back to match
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => prevId && onWalk(prevId)}
            disabled={!prevId || walkStatus === "loading"}
            aria-label="Previous chunk"
            title={!prevId ? "No earlier chunk in this document" : undefined}
            className={walkBtn}
          >
            ‹ prev
          </button>
          <button
            type="button"
            onClick={() => nextId && onWalk(nextId)}
            disabled={!nextId || walkStatus === "loading"}
            aria-label="Next chunk"
            title={!nextId ? "No later chunk in this document" : undefined}
            className={walkBtn}
          >
            next ›
          </button>
          <HelpTip icon dark side="left" term="chunk walking">
            {lookupTerm("chunk walking")} &ldquo;back to match&rdquo; returns to it.
          </HelpTip>
        </div>
        <div className="font-display text-sm font-semibold leading-[1.4] text-white">{title}</div>
        {authors ? (
          <div className="mt-1.5 font-mono text-[10.5px] leading-[1.5] text-[#9dbdda]">
            {authors}
          </div>
        ) : null}
      </div>

      <div className="p-4 text-[13px] leading-[1.85] text-[#a9c1d6]">
        {walkStatus === "loading" ? (
          <p className="font-mono text-[11px] text-[#8fb3d4]">Loading chunk…</p>
        ) : walkStatus === "error" ? (
          // rust is tuned for the paper ground (~2.5:1 on ink) — dark-ground
          // error text uses a light salmon at the readable tier instead.
          <p className="font-mono text-[11px] text-[#e8a48c]">
            That chunk could not be loaded — missing from the collection or a fetch error.
          </p>
        ) : atMatch ? (
          <>
            {prevCtx ? <p className="mb-3 whitespace-pre-wrap">{prevCtx}</p> : null}
            {/* Whole-passage match framing: rgba(255,209,0,.17) wash + matching
                4px spread so the highlight bleeds past the text box (mockup 5b). */}
            <p className="whitespace-pre-wrap rounded-[2px] bg-[rgba(255,209,0,0.17)] text-white shadow-[0_0_0_4px_rgba(255,209,0,0.17)]">
              {markedNodes(source.content, spans)}
            </p>
            {nextCtx ? <p className="mt-3 whitespace-pre-wrap">{nextCtx}</p> : null}
          </>
        ) : (
          <p className="whitespace-pre-wrap">{markedNodes(chunk!.content, spans)}</p>
        )}
        {/* Caption only when marks exist — no spans, no noise. */}
        {walkStatus === "ready" && spans.length > 0 ? (
          <p className="mt-3 flex items-center gap-1.5 font-mono text-[10px] text-[#8fb3d4]">
            highlights = lexical match with the answer (client-side)
            <HelpTip icon dark side="left" term="passage highlighting" />
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-[7px] border-t border-white/10 px-4 py-[11px]">
        <button
          type="button"
          disabled={!canCopy || !isValidDoi(m.doi)}
          onClick={() => copy(String(m.doi), "DOI")}
          className={btn}
          title={!canCopy ? "Clipboard needs a secure (https) context" : undefined}
        >
          Copy DOI
        </button>
        <button
          type="button"
          disabled={!canCopy}
          onClick={() => copy(formatCitation(m, title), "Citation")}
          className={btn}
          title={!canCopy ? "Clipboard needs a secure (https) context" : undefined}
        >
          Cite
        </button>
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-[3px] border border-white/35 px-2 py-[5px] text-[10px] text-accent hover:bg-white/5"
          >
            Open ↗
          </a>
        ) : (
          <span
            aria-disabled="true"
            className="rounded-[3px] border border-white/20 px-2 py-[5px] text-[10px] text-white/45"
          >
            Open ↗
          </span>
        )}
        <span aria-live="polite" className="text-[10px] text-[#8fb3d4]">
          {status}
        </span>
        {/* The retrieval score belongs to the matched chunk; a walked
            neighbour has none and gets none. */}
        {atMatch ? (
          <span className="ml-auto font-mono text-[10px] text-[#8fb3d4]">
            {source.score.toFixed(2)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function SourceViewer({
  source,
  answer,
  collection,
  apiKey,
}: {
  source: Source;
  answer: string; // the run's answer text, for the lexical marks
  collection: string; // registry id the run was sent with; "" = default
  apiKey: string;
}) {
  const m = source.metadata;

  // Neighbour context, when the ingester stamped the ids. Missing/out-of-scope
  // ids are omitted by the API and a failed fetch just leaves the passage alone
  // — context is an enhancement, never a blocker. The same fetch seeds the
  // walk's first step in both directions.
  const ids = [m.prev_chunk_id, m.next_chunk_id].filter(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
  const ctx = useQuery({
    // apiKey in the key: a credential switch must not re-render the previous
    // principal's chunk text from cache (retry:false would make it stick).
    queryKey: ["chunks", apiKey, collection, source.chunk_id, ids],
    queryFn: () => fetchChunks(ids, collection || undefined, apiKey || undefined),
    enabled: ids.length > 0,
    retry: false,
  });
  const ctxById = new Map((ctx.data?.chunks ?? []).map((c) => [c.chunk_id, c]));

  // Walk state: null = the matched chunk. Visited chunks cached so back/
  // forward is instant; both reset when the between-sources pager or the run
  // selector moves. Reset DURING render (not in an effect) so the new source
  // never paints one frame paired with the previous source's walked chunk;
  // keyed on collection too — chunk ids are only unique within a collection.
  // \u0000 as the separator: it cannot occur in a collection id or chunk
  // id, so the key is unambiguous. Written as an ESCAPE, never a raw byte —
  // a literal NUL makes this file binary to `file` and invisible to grep.
  const resetKey = `${collection}\u0000${source.chunk_id}`;
  const [prevKey, setPrevKey] = useState(resetKey);
  const [viewId, setViewId] = useState<string | null>(null);
  const [visited, setVisited] = useState<Map<string, ChunkOut>>(() => new Map());
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setViewId(null);
    setVisited(new Map());
  }

  const cached = (id: string) => visited.get(id) ?? ctxById.get(id);
  const wantId = viewId != null && !cached(viewId) ? viewId : null;
  const walk = useQuery({
    queryKey: ["chunk-walk", apiKey, collection, wantId],
    queryFn: () => fetchChunks([wantId!], collection || undefined, apiKey || undefined),
    enabled: wantId != null,
    retry: false,
  });
  useEffect(() => {
    const chunks = walk.data?.chunks;
    if (!chunks || chunks.length === 0) return;
    setVisited((prev) => {
      const next = new Map(prev);
      for (const c of chunks) next.set(c.chunk_id, c);
      return next;
    });
  }, [walk.data]);

  // A fresh fetch result is readable BEFORE the cache-merge effect runs —
  // otherwise every uncached step paints one "unavailable" frame while the
  // chunk sits in walk.data waiting to be merged. walk.data always belongs to
  // the current wantId (the query key), so no stale-id guard is needed.
  const lookup = (id: string) =>
    cached(id) ?? walk.data?.chunks.find((c) => c.chunk_id === id);

  const atMatch = viewId == null;
  const viewing = viewId != null ? lookup(viewId) : undefined;
  // An id the API omitted (missing/out-of-scope) resolves to an empty chunk
  // list — surfaced as unavailable, not a spinner that never ends.
  const missing = wantId != null && walk.data != null && !lookup(wantId);
  const walkStatus: WalkStatus =
    atMatch || viewing ? "ready" : walk.isError || missing ? "error" : "loading";

  return (
    <ChunkCard
      source={source}
      chunk={atMatch ? source : (viewing ?? null)}
      atMatch={atMatch}
      walkStatus={walkStatus}
      answer={answer}
      prevCtx={m.prev_chunk_id ? ctxById.get(m.prev_chunk_id)?.content : undefined}
      nextCtx={m.next_chunk_id ? ctxById.get(m.next_chunk_id)?.content : undefined}
      onWalk={(id) => setViewId(id === source.chunk_id ? null : id)}
      onBackToMatch={() => setViewId(null)}
    />
  );
}
