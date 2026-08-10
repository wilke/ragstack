// Client-side answer↔chunk lexical matching. The API returns NO match offsets
// (documented gap — see highlight.ts: a future backend `match_start`/`match_end`
// would supersede this), so the only honest signal available is lexical: which
// chunk sentences share enough content words with some answer sentence to have
// plausibly contributed. This is overlap, not entailment — the consumer MUST
// label the highlight as a lexical approximation, never as model-attributed
// grounding. Pure string functions — no DOM, no React, no Date, no randomness.
//
// The sentence grammar mirrors claims.ts splitClaims (terminal punctuation plus
// trailing quotes/brackets; claims.ts does not export its splitter, and this one
// must also track character offsets). Same tradeoff applies: abbreviation-heavy
// prose may over-split, which costs a fragment sentence here — fragments below
// the shared-token floor simply never match, so over-splitting loses recall,
// never correctness.

export interface MatchSpan {
  start: number; // chunk-relative character offsets (UTF-16 units, slice-safe)
  end: number;
  score: number; // best overlap coefficient vs any answer sentence, in (0, 1]
}

export interface MatchOptions {
  /** Minimum score for a chunk sentence to count as supporting. */
  threshold?: number;
  /** Floor on shared informative tokens — keeps two-word fragments from matching on noise. */
  minSharedTokens?: number;
  /** Tokens shorter than this are dropped before comparison. */
  minTokenLength?: number;
}

const DEFAULTS: Required<MatchOptions> = {
  threshold: 0.5,
  minSharedTokens: 2,
  minTokenLength: 2,
};

// Function words carry no evidential weight; the trailing group catches the
// stems the tokenizer leaves behind when apostrophes split contractions
// ("don't" → "don" + "t"; one-letter pieces already fall below minTokenLength).
const STOPWORDS = new Set(
  (
    "a an the and or but if then than that this these those it its is are was were be been being am " +
    "do does did done have has had having will would can could should shall may might must " +
    "of in on at to for from by with without as into onto over under about between through " +
    "during before after above below up down out off not no nor so too very also just only such " +
    "both each few more most other some any all own same we our you your they their them he she " +
    "his her him me my us what which who whom when where why how there here because while until " +
    "again further once " +
    "don doesn isn aren wasn weren won didn hasn haven couldn wouldn shouldn re ve ll"
  ).split(" "),
);

// Sentence = up to terminal punctuation (plus closing quotes/brackets), or an
// unterminated run. Newlines are excluded from sentence bodies so paragraph
// breaks split without a separate pre-pass (claims.ts pre-splits paragraphs
// instead — same effect, but offsets survive here). Fresh regex per call: the
// global flag carries lastIndex state across calls otherwise.
const SENTENCE_SRC = String.raw`[^.!?\n]*[.!?]+["'”’)\]]*|[^.!?\n]+`;

interface SentenceSpan {
  text: string;
  start: number;
  end: number;
}

// Offsets are trimmed to the sentence's visible text so downstream <mark>
// spans never open on whitespace.
function sentenceSpans(text: string): SentenceSpan[] {
  const spans: SentenceSpan[] = [];
  for (const m of text.matchAll(new RegExp(SENTENCE_SRC, "g"))) {
    const trimmed = m[0].trim();
    if (!trimmed) continue;
    const start = m.index + (m[0].length - m[0].trimStart().length);
    spans.push({ text: trimmed, start, end: start + trimmed.length });
  }
  return spans;
}

/** Split text into trimmed sentences (same grammar claims.ts uses for claims). */
export function splitSentences(text: string): string[] {
  return sentenceSpans(text).map((s) => s.text);
}

// Unicode-aware content tokens: lowercase, letters/digits only, stopwords and
// short tokens dropped. A Set — this is bag-of-words presence, not frequency.
function tokenize(text: string, minTokenLength: number): Set<string> {
  const out = new Set<string>();
  for (const m of text.toLowerCase().matchAll(/[\p{L}\p{N}]+/gu)) {
    if (m[0].length >= minTokenLength && !STOPWORDS.has(m[0])) out.add(m[0]);
  }
  return out;
}

// `[n]` citation markers (grammar shared with claims.ts) are answer plumbing,
// not answer content — stripped so "[12]" cannot collide with a chunk number.
const CITE_RE = /\[\d+(?:\s*[,;]\s*\d+)*\]/g;

// Overlap coefficient |∩| / min(|a|, |b|) rather than Jaccard: a long chunk
// sentence that fully contains an answer sentence's content scores 1 instead
// of being diluted by its own extra tokens, and a short chunk sentence wholly
// covered by the answer likewise scores 1 (containment in either direction).
function overlap(a: Set<string>, b: Set<string>, minShared: number): number {
  let shared = 0;
  for (const t of a) if (b.has(t)) shared++;
  if (shared < minShared) return 0;
  return shared / Math.min(a.size, b.size);
}

// Sentences that abut across only whitespace fuse into one span; scores keep
// the max, never sum — a merged span is not "more supported".
function mergeAdjacent(spans: MatchSpan[], text: string): MatchSpan[] {
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  const out: MatchSpan[] = [];
  for (const s of sorted) {
    const prev = out[out.length - 1];
    if (prev && (s.start <= prev.end || !text.slice(prev.end, s.start).trim())) {
      prev.end = Math.max(prev.end, s.end);
      prev.score = Math.max(prev.score, s.score);
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

/**
 * Character spans into `chunkText` for sentences whose lexical overlap with
 * any answer sentence clears the threshold. Sorted by position, non-overlapping
 * (adjacent matches merged). Empty array when nothing clears — the viewer then
 * falls back to whole-passage framing, never a fabricated highlight.
 */
export function matchSpans(
  answer: string,
  chunkText: string,
  opts?: MatchOptions,
): MatchSpan[] {
  const { threshold, minSharedTokens, minTokenLength } = { ...DEFAULTS, ...opts };

  const answerTokens = sentenceSpans(answer.replace(CITE_RE, " "))
    .map((s) => tokenize(s.text, minTokenLength))
    .filter((t) => t.size > 0);
  if (answerTokens.length === 0) return [];

  const matched: MatchSpan[] = [];
  for (const sentence of sentenceSpans(chunkText)) {
    const chunkTokens = tokenize(sentence.text, minTokenLength);
    if (chunkTokens.size === 0) continue;
    let best = 0;
    for (const tokens of answerTokens) {
      const score = overlap(chunkTokens, tokens, minSharedTokens);
      if (score > best) best = score;
    }
    if (best >= threshold) {
      matched.push({ start: sentence.start, end: sentence.end, score: best });
    }
  }
  return mergeAdjacent(matched, chunkText);
}
