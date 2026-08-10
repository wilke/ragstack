// The canonical answer-text module: one grammar for `[n]` citation markers,
// consumed by both presentations — Explore's editorial split (lead sentence +
// paragraphs, markers as chips: splitAnswer/segmentCitations/firstCited) and
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

export interface AnswerParts {
  lead: string;
  rest: string[]; // remaining paragraphs (the first paragraph's tail included)
}

// Lead claim = the first sentence: up to the first `.` `!` `?` followed by
// whitespace/end, letting attached citation markers ride along ("…axis. [1]").
// Decimals survive: "." before a digit has no following whitespace.
const LEAD_RE = new RegExp(String.raw`^[\s\S]*?[.!?](?:\s*${CITE_SRC})*(?=\s|$)`);

export function splitAnswer(answer: string): AnswerParts {
  const paras = answer
    .split(/\n+/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (paras.length === 0) return { lead: "", rest: [] };
  const [first, ...others] = paras;
  const m = first.match(LEAD_RE);
  if (!m || m[0].length === first.length) return { lead: first, rest: others };
  const tail = first.slice(m[0].length).trim();
  return { lead: m[0], rest: tail ? [tail, ...others] : others };
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
    // Sentence = up to terminal punctuation (plus closing quotes/brackets), or
    // the unterminated tail of the paragraph.
    const sentences = trimmed.match(/[^.!?]*[.!?]+["'”’)\]]*|[^.!?]+$/g) ?? [trimmed];
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
