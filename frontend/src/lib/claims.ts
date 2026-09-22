// The canonical answer-text module: one grammar for `[n]` citation markers,
// consumed by both presentations — Explore's editorial split (lead sentence +
// paragraphs, markers as chips: answerParagraphs/segmentCitations/firstCited) and
// Evidence's claim-by-claim decomposition (splitClaims). Pure string
// functions — no DOM, no React.
//
// Per the design handoff, per-claim GROUNDING does not exist server-side yet —
// this module extracts only what the answer text actually contains (sentences +
// citation markers); it must never invent a score. The splitters are
// deliberately simple: abbreviation-heavy prose may over-split ("Dr. Smith"
// ends a sentence early), which costs a short lead or an extra claim block,
// never lost text.

// [1] · [1, 3] · [1;3] — the marker shapes LLM answers actually emit. A fresh
// regex per call (the global flag carries lastIndex state across calls
// otherwise).
const CITE_SRC = String.raw`\[(\d+(?:\s*[,;]\s*\d+)*)\]`;

// A marker's payload ("1, 3") as deduped ascending 1-based ranks, keeping only
// ranks the response actually returned — a marker pointing outside 1..n must
// never become a chip for a source that does not exist.
function markerRanks(payload: string, sourceCount: number): number[] {
  const out = new Set<number>();
  for (const part of payload.split(/[,;]/)) {
    const n = Number(part.trim());
    if (Number.isInteger(n) && n >= 1 && n <= sourceCount) out.add(n);
  }
  return [...out].sort((a, b) => a - b);
}

// ---------------------------------------------------------------------------
// Explore: editorial answer block
// ---------------------------------------------------------------------------

// The answer's paragraphs, in order. Blank lines separate them.
//
// This deliberately does NOT split into a "lead sentence" and a remainder.
// It used to: a regex took everything up to the first `.` `!` `?` followed by
// whitespace. In a biomedical corpus that is unwinnable — every genus
// abbreviation ends a "sentence":
//
//     "Several genes in E. coli are responsible…"  ->  "Several genes in E."
//     "Growth was measured at 37 deg C. vs. 42…"   ->  "Growth was measured at 37 deg C."
//
// and the broken half rendered outside the highlighted block. `E. coli`,
// `S. aureus`, `et al.`, `i.e.`, `Fig.`, `spp.` — an abbreviation list is a
// losing game against this corpus, so the answer is now one block and the
// distinction is gone.
// A PARAGRAPH is separated by a blank line; a LINE is separated by a single
// newline, and lines are preserved inside their paragraph. The distinction
// matters: the generator has no format constraint (`_SYSTEM_PROMPT` in
// python/ragstack/llm.py) and routinely emits markdown bullet lists on single
// newlines — "Summary:\n- gene A\n- gene B". Splitting on `\n{2,}` alone kept
// that as one string, and without `whitespace-pre-line` on the `<p>` the browser
// collapsed it to "Summary: - gene A - gene B" on one line. That was a
// regression against the old splitter, which split on every newline. So:
// `\r\n` is normalised first, a "blank" line may carry whitespace (`\n  \n`
// is a break), and the rendering side keeps the single newlines as breaks.
export function answerParagraphs(answer: string): string[] {
  return answer
    .replace(/\r\n?/g, "\n")
    .split(/(?:\n[ \t]*){2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
}

export type CitationSegment = { text: string } | { cite: number };

// Marker → citation chip(s), one per in-range rank ("[1, 3]" → two chips). A
// marker whose every rank is out of range stays literal text rather than
// pointing at nothing. One space before a kept marker is swallowed so the
// chip hugs its sentence.
export function segmentCitations(text: string, sourceCount: number): CitationSegment[] {
  const out: CitationSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(new RegExp(CITE_SRC, "g"))) {
    const ranks = markerRanks(m[1], sourceCount);
    if (ranks.length === 0) continue;
    const before = text.slice(last, m.index).replace(/ $/, "");
    if (before) out.push({ text: before });
    for (const n of ranks) out.push({ cite: n });
    last = m.index + m[0].length;
  }
  const tail = text.slice(last);
  if (tail) out.push({ text: tail });
  return out;
}

// The first source the answer cites — its chips get the yellow treatment
// (per-claim grounding is a backend gap, so "cited by this claim" reduces to
// "cited first" for now).
export function firstCited(answer: string, sourceCount: number): number | null {
  for (const m of answer.matchAll(new RegExp(CITE_SRC, "g"))) {
    const ranks = markerRanks(m[1], sourceCount);
    if (ranks.length > 0) return ranks[0];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Evidence: claim-by-claim decomposition
// ---------------------------------------------------------------------------

export interface Claim {
  text: string; // the sentence, citation markers stripped for display
  cited: number[]; // 0-based indices into the run's sources, deduped, in-range only
}

function extractCited(fragment: string, sourceCount: number): number[] {
  const out = new Set<number>();
  for (const m of fragment.matchAll(new RegExp(CITE_SRC, "g"))) {
    for (const n of markerRanks(m[1], sourceCount)) out.add(n - 1); // 1-based → 0-based
  }
  return [...out].sort((a, b) => a - b);
}

/**
 * Split an answer into sentence-level claims with their cited source indices.
 * Markers pointing outside the retrieved set are dropped (never rendered as a
 * chip for a source that does not exist). A fragment that is ONLY markers
 * ("… bees. [1]") attaches its citations to the preceding sentence.
 */
export function splitClaims(answer: string, sourceCount: number): Claim[] {
  const claims: Claim[] = [];
  for (const para of answer.split(/\n+/)) {
    const trimmed = para.trim();
    if (!trimmed) continue;
    // Sentence = up to terminal punctuation (plus closing quotes/brackets and
    // any citation markers trailing it), or the unterminated tail.
    //
    // The trailing-marker clause is load-bearing. Without it
    // "Bees pollinate. [1] Nectar follows. [2]" hands [1] to the SECOND
    // sentence — every citation shifts one claim down and the first claim
    // renders uncited — and markers-after-the-period is a form the generator
    // actually emits. This is the sentence splitter Explore's lead used to
    // share (LEAD_RE, since removed — the answer is one block now, see
    // answerParagraphs). Evidence keeps sentence granularity here and so still
    // breaks at "E. coli"; that is the other half of the problem, tracked in
    // #625. Do not "fix" it with an abbreviation list.
    const sentences =
      trimmed.match(
        new RegExp(String.raw`[^.!?]*[.!?]+["'”’)\]]*(?:\s*${CITE_SRC})*|[^.!?]+$`, "g"),
      ) ?? [trimmed];
    for (const raw of sentences) {
      const cited = extractCited(raw, sourceCount);
      const text = raw
        .replace(new RegExp(CITE_SRC, "g"), "")
        .replace(/\s{2,}/g, " ")
        .replace(/\s+([.,;:!?])/g, "$1")
        .trim();
      if (!text || /^["'”’)\].,;:!?]*$/.test(text)) {
        // Marker-only (or punctuation-only) fragment — fold into the last claim.
        const prev = claims[claims.length - 1];
        if (prev && cited.length) {
          prev.cited = [...new Set([...prev.cited, ...cited])].sort((a, b) => a - b);
        }
        continue;
      }
      claims.push({ text, cited });
    }
  }
  return claims;
}
