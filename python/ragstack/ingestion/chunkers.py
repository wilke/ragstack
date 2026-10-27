"""Text chunkers — split documents into overlapping passages.

Every chunker satisfies the :class:`ragstack.protocols.Chunker` protocol
(``chunk(doc) -> list[Chunk]``) and assigns a **deterministic** chunk id of
``uuid5(NAMESPACE_URL, "{doc.id}:{start_char}:{end_char}")``. That identity is
the linchpin of idempotent re-ingest (re-ingesting the same document overwrites
the same store points instead of duplicating them), so every chunker must track
real character spans into ``doc.content`` rather than re-``find()``-ing chunk
strings — overlapping or repeated text would otherwise collide or mis-locate.

The sentence/word/semantic chunkers are adapted from ramanathanlab/distllm
(MIT License, https://github.com/ramanathanlab/distllm) via BV-BRC's
``embedding_app``; the semantic algorithm itself derives from LlamaIndex's
``semantic_splitter`` (MIT License). Char-span tracking is original to this
port: the upstream chunkers return bare strings, which would break the
deterministic-id contract above.
"""
from __future__ import annotations

import logging
import math
import re
import sys
import uuid
from collections.abc import Callable, Sequence

from ragstack.ingestion.tokenization import TokenCounter
from ragstack.models import Chunk, Document

logger = logging.getLogger(__name__)

# A callable that turns a list of texts into a list of dense vectors. Sync on
# purpose: chunkers run synchronously inside the (already-async) ingestion
# pipeline, so the semantic chunker takes a plain sync embed function rather
# than the async Embedder protocol. ``api/deps.py`` bridges the configured
# async embedder into this shape.
EmbedFn = Callable[[Sequence[str]], Sequence[Sequence[float]]]

#: How the sentence/word chunkers measure a chunk against the token budget.
#:
#: ``joined``  — fill to the budget measured on the JOINED chunk text, then cut
#:               back to the nearest unit boundary. The stated intent of
#:               word/sentence chunking, and the default.
#: ``summed``  — LEGACY: pack while the sum of per-unit counts, each unit
#:               tokenized in isolation, stays within budget. Systematically
#:               under-fills (see :func:`_pack_spans_tokens` for the measured
#:               factors and for why the mode is kept rather than deleted).
BUDGET_MODES: tuple[str, ...] = ("joined", "summed")
DEFAULT_BUDGET_MODE = "joined"


def _validate_budget_mode(budget_mode: str) -> str:
    """Reject an unknown ``budget_mode`` at construction time.

    Loud rather than silently falling back to a default: the two modes produce
    materially differently-sized chunks, and a typo that quietly selected the
    wrong one would mis-size a whole index without saying so.
    """
    if budget_mode not in BUDGET_MODES:
        raise ValueError(
            f"unknown budget_mode {budget_mode!r}; valid: {', '.join(BUDGET_MODES)}"
        )
    return budget_mode


#: What ``chunk_size`` counts for the sentence/word chunkers (issue #318 P1).
#:
#: ``chars``  — DEFAULT: pack whole units to ``chunk_size`` chars. With a token
#:              budget (``max_tokens`` + ``token_counter``) that budget is a CAP:
#:              a chunk over it is re-packed at unit boundaries to fit (see
#:              :func:`_pack_spans`). Without one, byte-identical to the original
#:              char packing.
#: ``tokens`` — ``chunk_size`` is a token budget. Pack to
#:              ``min(chunk_size, max_tokens)`` (or ``chunk_size`` with no cap),
#:              and hard-split a single over-budget unit at that SAME budget.
#:              Requires a ``token_counter``.
#:
#: ``chunk_overlap`` is chars under both. (The pre-#318 behaviour — a token
#: budget silently replacing ``chunk_size`` — is gone; see :func:`_pack_spans`.)
SIZE_UNITS: tuple[str, ...] = ("chars", "tokens")
DEFAULT_SIZE_UNIT = "chars"

#: The SEMANTIC size-cap marker: what ``chunk_params["size_cap"]`` may record.
#:
#: ``"tokens"`` — ``chunk_size`` is an UPPER BOUND, in tokens of the collection
#: embedding model's tokenizer (the same counter as the window cap), on every
#: semantic chunk. It must be <= the resolved window budget (``max_tokens``);
#: a larger one is refused, never clamped.
#:
#: **Absent** — the historical behaviour, kept exactly: ``chunk_size`` is used
#: only by the oversized-document ``fixed_token`` fallback and the only cap is
#: ``max_tokens``. Every semantic collection created before the marker existed
#: records ``chunk_size`` (often a meaningless 512) and was chunked uncapped, so
#: reading its ``chunk_size`` as a cap now would mix two chunkings in one
#: collection (#647's defect class). Only a recorded marker turns the cap on.
SIZE_CAP_TOKENS = "tokens"
SIZE_CAPS: tuple[str, ...] = (SIZE_CAP_TOKENS,)

#: How a semantic block over the cap is split (``chunk_params["cap_split"]``).
#: Only meaningful with a recorded ``size_cap``; an uncapped (legacy) collection
#: is always ``greedy``, which is what it was built with.
#:
#: ``greedy``   — re-pack the block's sentences greedily into <= cap pieces
#:                (:func:`_cap_unit_range`); can leave a small tail (520 tokens
#:                at cap 512 -> 512 + 8). The behaviour before ``cap_split``.
#: ``balanced`` — split into k = ceil(tokens / cap) pieces of roughly equal size
#:                at sentence boundaries, raising k until every piece fits
#:                (:func:`_balanced_unit_range`).
#: ``topic``    — split recursively at the block's strongest internal breakpoint
#:                (largest consecutive-buffer cosine distance, the distances
#:                breakpoint detection already computed: no extra embedding)
#:                until every piece fits (:meth:`SemanticChunker._topic_split`).
#:
#: Under all three a cut lands inside a sentence only when that one sentence
#: alone is over the cap (hard token split, as before).
CAP_SPLITS: tuple[str, ...] = ("greedy", "balanced", "topic")
DEFAULT_CAP_SPLIT = "greedy"


def _validate_size_unit(
    size_unit: str, chunk_size: int, token_counter: TokenCounter | None
) -> str:
    """Reject an unknown ``size_unit``, and a ``tokens`` request that can't be met.

    ``size_unit="tokens"`` without a counter is refused rather than degraded to
    chars — the same rule as ``fixed_token``: a silently substituted unit re-sizes
    the whole corpus and only a log line would say so.
    """
    if size_unit not in SIZE_UNITS:
        raise ValueError(
            f"unknown size_unit {size_unit!r}; valid: {', '.join(SIZE_UNITS)}"
        )
    if size_unit == "tokens":
        if token_counter is None:
            raise ValueError(
                "size_unit='tokens' requires a token_counter: chunk_size is a token "
                "budget and there is nothing to count tokens with. Refusing to fall "
                "back to a char budget."
            )
        if chunk_size != -1 and chunk_size < 1:
            raise ValueError(
                f"size_unit='tokens' needs chunk_size >= 1 (or -1 to disable "
                f"chunking); got {chunk_size}"
            )
    return size_unit


_NAMESPACE = uuid.NAMESPACE_URL


def _chunk_id(doc_id: str, start: int, end: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{doc_id}:{start}:{end}"))


def _make_chunk(doc: Document, start: int, end: int) -> Chunk:
    """Build a Chunk for the half-open span ``doc.content[start:end]``.

    Content is sliced from the span (never re-found) so ``start_char``/
    ``end_char`` always reconstruct the source exactly.

    Invariant: this is the ONLY place a ``Chunk`` is constructed, and it sets
    ``doc_id=doc.id``. Keep it that way — ``IngestionPipeline.ingest`` relies on
    ``chunk.doc_id == doc.id`` to decide which docs' prior chunks to replace.
    """
    return Chunk(
        id=_chunk_id(doc.id, start, end),
        doc_id=doc.id,
        content=doc.content[start:end],
        metadata=dict(doc.metadata),
        start_char=start,
        end_char=end,
    )


# --------------------------------------------------------------------------- #
# Token-budget splitting
# --------------------------------------------------------------------------- #
# Shared by every chunker's hard-cap path: split a single piece of text into
# substrings that each encode to <= max_tokens, preserving the text exactly
# (concatenating the pieces reproduces the input). Used both to enforce the cap
# on a packed chunk and to break a single over-budget unit (a very long sentence
# or word) so no source text is ever dropped.


def _hf_offset_tokenizer(token_counter: TokenCounter):
    """Return the HF *fast* tokenizer behind an ``HFTokenCounter``, or ``None``.

    ``HFTokenCounter`` (and the fakes in the tests) expose ``_tokenizer`` as a
    zero-arg callable returning a fast tokenizer that supports
    ``return_offsets_mapping=True``. Non-HF counters (estimate/endpoint) have no
    such attribute. Mirrors the guard in :class:`FixedTokenWindowChunker`.
    """
    get = getattr(token_counter, "_tokenizer", None)
    if not callable(get):
        return None
    try:
        return get()
    except Exception:  # noqa: BLE001 - a tokenizer that won't load → fall back
        return None


def _split_by_offsets(text: str, max_tokens: int, tokenizer) -> list[str] | None:
    """O(n) token-budget split via the fast tokenizer's offset mapping.

    Tokenize ``text`` ONCE with ``return_offsets_mapping=True`` and slice on token
    boundaries into runs of <= ``max_tokens`` tokens, each spanning a contiguous
    char range. Pieces tile ``text`` gaplessly: piece ``k`` runs from the char
    where its first token starts to the char where its last token ends, and the
    next piece resumes exactly there — the inter-token gap (if any) rides with the
    piece before it, so concatenation reproduces ``text`` byte-for-byte.

    Returns ``None`` if the tokenizer can't offset-map (caller falls back). A single
    linear pass, no per-piece re-tokenization — the whole point of the O(n) rewrite.
    """
    try:
        enc = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
        offsets = enc["offset_mapping"]
    except Exception:  # noqa: BLE001 - not a fast tokenizer / no offsets → fall back
        return None
    n = len(offsets)
    if n == 0:
        return [text]
    # Whole text already fits: one piece, no per-slice work. (The single tokenize
    # above replaces the caller's separate full count(text) guard.)
    if n <= max_tokens:
        return [text]

    # A slice re-tokenized in ISOLATION can gain a leading/merge token vs. its
    # in-context count, so a naive window of exactly max_tokens tokens could
    # re-count to max_tokens+1. Carve at `budget = max_tokens - 1` tokens instead,
    # reserving one token of headroom, so an isolated re-count still fits — a single
    # linear pass with NO per-piece re-tokenization (that re-count over ~n/budget
    # pieces was itself another full-text tokenization). budget floors at 1 so a
    # tiny max_tokens still makes progress; a lone token that alone re-counts over
    # budget is indivisible and left to the embed-side isolate-and-drop backstop.
    budget = max(1, max_tokens - 1)
    pieces: list[str] = []
    char_start = 0  # start of the current piece; absorbs any leading gap
    tok_start = 0
    while tok_start < n:
        tok_end = min(tok_start + budget, n)
        # Last piece runs to end-of-text so any trailing gap is preserved.
        char_end = len(text) if tok_end >= n else offsets[tok_end - 1][1]
        pieces.append(text[char_start:char_end])
        char_start = char_end
        tok_start = tok_end
    return pieces


def _split_by_estimate(
    text: str, max_tokens: int, token_counter: TokenCounter
) -> list[str]:
    """Bounded (non-O(n^2)) token-budget split for counters with no offset map.

    Estimate a chars-per-token ratio from ONE full count of ``text``, seek to the
    estimated char length for a <= ``max_tokens`` piece, then do a small local
    linear adjustment (a handful of ``count`` calls on a *bounded* window, not on
    growing prefixes) to land on the largest fitting boundary. Lossless: pieces
    tile ``text`` exactly. A 1-char floor guarantees forward progress.
    """
    n = len(text)
    total = token_counter.count(text)
    # Chars per token from the whole text; clamp so a degenerate ratio can't stall.
    cpt = max(1.0, n / total) if total > 0 else float(n or 1)
    pieces: list[str] = []
    start = 0
    while start < n:
        # Initial guess: slightly under budget to bias toward fitting on the first
        # count, then adjust locally.
        guess = start + max(1, int(max_tokens * cpt * 0.9))
        end = min(max(start + 1, guess), n)
        if token_counter.count(text[start:end]) <= max_tokens:
            # Grow while it still fits (bounded local steps, ~cpt chars each).
            step = max(1, int(cpt))
            while end < n and token_counter.count(text[start : end + step]) <= max_tokens:
                end += step
            # Fine-tune the last partial step one char at a time.
            while end < n and token_counter.count(text[start : end + 1]) <= max_tokens:
                end += 1
        else:
            # Shrink until it fits (bounded local steps back toward ``start``).
            step = max(1, int(cpt))
            while end > start + 1 and token_counter.count(text[start:end]) > max_tokens:
                end = max(start + 1, end - step)
            # Recover any over-shrink one char at a time.
            while end < n and token_counter.count(text[start : end + 1]) <= max_tokens:
                end += 1
        pieces.append(text[start:end])
        start = end
    return pieces


def split_text_to_token_budget(
    text: str, max_tokens: int, token_counter: TokenCounter
) -> list[str]:
    """Split ``text`` into substrings each <= ``max_tokens`` tokens, lossless.

    The pieces concatenate back to ``text`` exactly (no characters added or
    dropped), so a caller mapping them onto char spans keeps offsets honest.

    Strategy: if the whole text already fits, return it unchanged. Otherwise, when
    the counter is an HF fast tokenizer, tokenize the whole text ONCE with offset
    mapping and slice on token boundaries in a single linear pass
    (:func:`_split_by_offsets`) — O(n), so a multi-million-char blob splits in
    seconds instead of the old prefix-rescanning O(n^2). For counters without an
    offset map (estimate/endpoint) fall back to a *bounded* estimate-and-adjust
    split (:func:`_split_by_estimate`) that never re-tokenizes growing prefixes.
    Both paths are lossless and guarantee forward progress (a 1-token/1-char floor)
    even for a pathological indivisible unit.
    """
    if not text or max_tokens <= 0:
        return [text] if text else []

    # HF fast tokenizer: tokenize ONCE with offsets and slice linearly. The single
    # tokenize also tells us the total token count, so we skip the separate full
    # count(text) guard the char-space path needs (which on a multi-million-char
    # blob is itself a full tokenization — doing both doubled the cost).
    tokenizer = _hf_offset_tokenizer(token_counter)
    if tokenizer is not None:
        pieces = _split_by_offsets(text, max_tokens, tokenizer)
        if pieces is not None:
            return pieces

    if token_counter.count(text) <= max_tokens:
        return [text]
    return _split_by_estimate(text, max_tokens, token_counter)


class RecursiveCharacterChunker:
    """Split text by characters with configurable size and overlap.

    When ``max_tokens`` + ``token_counter`` are supplied, any emitted piece that
    still exceeds the token budget (a char window of dense text can) is further
    split by tokens so no chunk overflows the embedder context. With
    ``max_tokens`` None the behaviour is the original char-only splitting.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        *,
        max_tokens: int | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_tokens = max_tokens
        self.token_counter = token_counter

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.extend(self._emit(doc, start, end))
            if end == len(text):
                break
            start = end - self.chunk_overlap
        return chunks

    def _emit(self, doc: Document, start: int, end: int) -> list[Chunk]:
        """Emit the span, token-splitting it first if a budget is configured."""
        if self.max_tokens is None or self.token_counter is None:
            return [_make_chunk(doc, start, end)]
        return _token_split_span(doc, start, end, self.max_tokens, self.token_counter)


def _token_split_span(
    doc: Document,
    start: int,
    end: int,
    max_tokens: int,
    token_counter: TokenCounter,
) -> list[Chunk]:
    """Make Chunk(s) for ``doc.content[start:end]``, splitting on the token budget.

    The pieces from :func:`split_text_to_token_budget` tile the span gaplessly, so
    each maps to a contiguous ``(start, start+len)`` char range — offsets still
    reconstruct the source and chunk ids stay deterministic.
    """
    pieces = split_text_to_token_budget(doc.content[start:end], max_tokens, token_counter)
    if len(pieces) <= 1:
        return [_make_chunk(doc, start, end)]
    out: list[Chunk] = []
    cursor = start
    for piece in pieces:
        out.append(_make_chunk(doc, cursor, cursor + len(piece)))
        cursor += len(piece)
    return out


# --------------------------------------------------------------------------- #
# Sentence splitting with char spans
# --------------------------------------------------------------------------- #
# Sentence/word span helpers return (start, end) half-open spans into ``text``
# that tile the whole string with no gaps and no overlap: span[i].end ==
# span[i+1].start and the last span ends at len(text). Concatenating
# text[s:e] for every span reproduces ``text`` byte-for-byte (whitespace
# included), which is what keeps the deterministic chunk ids honest downstream.

#: Identity of the *designed* sentence segmenter — the Punkt backend, what
#: :func:`sentence_spans` returns for a given text when ``nltk`` is installed
#: (the ``[chunking]`` extra). **Bump it whenever ``sentence_spans`` output can
#: change for any input** (a new abbreviation, a changed heuristic, a new
#: backend rule): a sentence boundary is a chunk boundary, and chunk ids are
#: ``uuid5(doc_id:start:end)``, so a moved boundary is a moved chunk id and a
#: moved point id. Bump :data:`REGEX_SEGMENTER_VERSION` with it.
#:
#: This constant is NOT what gets recorded. :func:`segmenter_version` is — it
#: names the backend actually in use, so a process without ``nltk`` records
#: :data:`REGEX_SEGMENTER_VERSION` instead (#815: every image before v1.6.15
#: lacked ``nltk`` and ran the regex fallback while recording nothing). The
#: recorded value lands in the production receipt / archive provenance
#: (:func:`ragstack.provenance.tool_provenance`), every
#: :func:`ragstack.provenance.sentence_spans_fingerprint` and every
#: segmentation-cache key, so a reader can tell which segmentation a collection
#: was built with.
#:
#: History: ``"punkt-bare"`` (implicit; never recorded) — ``PunktSentenceTokenizer()``
#: with no parameters, every period a candidate break; the code that produced
#: the stage0 results and ``/rag/snapshots/regression/v1``.
#: ``"punkt-abbrev-v1"`` — :data:`SENTENCE_ABBREVIATIONS` seeded into Punkt and
#: mirrored by the regex fallback (#318 P2).
SEGMENTER_VERSION: str = "punkt-abbrev-v1"

#: The regex fallback's counterpart of :data:`SEGMENTER_VERSION`, recorded by
#: :func:`segmenter_version` when ``nltk`` is unavailable. The two backends
#: share :data:`SENTENCE_ABBREVIATIONS` but are different algorithms (Punkt's
#: orthographic/collocation heuristics vs. the regex post-filter), so their
#: boundaries can differ on real text: they must never share a cache key or a
#: provenance label. Bump it together with :data:`SEGMENTER_VERSION`.
#:
#: History: ``"regex-abbrev-v1"`` — the fallback with :data:`SENTENCE_ABBREVIATIONS`
#: (#318 P2); first recorded under this name by #815. Records written before
#: #815 by a process without ``nltk`` say ``"punkt-abbrev-v1"`` wrongly.
REGEX_SEGMENTER_VERSION: str = "regex-abbrev-v1"

#: Abbreviations after which a period is NOT a sentence break, for both sentence
#: backends. Punkt's convention: lower case, WITHOUT the trailing period, and
#: one *Punkt token* each — Punkt splits on whitespace, so "et al." is matched
#: by its last token ``al``, and "U.S.A." by ``u.s.a``. Matching is
#: case-insensitive (Punkt lowercases the token), so an entry also suppresses
#: the break after the same letters in upper case: that is why clinical
#: acronyms that end sentences (``ED``, ``MS``, ``AM``) are left out.
#:
#: Inclusion rule: an entry is here when its period-terminated form occurs
#: mid-sentence in biomedical full text. A missing entry is a FALSE SPLIT (a
#: chunk can start "2 shows the effect …"); a present entry at a true sentence
#: end is a FALSE MERGE (one sentence unit holds two sentences) — the cheaper
#: error for chunking, so ambiguous entries (``etc``, ``no``, ``al``) are in.
#: Excluded on purpose, because their period form is predominantly a sentence
#: end: ``min``/``max``/``sec`` ("for 5 min. Cells were …", "VO2 max. Many …"
#: — SI writes the unit without a period, so the period is the sentence's),
#: ``yr(s)``/``hr(s)``/``wk``/``mo``/``lb(s)``, ``resp`` ("… respectively."),
#: ``ed``/``eds`` (the emergency-department acronym ``ED``/``EDs``), ``arch``,
#: ``art``, ``app``, ``est`` (``EST``s), ``cv`` (``CV``). Kept despite a known
#: acronym collision, because the abbreviation is the commoner period form:
#: ``no`` (``NO``, nitric oxide), ``co`` (``CO``), ``sp`` (``SP``), ``str``
#: (``STR``), ``mr`` (``MR``; on the 50 regression documents the entry removes
#: 18 false "Mr." splits and makes 5 false merges after a sentence-final
#: "MR.") — each costs a false merge where the acronym ends a sentence.
#:
#: Single letters (genus initials — *E. coli*, *S. enterica*, *B. subtilis* —
#: and personal initials) are deliberately NOT entries. Punkt's built-in
#: initial heuristic already refuses a break after a lone letter followed by a
#: letter, upper or lower case; an entry would change only "X. <digit>" — and
#: wrongly ("Group B. 3 patients left." would merge). The regex fallback
#: mirrors that heuristic (:func:`_fallback_keeps_break`).
#:
#: Changing this set changes :func:`sentence_spans` output: bump
#: :data:`SEGMENTER_VERSION` and :data:`REGEX_SEGMENTER_VERSION`.
SENTENCE_ABBREVIATIONS: frozenset[str] = frozenset({
    # Honorifics, titles and degrees before/after a name ("Dr. Smith", "Ph.D.").
    "dr", "drs", "prof", "profs", "mr", "mrs", "messrs", "jr", "sr", "rev",
    "ph.d", "m.d", "b.sc", "m.sc", "m.p.h", "d.v.m", "b.a", "m.a",
    # Latin and scholarly connectives ("et al.", "i.e.", "cf.", "vs."); "al" is
    # the token Punkt sees for "et al.".
    "al", "e.g", "i.e", "cf", "viz", "vs", "ca", "etc", "a.k.a", "ibid",
    # Pointers into the paper or a reference: figures, tables, equations,
    # references, sections, supplements, chapters, volumes, pages, numbers.
    "fig", "figs", "tab", "tabs", "eq", "eqs", "ref", "refs", "sect", "suppl",
    "supp", "ch", "chap", "vol", "vols", "pp", "no", "nos", "cat",
    # Measures and quantities written with a period ("approx. 3 mg", "mol. wt.").
    "approx", "conc", "temp", "wt", "mol", "dil",
    # Dosing routes and frequencies ("injected i.v. into", "given b.i.d. for").
    "i.v", "i.p", "i.m", "s.c", "p.o", "b.i.d", "t.i.d", "q.i.d", "q.d",
    # Taxonomy below the genus ("Bacillus sp. strain", "subsp. enterica").
    "sp", "spp", "subsp", "ssp", "var", "nov", "str",
    # Journal-title words as cited in full text ("Proc. Natl. Acad. Sci. U.S.A.",
    # "J. Biol. Chem.", "N. Engl. J. Med.").
    "proc", "natl", "acad", "sci", "biol", "chem", "biochem", "biophys", "med",
    "res", "clin", "int", "annu", "physiol", "immunol", "microbiol", "genet",
    "pharmacol", "neurosci", "endocrinol", "infect", "dis", "commun",
    "nucl", "soc", "assoc", "exp", "pathol", "oncol", "epidemiol", "virol",
    "hum", "nat", "eur", "br", "engl", "ann", "intern",
    # Institutions, companies, places ("Sigma Chemical Co. (St. Louis, MO)",
    # "Dept. of", "U.S. cohort").
    "inc", "ltd", "co", "corp", "univ", "dept", "inst", "st", "mt", "ft",
    "u.s", "u.k", "u.s.a",
    # Months as abbreviated in citations and dates ("Sept. 2019"; "nov" is
    # listed under taxonomy).
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "dec",
})

_SENTENCE_END = re.compile(r"(?P<punct>[.!?]+)(?P<close>[\"')\]]*)\s+|\n{2,}")

# The Punkt token before a period: the run of characters that Punkt's word
# tokenizer keeps in one token (it splits brackets, quotes and these marks off
# as tokens of their own), with the separators a token cannot start with removed.
_TOKEN_BEFORE_PERIOD = re.compile(r"[^\s()\[\]{}\"'`;:*@!?]*\Z")
# Punkt's own numeric-token pattern (``PunktToken._RE_NUMERIC``), on the token
# including its trailing period.
_PUNKT_NUMERIC = re.compile(r"^-?[\.,]?\d[\d,\.-]*\.?$")
# A paragraph break: two newlines with only horizontal whitespace between.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t\r\f\v]*\n")
# A single period (optionally closed by quotes/brackets) followed by whitespace
# that contains a paragraph break; group "end" is where the next paragraph starts.
_PERIOD_THEN_PARAGRAPH = re.compile(
    r"(?<!\.)\.(?P<close>[\"')\]]*)[ \t\r\f\v]*\n[ \t\r\f\v]*\n\s*")


#: How far back :func:`_token_before` looks. Far longer than any abbreviation,
#: and it keeps each lookup O(1) on multi-megabyte documents.
_TOKEN_WINDOW = 64


def _token_before(text: str, period_at: int) -> str:
    """The Punkt token that ends at the period ``text[period_at]``, without it.

    Only the last :data:`_TOKEN_WINDOW` characters are searched; a token that
    fills the whole window is longer than any abbreviation, so it is returned
    with a ``\x00`` prefix — never an abbreviation, initial or number, while
    its last hyphen-separated part still matches the way Punkt matches it."""
    lo = max(0, period_at - _TOKEN_WINDOW)
    tm = _TOKEN_BEFORE_PERIOD.search(text, lo, period_at)
    token = tm.group(0) if tm else ""
    if lo > 0 and tm is not None and tm.start() == lo:
        token = "\x00" + token
    return token.lstrip("-,&#")


def _is_abbreviation(token: str) -> bool:
    """Punkt's first-pass abbreviation test on a token without its final period:
    the token, or its last hyphen-separated part, lower-cased, is in
    :data:`SENTENCE_ABBREVIATIONS`."""
    low = token.lower()
    return low in SENTENCE_ABBREVIATIONS or low.split("-")[-1] in SENTENCE_ABBREVIATIONS


def _fallback_keeps_break(text: str, m: re.Match[str]) -> bool:
    """Whether the regex fallback keeps the candidate break ``m``.

    Mirrors what Punkt decides with :data:`SENTENCE_ABBREVIATIONS` and no
    trained orthographic context, so the two backends agree wherever the
    question is an abbreviation:

    * a single ``.`` after an abbreviation is never a break (Punkt has no
      orthographic evidence that would re-break it);
    * a single ``.`` after a lone letter (an initial, a genus abbreviation)
      followed by a letter is not a break ("E. coli", "J. Smith");
    * a single ``.`` after a number followed by a lower-case letter is not a
      break ("arm 2. received").

    ``?``, ``!``, ``..`` and paragraph breaks are kept exactly as before: a
    period followed by a paragraph break is always a break, whatever precedes it
    ("… the burden of MR.\n\nExercise …").
    """
    punct = m.group("punct")
    if punct != ".":
        return True  # "?", "!", "..", or a paragraph break (punct is None)
    if _PARAGRAPH_BREAK.search(text, m.end("close"), m.end()):
        return True
    token = _token_before(text, m.start("punct"))
    if not token:
        return True
    if _is_abbreviation(token):
        return False
    if m.group("close"):
        # Punkt's next token is then the closing mark, not a word: the
        # initial / number heuristics below do not fire.
        return True
    nxt = text[m.end()] if m.end() < len(text) else ""
    if len(token) == 1 and token.isalpha() and nxt.isalpha():
        return False
    if _PUNKT_NUMERIC.match(token + ".") and nxt.islower():
        return False
    return True


def _fallback_sentence_spans(text: str) -> list[tuple[int, int]]:
    """Regex sentence splitter used when NLTK/punkt is unavailable.

    Splits *after* sentence-ending punctuation (keeping the trailing whitespace
    attached to the sentence it follows) so the spans tile ``text`` exactly.
    A candidate break that :func:`_fallback_keeps_break` rejects (a period after
    an abbreviation, an initial, or a number before lower case) is skipped, so
    the fallback agrees with the Punkt backend on abbreviations.
    """
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        if not _fallback_keeps_break(text, m):
            continue
        end = m.end()
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


_PUNKT_TOKENIZER: object | None = None


def _punkt_tokenizer(nltk_module):
    """The shared Punkt tokenizer seeded with :data:`SENTENCE_ABBREVIATIONS`,
    built on first use. Built in code from ``PunktParameters`` — never loaded
    from NLTK's ``punkt`` data — so no data download is needed. Tokenizing does
    not mutate the parameters (only training does), so one instance is shared."""
    global _PUNKT_TOKENIZER
    if _PUNKT_TOKENIZER is None:
        params = nltk_module.tokenize.punkt.PunktParameters()
        params.abbrev_types = set(SENTENCE_ABBREVIATIONS)
        _PUNKT_TOKENIZER = nltk_module.tokenize.PunktSentenceTokenizer(params)
    return _PUNKT_TOKENIZER


def _punkt_sentence_spans(text: str) -> list[tuple[int, int]] | None:
    """NLTK Punkt sentence spans, or ``None`` if NLTK is unavailable.

    Uses a ``PunktSentenceTokenizer`` whose parameters are built in code —
    NLTK's defaults plus ``abbrev_types`` = :data:`SENTENCE_ABBREVIATIONS` — so
    it needs only ``nltk`` installed (the ``[chunking]`` extra) — no ``punkt``
    data download. The regex fallback (:func:`_fallback_sentence_spans`) is used
    only when ``nltk`` itself is not installed.

    Punkt's ``span_tokenize`` yields spans over the sentence tokens only,
    dropping the inter-sentence whitespace. We re-expand each span to start
    where the previous one ended so the result tiles ``text`` with no gaps —
    preserving every character for exact offset reconstruction.
    """
    try:
        import nltk  # lazy: only when the [chunking] extra is installed
    except ImportError:
        return None
    try:
        tokenizer = _punkt_tokenizer(nltk)
        raw = list(tokenizer.span_tokenize(text))
    except LookupError:
        # Defensive: some NLTK builds load punkt data; fall back if it's absent.
        return None
    except Exception:
        return None
    if not raw:
        return []
    # Tile gaplessly: each sentence runs from the previous sentence's end up to
    # (but not including) the next sentence's start; the last one runs to
    # end-of-text. This mirrors the upstream span->sentence expansion while
    # guaranteeing full coverage of ``text``.
    starts = [s for s, _ in raw[1:]]
    starts.extend(_abbreviation_paragraph_breaks(text))
    bounds = sorted({b for b in starts if 0 < b < len(text)})
    edges = [0, *bounds, len(text)]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def _abbreviation_paragraph_breaks(text: str) -> list[int]:
    """Sentence starts Punkt drops because an abbreviation ends a paragraph.

    Punkt knows no paragraphs: once ``etc``/``MR``-as-``mr`` is an abbreviation
    it would join "… back pain, etc.\n\nTherefore …" across the paragraph
    break, which bare Punkt (every period a candidate) never did. A period
    after an abbreviation followed by a paragraph break stays a break — the
    same rule the regex fallback applies (:func:`_fallback_keeps_break`)."""
    out: list[int] = []
    for m in _PERIOD_THEN_PARAGRAPH.finditer(text):
        if m.end() < len(text) and _is_abbreviation(_token_before(text, m.start())):
            out.append(m.end())
    return out


# A span longer than this (chars) with no sentence break gets sub-split on
# secondary separators (see :func:`_subsplit_long_span`). Well above any normal
# prose sentence, so ordinary text is never touched — only no-punctuation blobs
# (data tables, dumps) that Punkt/regex return as one giant span.
_LONG_SPAN_CHARS = 2000

# Secondary separators, tried in order of preference: paragraph/line breaks, then
# tabs, then semicolons, then any run of whitespace. Each keeps its trailing
# separator run attached to the piece before it so the sub-spans tile losslessly.
_SEPARATORS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\n+"),
    re.compile(r"\t+"),
    re.compile(r";+\s*"),
    re.compile(r"\s+"),
)


def _subsplit_span(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Break the long span ``text[start:end]`` on the first separator that yields
    more than one piece, preferring stronger separators (newline > tab > ``;`` >
    whitespace). Returns sub-spans that tile ``[start, end)`` gaplessly — each ends
    at (and includes) the separator run that follows it, so concatenation is exact.
    Returns ``[(start, end)]`` unchanged when no separator splits it."""
    segment = text[start:end]
    for sep in _SEPARATORS:
        cuts = [m.end() for m in sep.finditer(segment)]
        # A trailing separator at the very end doesn't create a new piece.
        cuts = [c for c in cuts if c < len(segment)]
        if not cuts:
            continue
        spans: list[tuple[int, int]] = []
        prev = 0
        for c in cuts:
            spans.append((start + prev, start + c))
            prev = c
        spans.append((start + prev, end))
        return spans
    return [(start, end)]


def _subsplit_long_spans(
    text: str, spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Sub-split any span longer than ``_LONG_SPAN_CHARS`` on secondary separators.

    Normal prose (sentences well under the threshold) passes through untouched, so
    behaviour for ordinary documents is byte-identical. A no-punctuation blob that
    sentence detection returned as one giant span becomes many separator-delimited
    units. The result still tiles ``text`` exactly (offsets stay honest for the
    deterministic chunk ids)."""
    out: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start > _LONG_SPAN_CHARS:
            out.extend(_subsplit_span(text, start, end))
        else:
            out.append((start, end))
    return out


def _segmenter_version_for(punkt_available: bool) -> str:
    """The recorded segmenter name for a backend: :data:`SEGMENTER_VERSION`
    for Punkt, :data:`REGEX_SEGMENTER_VERSION` for the regex fallback."""
    return SEGMENTER_VERSION if punkt_available else REGEX_SEGMENTER_VERSION


_SEGMENTER_VERSION_CACHE: str | None = None


def segmenter_version() -> str:
    """The segmenter this process's :func:`sentence_spans` actually runs —
    ``"punkt-abbrev-v1"`` when the Punkt backend works here, else
    ``"regex-abbrev-v1"``. This, not the constant :data:`SEGMENTER_VERSION`, is
    what every record writes (#815).

    The probe is the one :func:`ragstack.provenance.sentence_spans_fingerprint`
    uses for its ``backend`` field — ``_punkt_sentence_spans("A b. C d.") is not
    None`` — run once and cached: whether ``nltk`` imports does not change
    within a process. (Tests that hide ``nltk`` reset
    ``_SEGMENTER_VERSION_CACHE``.)"""
    global _SEGMENTER_VERSION_CACHE
    if _SEGMENTER_VERSION_CACHE is None:
        _SEGMENTER_VERSION_CACHE = _segmenter_version_for(
            _punkt_sentence_spans("A b. C d.") is not None)
    return _SEGMENTER_VERSION_CACHE


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return gapless sentence spans into ``text`` (NLTK Punkt, else regex).

    After sentence detection, very long spans (a no-punctuation data table that
    yields one giant span) are sub-split on secondary separators (newline, tab,
    ``;``, whitespace) via :func:`_subsplit_long_spans` so downstream chunking
    isn't handed a single multi-million-char unit. Normal prose is unchanged and
    the spans still tile ``text`` exactly.
    """
    spans = _punkt_sentence_spans(text)
    if spans is None:
        _warn_regex_fallback_once()
        spans = _fallback_sentence_spans(text)
    return _subsplit_long_spans(text, spans)


_FALLBACK_WARNED = False


def _warn_regex_fallback_once() -> None:
    """Log, once per process, that :func:`sentence_spans` is on the regex
    fallback — a different segmentation from the Punkt one every study, golden
    and dev environment measured (#815), recorded as
    :data:`REGEX_SEGMENTER_VERSION`.

    Deliberately a warning, not a refusal: whether a production worker/API
    without ``nltk`` should refuse sentence-based chunk methods
    (refuse-don't-degrade) is the owner's policy decision, tracked in #815."""
    global _FALLBACK_WARNED
    if _FALLBACK_WARNED:
        return
    _FALLBACK_WARNED = True
    logger.warning(
        "sentence_spans: NLTK Punkt is unavailable; using the regex sentence "
        "fallback (segmenter_version=%s), a different segmentation from the "
        "Punkt one (%s) the sentence/semantic chunkers were measured with. "
        "Fix: pip install \"ragstack[chunking]\" (or rebuild the tools/server "
        "image, whose defs install the chunking extra). See #815.",
        REGEX_SEGMENTER_VERSION, SEGMENTER_VERSION)


class SentenceChunker:
    """Group whole sentences into chunks of ~``chunk_size`` chars with overlap.

    Sentence boundaries come from NLTK Punkt when the ``chunking`` extra is
    installed, otherwise a regex fallback. Char spans are tracked from the
    sentence spans straight through to the chunk, so no chunk ever splits a
    sentence and ``start_char``/``end_char`` reconstruct the source exactly.

    ``chunk_size == -1`` disables chunking (the whole document is one chunk).

    ``size_unit`` says what ``chunk_size`` counts (see :data:`SIZE_UNITS`):
    ``"chars"`` (default) or ``"tokens"`` (requires a ``token_counter``).
    ``chunk_overlap`` is chars in both. When ``max_tokens`` + ``token_counter``
    are supplied, ``max_tokens`` is a cap in both units: no chunk exceeds it, and
    a sentence is split only when it alone is over the cap (see
    :func:`_pack_spans`). ``budget_mode`` (``size_unit="tokens"`` only) selects
    how a chunk is measured against the token budget — ``"joined"`` (default:
    fill to the budget on the joined text, then cut back to a sentence boundary)
    or ``"summed"`` (legacy, under-filling); see :func:`_pack_spans_tokens`.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        *,
        max_tokens: int | None = None,
        token_counter: TokenCounter | None = None,
        budget_mode: str = DEFAULT_BUDGET_MODE,
        size_unit: str = DEFAULT_SIZE_UNIT,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_tokens = max_tokens
        self.token_counter = token_counter
        self.budget_mode = _validate_budget_mode(budget_mode)
        self.size_unit = _validate_size_unit(size_unit, chunk_size, token_counter)

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        if not text:
            return []
        if self.chunk_size == -1:
            return _whole_doc(doc, self.max_tokens, self.token_counter)

        spans = sentence_spans(text)
        if not spans:
            return [_make_chunk(doc, 0, len(text))]

        return _pack_spans(
            doc, spans, self.chunk_size, self.chunk_overlap,
            max_tokens=self.max_tokens, token_counter=self.token_counter,
            budget_mode=self.budget_mode, size_unit=self.size_unit,
        )


# --------------------------------------------------------------------------- #
# Word splitting with char spans
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"\S+")


def word_spans(text: str) -> list[tuple[int, int]]:
    """Return spans of non-whitespace runs (words), keeping trailing whitespace
    attached to each word so the spans tile ``text`` gaplessly."""
    matches = list(_WORD.finditer(text))
    if not matches:
        return []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for i in range(len(matches)):
        next_start = matches[i + 1].start() if i < len(matches) - 1 else len(text)
        spans.append((cursor, next_start))
        cursor = next_start
    return spans


class WordChunker:
    """Group whole words into chunks of ~``chunk_size`` chars with overlap.

    Word boundaries are non-whitespace runs; trailing whitespace stays attached
    so spans tile the source and offsets reconstruct it exactly. ``chunk_size
    == -1`` disables chunking.

    ``size_unit``, ``max_tokens`` and ``budget_mode`` mean what they do on
    :class:`SentenceChunker`, with words as the unit: ``max_tokens`` is a cap,
    and a word is split only when it alone is over it. ``budget_mode="summed"``
    under-fills a word-unit token chunk to ~0.68 of the budget; see
    :func:`_pack_spans_tokens`.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        *,
        max_tokens: int | None = None,
        token_counter: TokenCounter | None = None,
        budget_mode: str = DEFAULT_BUDGET_MODE,
        size_unit: str = DEFAULT_SIZE_UNIT,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_tokens = max_tokens
        self.token_counter = token_counter
        self.budget_mode = _validate_budget_mode(budget_mode)
        self.size_unit = _validate_size_unit(size_unit, chunk_size, token_counter)

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        if not text:
            return []
        if self.chunk_size == -1:
            return _whole_doc(doc, self.max_tokens, self.token_counter)

        spans = word_spans(text)
        if not spans:
            return [_make_chunk(doc, 0, len(text))]

        return _pack_spans(
            doc, spans, self.chunk_size, self.chunk_overlap,
            max_tokens=self.max_tokens, token_counter=self.token_counter,
            budget_mode=self.budget_mode, size_unit=self.size_unit,
        )


def _whole_doc(
    doc: Document, max_tokens: int | None, token_counter: TokenCounter | None
) -> list[Chunk]:
    """The whole document as one logical chunk (``chunk_size == -1`` / disable
    chunking). With a token budget set, still token-split it so a chunk can't
    exceed the window (a long doc becomes its minimal set of <=budget pieces)."""
    if max_tokens is not None and token_counter is not None:
        return _token_split_span(doc, 0, len(doc.content), max_tokens, token_counter)
    return [_make_chunk(doc, 0, len(doc.content))]


def _overlap_resume(
    spans: list[tuple[int, int]], i: int, j: int, chunk_overlap: int
) -> int:
    """Start-unit index for the next chunk (sliding-window overlap): walk back from
    ``j`` accumulating whole trailing units until their combined char length would
    exceed ``chunk_overlap``, then resume there. Always advances by at least one
    unit past ``i`` so a large overlap can't loop. ``chunk_overlap <= 0`` disables
    overlap (resume at ``j``). Shared by the char- and token-budget packers."""
    if chunk_overlap <= 0:
        return j
    overlap = 0
    k = j
    while k > i + 1:
        prev_len = spans[k - 1][1] - spans[k - 1][0]
        if overlap + prev_len > chunk_overlap:
            break
        overlap += prev_len
        k -= 1
    return k if k > i else i + 1


def _pack_spans(
    doc: Document,
    spans: list[tuple[int, int]],
    chunk_size: int,
    chunk_overlap: int,
    *,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    budget_mode: str = DEFAULT_BUDGET_MODE,
    size_unit: str = DEFAULT_SIZE_UNIT,
) -> list[Chunk]:
    """Greedily pack consecutive unit spans (sentences/words) into chunks, then
    start the next chunk with a tail of units (sliding-window overlap).

    ``chunk_size`` is always the size; ``size_unit`` says what it counts:

    - ``size_unit="chars"`` (default): pack whole units to ``chunk_size`` chars —
      byte-for-byte the original char packing. When ``max_tokens`` +
      ``token_counter`` are set, ``max_tokens`` is a **cap**, not the size: a
      char-packed chunk whose joined token count exceeds it is re-packed over its
      OWN unit spans with the joined-mode token packer (no overlap inside: the
      pieces tile that chunk), so a cut falls inside a unit only when that one
      unit alone exceeds the cap. The outer overlap resume then continues from
      the char-packed chunk's end, exactly as without a cap. A chunk that fits
      the cap is emitted unchanged, so a cap larger than every chunk changes
      nothing. ``budget_mode`` does not apply here (the cap re-pack is always
      ``"joined"``).
    - ``size_unit="tokens"``: the budget is ``chunk_size`` tokens, capped at
      ``max_tokens`` when that is set. It is ONE budget for both of the token
      packer's jobs — the fill limit and the oversized-single-unit hard split —
      so a unit longer than ``chunk_size`` is split to ``chunk_size``, not
      emitted whole because it happens to fit the (much larger) model window.
      ``budget_mode`` selects the fill (see :func:`_pack_spans_tokens`).

    (Before #318 a token budget with ``size_unit`` unset packed to ``max_tokens``
    and ignored ``chunk_size`` entirely; that behaviour is removed — no deployed
    sentence/words collection was built with it, and keeping it would have spread
    it to every in-process collection once the API resolves ``max_tokens``.)

    Because units are consecutive and tile the source, each chunk's span is
    ``(units[first].start, units[last].end)`` — a contiguous char range that
    reconstructs that slice of the source exactly. Overlap re-emits earlier
    units, so chunk ranges may overlap (and their ids differ by start/end).
    """
    if size_unit == "tokens":
        if token_counter is None:
            raise ValueError("size_unit='tokens' requires a token_counter")
        budget = chunk_size if max_tokens is None else min(chunk_size, max_tokens)
        return _pack_spans_tokens(
            doc, spans, budget, chunk_overlap, token_counter,
            budget_mode=budget_mode,
        )

    capped = max_tokens is not None and token_counter is not None
    text = doc.content
    chunks: list[Chunk] = []
    n = len(spans)
    i = 0
    while i < n:
        cur_start = spans[i][0]
        j = i
        size = 0
        # Always take at least one unit so a single oversized unit still emits.
        while j < n:
            unit_len = spans[j][1] - spans[j][0]
            if j > i and size + unit_len > chunk_size:
                break
            size += unit_len
            j += 1
        end = spans[j - 1][1]
        if capped:
            assert token_counter is not None and max_tokens is not None  # narrow for mypy
            chunks.extend(
                _cap_unit_range(doc, spans, i, j, max_tokens, token_counter, text)
            )
        else:
            chunks.append(_make_chunk(doc, cur_start, end))
        if j >= n:
            break
        i = _overlap_resume(spans, i, j, chunk_overlap)
    return chunks


def _cap_unit_range(
    doc: Document,
    spans: list[tuple[int, int]],
    lo: int,
    hi: int,
    max_tokens: int,
    token_counter: TokenCounter,
    text: str | None = None,
) -> list[Chunk]:
    """Emit units ``spans[lo:hi]`` as one chunk, or — if its joined text is over
    ``max_tokens`` — re-packed at UNIT boundaries to fit.

    The shared token-cap step for chunkers whose size is not tokens (char-sized
    ``sentence``/``words``, and semantic blocks): it never moves a boundary the
    caller chose unless the cap forces it, and when it does, the new boundaries
    are unit edges. The re-pack is :func:`_pack_spans_tokens` in joined mode with
    no overlap, so the pieces tile the range; a cut lands inside a unit only via
    its hard-split path, i.e. when that single unit alone exceeds ``max_tokens``.
    A single-unit range goes straight to :func:`_token_split_span` (nothing to
    respect; it returns the unit unchanged when it fits).
    """
    src = doc.content if text is None else text
    start, end = spans[lo][0], spans[hi - 1][1]
    if hi - lo == 1 or token_counter.count(src[start:end]) <= max_tokens:
        return _token_split_span(doc, start, end, max_tokens, token_counter)
    return _pack_spans_tokens(
        doc, spans[lo:hi], max_tokens, 0, token_counter, budget_mode="joined",
    )


def _balanced_unit_range(
    doc: Document,
    spans: list[tuple[int, int]],
    lo: int,
    hi: int,
    cap: int,
    token_counter: TokenCounter,
) -> list[Chunk]:
    """Emit units ``spans[lo:hi]`` as pieces of roughly EQUAL token size, each
    <= ``cap`` (``cap_split="balanced"``).

    A unit that alone exceeds ``cap`` is hard-split by tokens exactly as the
    greedy packer does, and it separates the block into runs; each run of
    fitting units that is itself over ``cap`` is cut into k pieces at unit
    boundaries, starting at k = ceil(joined tokens / cap). The pieces are chosen
    by :func:`_balanced_cuts` on the per-unit counts (min-max piece, then the
    largest smallest piece) and then VERIFIED on the joined text; when a piece
    does not fit, k is raised. k = number of units always fits (each piece is
    then a single unit, whose joined count IS its own count), so the loop ends
    and the never-exceed-``cap`` invariant is measured, not inferred. (k is
    clamped to the number of units; at k = n the units are emitted one per piece.)
    """
    text = doc.content
    tok = [token_counter.count(text[s:e]) for s, e in spans[lo:hi]]
    out: list[Chunk] = []
    run = lo
    for i in range(lo, hi):
        if tok[i - lo] > cap:
            if run < i:
                out.extend(_balanced_run(doc, spans, run, i, cap, token_counter, tok[run - lo : i - lo]))
            s, e = spans[i]
            out.extend(_token_split_span(doc, s, e, cap, token_counter))
            run = i + 1
    if run < hi:
        out.extend(_balanced_run(doc, spans, run, hi, cap, token_counter, tok[run - lo :]))
    return out


def _balanced_run(
    doc: Document,
    spans: list[tuple[int, int]],
    lo: int,
    hi: int,
    cap: int,
    token_counter: TokenCounter,
    weights: list[int],
) -> list[Chunk]:
    """One run of :func:`_balanced_unit_range`: every unit fits ``cap`` alone."""
    text = doc.content

    def joined(a: int, b: int) -> int:
        return token_counter.count(text[spans[a][0] : spans[b - 1][1]])

    total = joined(lo, hi)
    if total <= cap:
        return [_make_chunk(doc, spans[lo][0], spans[hi - 1][1])]
    n = hi - lo
    # Clamp to n: seams can make the joined count exceed the sum of the units'
    # own counts, so ceil(total / cap) may ask for more pieces than units.
    k = max(2, min(n, -(-total // cap)))
    while k < n:
        cuts = _balanced_cuts(weights, k, cap)
        if cuts is not None:
            pieces = [(lo + a, lo + b) for a, b in zip(cuts, cuts[1:], strict=False)]
            if all(joined(a, b) <= cap for a, b in pieces):
                return [_make_chunk(doc, spans[a][0], spans[b - 1][1]) for a, b in pieces]
        k += 1
    # k == n: one unit per piece. Each fits — its count was measured on exactly
    # this slice (``weights``) and every unit of a run is <= cap.
    return [_make_chunk(doc, s, e) for s, e in spans[lo:hi]]


def _balanced_cuts(weights: list[int], k: int, cap: int) -> list[int] | None:
    """Boundaries ``[0, c1, ..., n]`` cutting ``weights`` into exactly ``k``
    contiguous non-empty pieces, each summing to <= ``cap``, that (1) minimise the
    largest piece and then (2) maximise the smallest one; ``None`` if no such
    partition exists. Deterministic: ties keep the earliest cut.

    Two O(k * n * w) dynamic programmes over prefix sums (``w`` = units that fit
    in one piece), pure arithmetic — no token counting here.
    """
    n = len(weights)
    if k > n:
        return None
    prefix = [0]
    for w in weights:
        prefix.append(prefix[-1] + w)
    inf = float("inf")

    def fits_before(j: int, limit: float):
        """Start indices i < j with sum(weights[i:j]) <= limit, nearest first."""
        i = j - 1
        while i >= 0 and prefix[j] - prefix[i] <= limit:
            yield i
            i -= 1

    # (1) best[p][j]: min over partitions of weights[:j] into p pieces of the max piece.
    best = [[inf] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0
    for p in range(1, k + 1):
        for j in range(p, n + 1):
            for i in fits_before(j, cap):
                if i < p - 1:
                    break
                cand = max(best[p - 1][i], prefix[j] - prefix[i])
                if cand < best[p][j]:
                    best[p][j] = cand
    limit = best[k][n]
    if limit == inf:
        return None
    # (2) among partitions whose pieces are all <= limit, maximise the smallest.
    low = [[-inf] * (n + 1) for _ in range(k + 1)]
    back = [[-1] * (n + 1) for _ in range(k + 1)]
    low[0][0] = inf
    for p in range(1, k + 1):
        for j in range(p, n + 1):
            for i in fits_before(j, limit):
                if i < p - 1:
                    break
                if low[p - 1][i] == -inf:
                    continue
                cand = min(low[p - 1][i], prefix[j] - prefix[i])
                if cand > low[p][j] or (cand == low[p][j] and i < back[p][j]):
                    low[p][j] = cand
                    back[p][j] = i
    if low[k][n] == -inf:  # pragma: no cover — (1) found one at this limit
        return None
    cuts = [n]
    j = n
    for p in range(k, 0, -1):
        j = back[p][j]
        cuts.append(j)
    return cuts[::-1]


def _furthest_fitting_unit(
    i: int, n: int, max_tokens: int, joined_of: Callable[[int, int], int]
) -> int:
    """Largest ``j`` in ``(i, n]`` whose joined text ``units[i:j]`` fits the budget.

    ``joined_of(lo, hi)`` returns the token count of the joined text of units
    ``[lo, hi)``; ``joined_of(i, i + 1)`` is assumed already known to fit (the
    caller hard-splits an over-budget single unit before calling).

    Gallops upward from ``i + 1`` doubling the step while the joined text still
    fits, then binary-searches the last fitting boundary between the largest
    known-fitting ``lo`` and the smallest known-overflowing ``hi``. That is
    O(log k) counter calls for a chunk of k units, each on text no larger than
    ~2x the budget — versus the legacy packer's O(k) per-unit calls — and it never
    returns a ``j`` whose joined count was not measured, which is what keeps the
    never-exceed-budget invariant exact rather than inferred.

    Token counts of growing prefixes are monotone in practice but not guaranteed
    to be by any tokenizer contract; a non-monotone counter can only cost fill
    here (an earlier boundary is returned), never the invariant.
    """
    lo = i + 1  # largest known-fitting
    hi = -1  # smallest known-overflowing, -1 = none found
    step = 1
    while lo < n:
        cand = min(lo + step, n)
        if joined_of(i, cand) <= max_tokens:
            lo = cand
            if lo == n:
                return n
            step *= 2
        else:
            hi = cand
            break
    if hi < 0:
        return lo
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if joined_of(i, mid) <= max_tokens:
            lo = mid
        else:
            hi = mid
    return lo


def _pack_spans_tokens(
    doc: Document,
    spans: list[tuple[int, int]],
    max_tokens: int,
    chunk_overlap: int,
    token_counter: TokenCounter,
    *,
    budget_mode: str = DEFAULT_BUDGET_MODE,
) -> list[Chunk]:
    """Token-budget variant of :func:`_pack_spans`, in two fill modes.

    ``max_tokens`` here is the **packing budget** the caller resolved — the model
    window on the default path, ``min(chunk_size, window)`` under
    ``size_unit="tokens"``. It is deliberately one number for both the fill limit
    and the hard split below: splitting at a larger cap than the fill budget would
    let a single unit between the two through whole (#318 P1).

    ``budget_mode="joined"`` (**default**) implements the stated intent of
    word/sentence chunking: fill the chunk up to the token budget measured on the
    **joined** chunk text, then cut back to the nearest unit boundary, so a word
    (or sentence) is never split. The furthest fitting boundary is found by a
    galloping-then-binary search over the joined prefix count — O(log k) counter
    calls per chunk, and every emitted ``j`` is one whose joined count was
    *actually measured* at <= ``max_tokens``.

    ``budget_mode="summed"`` is the **legacy** fill: pack while the running sum of
    per-unit counts, each unit tokenized *in isolation*, stays within budget. That
    sum systematically over-counts the joined text, because a BPE tokenizer merges
    the space before a word into that word's token — a merge that a unit tokenized
    alone cannot see. Measured with the SFR tokenizer on real text, the per-WORD
    sum over-counts the joined chunk by 1.47-1.50x (realised fill ~0.68) and the
    per-SENTENCE sum by 1.00-1.04x (fill ~0.92-0.97). The mode is kept, and named
    honestly, because the chunking study's central exploratory claim is that
    *realised* chunk tokens — not nominal chunk size — explain retrieval quality,
    and the mis-filled arms are the only configurations where realised and nominal
    come apart. Deleting the behaviour would make that claim permanently fitted
    rather than testable; keeping it lets a later run reproduce both arms. It is
    also what makes the already-completed Leg A / Leg B grids interpretable.

    Both modes share the hard-split path (a single unit that alone exceeds the
    budget is split by tokens via :func:`_token_split_span` so no text is dropped)
    and the same invariant, asserted below on the measured count: **no emitted
    chunk exceeds ``max_tokens``**. Per-unit counts are memoized (``tok_of``), and
    in joined mode so are the joined-prefix counts, so overlap re-visits cost
    nothing.

    Overlap honours ``chunk_overlap`` **chars** in both modes, with the same
    semantics as the char path: after emitting a chunk, walk back from the end
    accumulating whole trailing units until their combined char length would
    exceed ``chunk_overlap``, then resume there. ``i`` always advances by at least
    one unit so a large overlap can't loop.
    """
    if budget_mode not in BUDGET_MODES:
        raise ValueError(
            f"unknown budget_mode {budget_mode!r}; valid: {', '.join(BUDGET_MODES)}"
        )
    text = doc.content
    n = len(spans)
    # Per-span token counts, memoized (a span is re-counted across overlap).
    span_tok: dict[int, int] = {}

    def tok_of(idx: int) -> int:
        if idx not in span_tok:
            s, e = spans[idx]
            span_tok[idx] = token_counter.count(text[s:e])
        return span_tok[idx]

    # Joined-mode memo: (first unit, one-past-last unit) → tokens of the joined
    # text. Keyed on the unit pair, so the overlap re-visit of an earlier prefix
    # is free.
    joined_tok: dict[tuple[int, int], int] = {}

    def joined_of(lo: int, hi: int) -> int:
        key = (lo, hi)
        if key not in joined_tok:
            joined_tok[key] = token_counter.count(text[spans[lo][0] : spans[hi - 1][1]])
        return joined_tok[key]

    chunks: list[Chunk] = []
    i = 0
    while i < n:
        # Single unit already over budget → hard-split it and advance. Counting
        # exactly text[spans[i][0]:spans[i][1]] is what makes the j == i + 1 base
        # case below already-verified: it is the same slice.
        if tok_of(i) > max_tokens:
            s, e = spans[i]
            chunks.extend(_token_split_span(doc, s, e, max_tokens, token_counter))
            i += 1
            continue
        joined_tok[(i, i + 1)] = span_tok[i]

        cur_start = spans[i][0]
        if budget_mode == "joined":
            j = _furthest_fitting_unit(i, n, max_tokens, joined_of)
            emitted = joined_of(i, j)
        else:
            # Legacy: grow by the memoized per-span running sum only (each span
            # counted once). The sum >= the joined count, so the chunk fits — it
            # just forgoes the seam-merge reclaim and under-fills.
            j = i + 1
            running = tok_of(i)
            while j < n:
                nxt = tok_of(j)
                if running + nxt > max_tokens:
                    break
                running += nxt
                j += 1
            emitted = running
        # Invariant, on the measured count (joined mode) or on the sum that upper-
        # bounds it (legacy): a packed chunk never exceeds the budget. The
        # hard-split path above is the only way an over-budget unit reaches a chunk.
        assert emitted <= max_tokens, (
            f"packed chunk of {emitted} tokens exceeds budget {max_tokens} "
            f"(units [{i},{j}), budget_mode={budget_mode!r})"
        )
        end = spans[j - 1][1]
        chunks.append(_make_chunk(doc, cur_start, end))
        if j >= n:
            break
        i = _overlap_resume(spans, i, j, chunk_overlap)
    return chunks


# --------------------------------------------------------------------------- #
# Fixed sliding-token-window chunking with char spans
# --------------------------------------------------------------------------- #
# A true token-SIZE chunker (not just a token CAP like RecursiveCharacterChunker's
# max_tokens path): it tokenizes the whole doc with the embedding model's HF fast
# tokenizer (``return_offsets_mapping=True``, ``add_special_tokens=False``), slides
# an N-token window advancing (N - overlap) tokens, and maps each window back to
# EXACT source char offsets via the offset map so chunk content is sliced from the
# source and the deterministic uuid5(doc_id:start:end) id is preserved. Ported from
# the eval harness's ``token_window_chunks`` (chunking_compare_7way.py).


class FixedTokenWindowChunker:
    """Sliding TOKEN-window chunker with exact source char offsets.

    Tokenizes the whole document with the embedding model's HF fast tokenizer
    (via the injected :class:`TokenCounter`), then slides a ``chunk_size``-token
    window advancing ``chunk_size - chunk_overlap`` tokens each step. Each window
    maps back to a contiguous char span ``[offs[i][0], offs[j-1][1])`` through the
    tokenizer's offset mapping, so chunk content is sliced from the source and the
    deterministic ``uuid5(doc_id:start:end)`` id is preserved exactly like the
    other chunkers.

    The emitted char span is trimmed so that *re-tokenizing the sliced substring*
    yields <= ``chunk_size`` tokens: a substring re-encoded in isolation can gain a
    token vs. its in-context tokenization (a leading/merge token), so a naive window
    of exactly ``chunk_size`` tokens could re-count as N+1. The window is shrunk by
    whole tokens until the re-counted content fits, and the next window advances from
    the *trimmed* end so a trimmed-off token is never skipped (no source-text loss,
    even at ``chunk_overlap=0``). The one case the trim can't fix is a single token
    that alone re-counts > ``chunk_size`` (an indivisible long token): it is emitted
    as-is and left to the ingest embed path's isolate-and-drop backstop.

    Requires an HF ``TokenCounter`` (it needs the fast tokenizer's offset
    mapping) — a non-HF counter is rejected at construction (see ``__init__``),
    because silently degrading to a single whole-doc chunk per document would be an
    invisible corpus-wide chunking regression. ``chunk_size``/``chunk_overlap`` are
    **tokens**, unlike the char-based chunkers.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        *,
        token_counter: TokenCounter | None = None,
    ) -> None:
        # fixed_token needs the HF fast tokenizer's offset mapping. A non-HF counter
        # (estimate/endpoint) has no offset map and would yield ONE whole-doc chunk
        # per document: a silent, corpus-wide chunking regression. Fail fast at
        # construction instead of degrading. make_token_counter('hf') no longer
        # substitutes a counter behind the caller's back, so this now guards only a
        # caller that passed an inexact counter itself — it stays because that is
        # still reachable, and this is the last line of defence.
        tok = getattr(token_counter, "_tokenizer", None)
        if not callable(tok):
            raise ValueError(
                "FixedTokenWindowChunker requires an HF TokenCounter with a fast "
                f"tokenizer (offset mapping); got {type(token_counter).__name__}"
            )
        assert token_counter is not None  # a None counter has no callable _tokenizer
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.token_counter: TokenCounter = token_counter
        self._get_tokenizer = tok

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        if not text:
            return []
        counter = self.token_counter
        tokenizer = self._get_tokenizer()
        enc = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
        offsets = enc["offset_mapping"]
        n = len(offsets)
        if n == 0:
            return [_make_chunk(doc, 0, len(text))]
        window = max(1, self.chunk_size)
        overlap = max(0, min(self.chunk_overlap, window - 1))
        chunks: list[Chunk] = []
        start_tok = 0
        while start_tok < n:
            end_tok = min(start_tok + window, n)
            char_start = offsets[start_tok][0]
            char_end = offsets[end_tok - 1][1]
            # Only a FULL window (== `window` tokens) can re-count over budget; a
            # short tail window never can, so skip the re-tokenizing count() there.
            if end_tok - start_tok >= window:
                # Trim by whole tokens until the re-tokenized slice is <= window: an
                # isolated boundary re-encode can add one merge token. The guard
                # stops at a single token (end_tok - 1 == start_tok): a lone token
                # that itself re-counts > window is indivisible and emitted as-is —
                # the ingest embed path's isolate-and-drop backstop handles that.
                while (
                    end_tok - 1 > start_tok
                    and counter.count(text[char_start:char_end]) > window
                ):
                    end_tok -= 1
                    char_end = offsets[end_tok - 1][1]
            # start_tok strictly increases each step, so char_start does too — spans
            # never repeat; only skip a zero-width span.
            if char_end > char_start:
                chunks.append(_make_chunk(doc, char_start, char_end))
            if end_tok >= n:
                break
            # Advance from the (possibly trimmed) end, keeping `overlap` tokens of
            # context. Tying the step to end_tok — not a fixed window-overlap step —
            # means a trimmed window never skips its trimmed-off tokens (which would
            # silently drop source text when overlap is small). max(start_tok+1, …)
            # guarantees forward progress even if overlap spans the whole window.
            start_tok = max(start_tok + 1, end_tok - overlap)
        # A whitespace-only or degenerate tail can leave no chunk; never drop text.
        if not chunks:
            return [_make_chunk(doc, 0, len(text))]
        return chunks


# --------------------------------------------------------------------------- #
# Neighbor-link metadata
# --------------------------------------------------------------------------- #


def link_neighbors(chunks: list[Chunk]) -> None:
    """Stamp ``chunk_index`` / ``prev_chunk_id`` / ``next_chunk_id`` on an ORDERED
    document chunk list, in place.

    Given a document's final ordered chunks (after any token-cap splitting), set on
    every chunk's ``metadata``:

    - ``chunk_index`` — 0-based position in the document.
    - ``prev_chunk_id`` — the preceding chunk's ``chunk.id`` (the doc-level
      ``uuid5(doc_id:start:end)``), or ``None`` for the first chunk.
    - ``next_chunk_id`` — the following chunk's ``chunk.id``, or ``None`` for the
      last chunk.

    Uses the doc-level ``chunk.id``, NOT the tenant-prefixed store point id, so the
    links are stable across tenants and idempotent re-ingest. Call once per
    document on its final chunk list; the fields flow through to both the Qdrant
    payload and the ES document via ``Chunk.metadata``.
    """
    n = len(chunks)
    for i, c in enumerate(chunks):
        c.metadata["chunk_index"] = i
        c.metadata["prev_chunk_id"] = chunks[i - 1].id if i > 0 else None
        c.metadata["next_chunk_id"] = chunks[i + 1].id if i < n - 1 else None


def link_neighbors_by_document(chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    """Group ``chunks`` by ``doc_id`` (preserving order) and :func:`link_neighbors`
    each document's group, in place. Returns the per-``doc_id`` grouping so a caller
    that also needs it (e.g. per-document metrics) can reuse it instead of
    re-grouping the same list.

    Call this on the FINAL list of chunks that will actually be STORED — i.e. after
    embedding has dropped any unembeddable chunks — so a survivor's
    ``prev_chunk_id`` / ``next_chunk_id`` never dangles to a chunk that was
    quarantined and never written. Grouping by ``doc_id`` also keeps a mixed
    multi-document batch (both ingest paths flatten several docs together) from
    cross-linking one document's tail chunk to the next document's head.
    """
    groups: dict[str, list[Chunk]] = {}
    for c in chunks:
        groups.setdefault(c.doc_id, []).append(c)
    for group in groups.values():
        link_neighbors(group)
    return groups


# --------------------------------------------------------------------------- #
# Semantic chunking
# --------------------------------------------------------------------------- #


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """1 - cosine similarity, in pure Python (no numpy dependency)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return 1.0 - dot / (na * nb)


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (matches numpy.percentile default)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _mean_pool(vectors: Sequence[Sequence[float]]) -> list[float]:
    """Element-wise mean of a non-empty list of equal-length vectors (pure, exact
    for a fixed input order → deterministic)."""
    n = len(vectors)
    if n == 1:
        return list(vectors[0])
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for j in range(dim):
            acc[j] += v[j]
    return [x / n for x in acc]


class SemanticChunker:
    """Split a document at topic boundaries detected via embedding similarity.

    Algorithm (adapted from distllm / LlamaIndex):

    1. Split into sentences (with char spans).
    2. Build an overlapping ``buffer_size``-sentence buffer around each sentence.
    3. Embed every buffer via the injected ``embed_fn``.
    4. Cosine-distance consecutive buffers; place a breakpoint wherever the
       distance exceeds the ``breakpoint_percentile_threshold`` percentile.
    5. Join the sentences between breakpoints into chunks, merging chunks shorter
       than ``min_chunk_length`` into a neighbour so no text is dropped.

    The embedder is injected so the pipeline can hand the chunker the *same*
    embedder it uses everywhere else. ``chunk(doc)`` stays synchronous; the
    embed function is synchronous too (``api/deps.py`` bridges the async
    embedder). No text is ever dropped — chunk spans tile the source and offsets
    reconstruct it exactly.
    """

    def __init__(
        self,
        embed_fn: EmbedFn,
        buffer_size: int = 3,
        breakpoint_percentile_threshold: float = 80.0,
        min_chunk_length: int = 500,
        *,
        max_tokens: int | None = None,
        token_counter: TokenCounter | None = None,
        pool_sentences: bool = False,
        distance_round: int | None = None,
        breakpoint_max_tokens: int | None = None,
        breakpoint_token_counter: TokenCounter | None = None,
        max_breakpoint_sentences: int | None = 3000,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        size_cap_tokens: int | None = None,
        cap_split: str = DEFAULT_CAP_SPLIT,
    ) -> None:
        if cap_split not in CAP_SPLITS:
            raise ValueError(
                f"unknown cap_split {cap_split!r}; valid: {', '.join(CAP_SPLITS)}"
            )
        if size_cap_tokens is None and cap_split != DEFAULT_CAP_SPLIT:
            # A collection without a recorded cap is chunked as it always was;
            # a split strategy with nothing to split at would only look applied.
            raise ValueError(
                f"cap_split={cap_split!r} needs a size cap (size_cap='tokens'): "
                f"without one, semantic blocks are split only at the model window, "
                f"greedily, as before"
            )
        if size_cap_tokens is not None:
            if token_counter is None:
                raise ValueError(
                    "a semantic size cap (size_cap='tokens') requires a token_counter: "
                    "chunk_size is a token bound and there is nothing to count with"
                )
            if size_cap_tokens < 1:
                raise ValueError(
                    f"a semantic size cap needs chunk_size >= 1 token; got {size_cap_tokens}"
                )
            if max_tokens is not None and size_cap_tokens > max_tokens:
                raise ValueError(
                    f"semantic chunk_size={size_cap_tokens} tokens (size_cap='tokens') "
                    f"exceeds max_tokens={max_tokens} (the embedder's per-chunk token "
                    f"budget): a chunk that size would overflow the model context. "
                    f"Use chunk_size <= {max_tokens}."
                )
        #: The per-chunk token cap a recorded ``size_cap='tokens'`` sets
        #: (``chunk_size``), or ``None`` (legacy: only ``max_tokens`` caps).
        self.size_cap_tokens = size_cap_tokens
        self.cap_split = cap_split
        self.embed_fn = embed_fn
        self.buffer_size = buffer_size
        self.breakpoint_percentile_threshold = breakpoint_percentile_threshold
        self.min_chunk_length = min_chunk_length
        # When set, any semantic chunk over the token budget is split into
        # <=max_tokens pieces — at sentence boundaries (see _emit_block), cutting
        # inside a sentence only when one sentence alone is over budget — so no
        # chunk overflows the embedder context.
        self.max_tokens = max_tokens
        self.token_counter = token_counter
        # breakpoint_max_tokens: the context budget for the BREAKPOINT embed inputs
        # (buffers/sentences), used when the breakpoint model has a SMALLER window
        # than the stored model — e.g. BGE (512) doing boundary detection while SFR
        # (4096) stores chunks. Without it, a long sentence bounded only to the
        # stored budget would overflow the breakpoint model (HTTP 400). None →
        # falls back to max_tokens (single-model behaviour, unchanged).
        self.breakpoint_max_tokens = breakpoint_max_tokens
        # breakpoint_token_counter: count the breakpoint-embed inputs with the
        # BREAKPOINT model's own tokenizer. Critical when it differs from the stored
        # tokenizer — a BPE stored counter (e.g. Mistral) undercounts vs a wordpiece
        # breakpoint model (e.g. BGE/BERT), so counting with the stored tokenizer can
        # still overflow the breakpoint context. None → use token_counter.
        self.breakpoint_token_counter = breakpoint_token_counter
        # pool_sentences: embed each SENTENCE once and mean-pool the
        # (2*buffer_size+1)-sentence window into each buffer vector, instead of
        # embedding N overlapping buffer TEXTS. Same breakpoint math downstream,
        # but ~(2*buffer_size+1)x less embedding token work — each sentence is
        # embedded once rather than re-embedded inside every overlapping window.
        self.pool_sentences = pool_sentences
        # distance_round: round cosine distances to this many decimals before the
        # percentile threshold. A tiny low-bit float difference (across GPU/kernel
        # versions) could otherwise flip a distance across the threshold and change
        # a boundary; rounding makes the block boundaries reproducible cross-host.
        # None = legacy (no rounding, byte-identical to the pre-pooling path).
        self.distance_round = distance_round
        # max_breakpoint_sentences: OVERSIZED-DOC FALLBACK. Breakpoint detection
        # embeds one input PER SENTENCE SPAN (see _buffer_embeddings), so its embed
        # cost scales with the span count. A giant data-table doc can sub-split into
        # hundreds of thousands of spans (PR #79 made sentence_spans separator-aware),
        # and embedding all of them saturates the embedding fleet — a single doc can
        # stall every ingest shard. Semantic breakpoints add no value on such a doc
        # anyway. When a doc yields MORE than this many spans, skip the segmentation
        # embed entirely and chunk it with the deterministic fixed_token sliding
        # window instead (zero per-span embedding → O(n), bounded, fleet-safe).
        # None disables the fallback (always attempt semantic). Chosen default 3000:
        # comfortably above the corpus p99 of ~25k tokens (a 25k-token doc is only
        # ~1–2k sentence spans), so normal and even large prose stays semantic, while
        # the pathological tables that produce the fleet flood (tens/hundreds of
        # thousands of spans) fall back.
        self.max_breakpoint_sentences = max_breakpoint_sentences
        # Sliding-token-window params for the fallback chunker. The fallback reuses
        # FixedTokenWindowChunker, built lazily on first use (it requires an HF
        # TokenCounter with an offset map). If none is available, the fallback
        # degrades to a token-budget split over the whole doc (still O(n), lossless,
        # deterministic ids) rather than flooding the fleet. (With a recorded size
        # cap, make_chunker also passes chunk_size as size_cap_tokens, so the
        # fallback window and the cap are the same number of tokens.)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._fallback_chunker: FixedTokenWindowChunker | None = None
        self._fallback_built = False

    def _oversize_fallback(self, doc: Document, n_spans: int) -> list[Chunk]:
        """Chunk an oversized doc via the fixed_token machinery — no per-span embed.

        Prefers :class:`FixedTokenWindowChunker` (a real sliding token window with
        overlap); when no HF offset-tokenizer is available it degrades to a whole-doc
        token-budget split. Either path does ZERO breakpoint embedding, so cost is
        O(n) in the doc size and never touches the embedding fleet.
        """
        print(
            f"[semantic] oversized doc {doc.id!r}: {n_spans} spans "
            f"(> max_breakpoint_sentences={self.max_breakpoint_sentences}), "
            f"{len(doc.content)} chars — falling back to fixed_token (no breakpoint "
            f"embed).",
            file=sys.stderr,
        )
        if not self._fallback_built:
            self._fallback_built = True
            tok = getattr(self.token_counter, "_tokenizer", None)
            if self.token_counter is not None and callable(tok):
                self._fallback_chunker = FixedTokenWindowChunker(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    token_counter=self.token_counter,
                )
        if self._fallback_chunker is not None:
            return self._fallback_chunker.chunk(doc)
        # No HF offset tokenizer: token-budget split the whole doc (via the same
        # _token_split_span the other chunkers use). Bounded and lossless; without a
        # counter at all, _emit yields a single whole-doc chunk.
        return self._emit(doc, 0, len(doc.content))

    def _emit(self, doc: Document, start: int, end: int) -> list[Chunk]:
        """Emit a span with no sentence structure to respect, token-splitting it
        if over budget. Used for a single-sentence document and by the
        oversized-doc fallback; a multi-sentence semantic block goes through
        :meth:`_emit_block`, which never cuts inside a sentence that fits."""
        cap = self._block_cap
        if cap is None or self.token_counter is None:
            return [_make_chunk(doc, start, end)]
        return _token_split_span(doc, start, end, cap, self.token_counter)

    @property
    def _block_cap(self) -> int | None:
        """The token cap on an EMITTED chunk: the recorded size cap when there is
        one (already checked <= ``max_tokens``), else ``max_tokens``. Breakpoint
        inputs are bounded separately (:meth:`_cap_tokens`), by the window, so a
        size cap never moves a breakpoint."""
        return self.size_cap_tokens if self.size_cap_tokens is not None else self.max_tokens

    def _emit_block(
        self,
        doc: Document,
        spans: list[tuple[int, int]],
        lo: int,
        hi: int,
        distances: Sequence[float] | None = None,
    ) -> list[Chunk]:
        """Emit the semantic block of sentences ``spans[lo:hi]``, within budget.

        A block that fits ``max_tokens`` (or any block, with no budget) is one
        chunk, exactly as before. An over-budget block is re-packed at SENTENCE
        boundaries with the joined-mode token packer (no overlap: the pieces tile
        the block), so a cut lands inside a sentence only when that one sentence
        alone exceeds ``max_tokens`` — the packer's hard-split path. The block's
        spans are the ones breakpoint detection already used; nothing is
        re-segmented. Before this, the whole block went to
        :func:`_token_split_span`, which cuts wherever the token count runs out —
        mid-sentence, routinely.
        """
        cap = self._block_cap
        counter = self.token_counter
        if cap is None or counter is None:
            return [_make_chunk(doc, spans[lo][0], spans[hi - 1][1])]
        if self.cap_split == "greedy":
            # Same cap step as char-sized sentence/words (_cap_unit_range). The
            # legacy (no size cap) path is always this one, with cap = max_tokens.
            return _cap_unit_range(doc, spans, lo, hi, cap, counter)
        start, end = spans[lo][0], spans[hi - 1][1]
        if hi - lo == 1 or counter.count(doc.content[start:end]) <= cap:
            return _token_split_span(doc, start, end, cap, counter)
        if self.cap_split == "balanced":
            return _balanced_unit_range(doc, spans, lo, hi, cap, counter)
        if distances is None:  # pragma: no cover — chunk() always passes them
            raise ValueError("cap_split='topic' needs the breakpoint distances")
        return self._topic_split(doc, spans, lo, hi, cap, distances)

    def _topic_split(
        self,
        doc: Document,
        spans: list[tuple[int, int]],
        lo: int,
        hi: int,
        cap: int,
        distances: Sequence[float],
    ) -> list[Chunk]:
        """``cap_split="topic"``: cut the block at its strongest internal breakpoint,
        recursively, until every piece is <= ``cap`` tokens.

        ``distances[i]`` is the cosine distance between the buffers of sentences
        ``i`` and ``i + 1`` — the series breakpoint detection already computed
        (rounded, for ``semantic_pooled``), so this embeds nothing. Inside
        ``spans[a:b]`` the candidate cuts are before sentence ``m`` for ``a < m < b``,
        scored by ``distances[m - 1]``; ties go to the cut that best balances the
        two sides' characters, then to the earliest. A single sentence still over
        ``cap`` is hard-split by tokens, as under every strategy. Iterative (an
        explicit stack, left piece first), so a long block cannot hit the
        recursion limit and the pieces come out in document order.
        """
        assert self.token_counter is not None  # narrowed by _emit_block
        counter = self.token_counter
        text = doc.content
        out: list[Chunk] = []
        stack = [(lo, hi)]
        while stack:
            a, b = stack.pop()
            start, end = spans[a][0], spans[b - 1][1]
            if b - a == 1 or counter.count(text[start:end]) <= cap:
                out.extend(_token_split_span(doc, start, end, cap, counter))
                continue

            def score(m: int, a: int = a, start: int = start, end: int = end) -> tuple:
                at = spans[m][0]
                return (distances[m - 1], -abs((at - start) - (end - at)), -m)

            cut = max(range(a + 1, b), key=score)
            stack.append((cut, b))
            stack.append((a, cut))
        return out

    def _cap_tokens(self, s: str) -> str:
        """Bound ``s`` to the BREAKPOINT embedder's token budget (first lossless
        piece) so a long buffer/sentence can't exceed its context window (HTTP 400).
        Uses breakpoint_max_tokens when set (a smaller breakpoint model), else
        max_tokens, and the breakpoint model's own tokenizer when given (so a
        tokenizer mismatch can't undercount). Only the similarity input is bounded —
        never the emitted chunk text. No-op without a budget/counter (legacy)."""
        budget = self.breakpoint_max_tokens or self.max_tokens
        counter = self.breakpoint_token_counter or self.token_counter
        if budget is not None and counter is not None:
            if counter.count(s) > budget:
                return split_text_to_token_budget(s, budget, counter)[0]
        return s

    def _buffer_embeddings(
        self, text: str, spans: list[tuple[int, int]]
    ) -> Sequence[Sequence[float]]:
        """Per-sentence buffer vectors that drive breakpoint distances.

        Legacy (``pool_sentences=False``): embed each overlapping buffer TEXT (a
        window of up to ``2*buffer_size+1`` sentences) — byte-identical to the
        pre-pooling behaviour.

        Pooled (``pool_sentences=True``): embed each SENTENCE once, then mean-pool
        the same window of adjacent sentence vectors. One embed call either way
        (the sync bridge fans it out), but the pooled inputs are single sentences —
        ~(2*buffer_size+1)x fewer tokens embedded, and the pooling is deterministic
        so the resulting blocks stay reproducible."""
        n = len(spans)
        if self.pool_sentences:
            sent_vecs = self.embed_fn([self._cap_tokens(text[s:e]) for s, e in spans])
            pooled: list[list[float]] = []
            for i in range(n):
                lo = max(0, i - self.buffer_size)
                hi = min(i + 1 + self.buffer_size, n)
                pooled.append(_mean_pool(sent_vecs[lo:hi]))
            return pooled
        buffers: list[str] = []
        for i in range(n):
            lo = max(0, i - self.buffer_size)
            hi = min(i + 1 + self.buffer_size, n)
            buffers.append(self._cap_tokens(text[spans[lo][0] : spans[hi - 1][1]]))
        return self.embed_fn(buffers)

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        if not text:
            return []

        spans = sentence_spans(text)
        if not spans:
            return self._emit(doc, 0, len(text))
        # A single sentence can't have an internal boundary.
        if len(spans) == 1:
            return self._emit(doc, 0, len(text))

        # OVERSIZED-DOC FALLBACK (before the segmentation embed): breakpoint
        # detection embeds one input per span, so a doc that sub-split into a huge
        # number of spans would trigger an embed burst that saturates the fleet.
        # Fall back to fixed_token instead — no per-span embedding at all.
        if (
            self.max_breakpoint_sentences is not None
            and len(spans) > self.max_breakpoint_sentences
        ):
            return self._oversize_fallback(doc, len(spans))

        embeddings = self._buffer_embeddings(text, spans)

        distances = [
            _cosine_distance(embeddings[i], embeddings[i + 1])
            for i in range(len(embeddings) - 1)
        ]
        if self.distance_round is not None:
            distances = [round(d, self.distance_round) for d in distances]

        # Index groups over sentence indices: [start, end) per chunk.
        groups = self._breakpoint_groups(distances, len(spans))

        # Map sentence-index groups to contiguous char spans.
        chunk_spans: list[tuple[int, int]] = [
            (spans[s][0], spans[e - 1][1]) for s, e in groups if e > s
        ]
        chunk_spans = self._merge_short(chunk_spans)
        # _merge_short only extends ranges between existing sentence edges, so
        # every chunk span maps back to a sentence-index range [lo, hi). First
        # span starting at a char / last span ending at it, so a (degenerate)
        # zero-width span can never invert the range.
        first_of: dict[int, int] = {}
        past_of: dict[int, int] = {}
        for i, (s, e) in enumerate(spans):
            first_of.setdefault(s, i)
            past_of[e] = i + 1
        out: list[Chunk] = []
        for s, e in chunk_spans:
            out.extend(self._emit_block(doc, spans, first_of[s], past_of[e], distances))
        return out

    def _breakpoint_groups(
        self, distances: list[float], n_sentences: int
    ) -> list[tuple[int, int]]:
        """Return (start, end) sentence-index groups split at breakpoints."""
        if not distances:
            return [(0, n_sentences)]
        threshold = _percentile(distances, self.breakpoint_percentile_threshold)
        above = [i for i, d in enumerate(distances) if d > threshold]
        groups: list[tuple[int, int]] = []
        start = 0
        for idx in above:
            groups.append((start, idx + 1))
            start = idx + 1
        groups.append((start, n_sentences))
        return groups

    def _merge_short(self, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Merge chunks shorter than ``min_chunk_length`` into a neighbour so no
        text is dropped. Spans stay contiguous, so merging just extends a range.
        """
        if self.min_chunk_length <= 0 or len(spans) <= 1:
            return spans
        merged: list[tuple[int, int]] = []
        for s, e in spans:
            if merged and (e - s) < self.min_chunk_length:
                ps, _pe = merged[-1]
                merged[-1] = (ps, e)
            else:
                merged.append((s, e))
        # If the leading chunk is still short, fold it into the next.
        if len(merged) > 1 and (merged[0][1] - merged[0][0]) < self.min_chunk_length:
            merged[1] = (merged[0][0], merged[1][1])
            merged.pop(0)
        return merged


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

CHUNK_METHODS = ("fixed", "fixed_token", "sentence", "words", "semantic", "semantic_pooled")


def make_chunker(
    method: str = "fixed",
    *,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    embed_fn: EmbedFn | None = None,
    buffer_size: int = 3,
    breakpoint_percentile_threshold: float = 80.0,
    min_chunk_length: int = 500,
    max_tokens: int | None = None,
    token_counter: TokenCounter | None = None,
    breakpoint_max_tokens: int | None = None,
    breakpoint_token_counter: TokenCounter | None = None,
    max_breakpoint_sentences: int | None = 3000,
    budget_mode: str = DEFAULT_BUDGET_MODE,
    size_unit: str = DEFAULT_SIZE_UNIT,
    size_cap: str | None = None,
    cap_split: str = DEFAULT_CAP_SPLIT,
):
    """Build the chunker named by ``method``.

    ``fixed`` → :class:`RecursiveCharacterChunker`, ``fixed_token`` →
    :class:`FixedTokenWindowChunker` (a sliding TOKEN window; ``chunk_size`` /
    ``chunk_overlap`` are tokens and it requires a ``token_counter``), ``sentence``
    → :class:`SentenceChunker`, ``words`` → :class:`WordChunker`, ``semantic`` →
    :class:`SemanticChunker` (which requires ``embed_fn``). The return type is
    the protocol :class:`ragstack.protocols.Chunker`; the concrete classes are
    not a common base, so it is left unannotated.

    When ``max_tokens`` + ``token_counter`` are passed, every method sizes/caps by
    tokens so no emitted chunk exceeds the embedder context. With them None the
    char-budget behaviour is unchanged (back-compat). ``fixed_token`` is the one
    method that is not *capped* by ``max_tokens`` — its window is the size — so a
    ``chunk_size`` larger than ``max_tokens`` raises ``ValueError`` instead.

    ``budget_mode`` (``sentence``/``words`` with ``size_unit="tokens"`` only):
    ``"joined"`` (default) fills to the budget measured on the joined chunk text
    and then cuts back to a unit boundary; ``"summed"`` reproduces the legacy
    under-filling sum-of-isolated-unit-counts packing. See
    :func:`_pack_spans_tokens`. Other methods ignore it.

    ``size_unit`` (``sentence``/``words`` only): what ``chunk_size`` counts — see
    :data:`SIZE_UNITS`. ``"chars"`` (default): ``chunk_size`` chars, with
    ``max_tokens`` as a cap. ``"tokens"``: ``chunk_size`` tokens, capped at
    ``max_tokens``. ``chunk_size`` is used either way. Other methods ignore it.

    ``max_breakpoint_sentences`` (semantic only): oversized-doc fallback threshold —
    a doc that sub-splits into more than this many sentence spans is chunked with the
    fixed_token sliding window instead of the (per-span) breakpoint embed, keeping
    ingest cost independent of document size. ``None`` disables the fallback.

    ``size_cap`` (semantic only): the collection's recorded size-cap marker, see
    :data:`SIZE_CAPS`. ``"tokens"`` makes ``chunk_size`` an upper bound, in
    tokens, on every semantic chunk — refused (``ValueError``) above
    ``max_tokens``, never clamped. ``None`` (default): the historical behaviour,
    ``chunk_size`` feeds only the oversized-doc fallback. ``cap_split`` (semantic,
    with ``size_cap`` only): how an over-cap block is split, see
    :data:`CAP_SPLITS`.
    """
    if cap_split not in CAP_SPLITS:
        raise ValueError(f"unknown cap_split {cap_split!r}; valid: {', '.join(CAP_SPLITS)}")
    if size_cap is not None and size_cap not in SIZE_CAPS:
        raise ValueError(f"unknown size_cap {size_cap!r}; valid: {', '.join(SIZE_CAPS)}")
    if method not in ("semantic", "semantic_pooled") and (
        size_cap is not None or cap_split != DEFAULT_CAP_SPLIT
    ):
        # Same rule as size_unit: a knob this method ignores must not look applied.
        raise ValueError(
            f"size_cap/cap_split apply only to the semantic methods, not "
            f"chunk_method={method!r}"
        )
    if size_unit not in SIZE_UNITS:
        # Checked for every method, not only the two that read it: a typo must not
        # be accepted just because this method happens to ignore the knob.
        raise ValueError(
            f"unknown size_unit {size_unit!r}; valid: {', '.join(SIZE_UNITS)}"
        )
    if method == "fixed":
        return RecursiveCharacterChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            max_tokens=max_tokens, token_counter=token_counter,
        )
    if method == "fixed_token":
        # chunk_size / chunk_overlap are TOKENS here; the window IS the cap so
        # max_tokens is not threaded (the chunker enforces <= chunk_size tokens).
        # That makes a window WIDER than max_tokens a request this method cannot
        # honour without breaking the other one — every chunk would overflow the
        # embedder context. Refuse it rather than silently clamp (a clamp would
        # re-size the collection behind the caller's back).
        if token_counter is None:
            raise ValueError("chunk_method='fixed_token' requires a token_counter")
        if max_tokens is not None and chunk_size > max_tokens:
            raise ValueError(
                f"chunk_method='fixed_token' with chunk_size={chunk_size} tokens "
                f"exceeds max_tokens={max_tokens} (the embedder's per-chunk token "
                f"budget): every full window would overflow the model context. "
                f"Use chunk_size <= {max_tokens}."
            )
        return FixedTokenWindowChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            token_counter=token_counter,
        )
    if method in ("sentence", "words"):
        cls = SentenceChunker if method == "sentence" else WordChunker
        return cls(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            max_tokens=max_tokens, token_counter=token_counter,
            budget_mode=budget_mode, size_unit=size_unit,
        )
    if method in ("semantic", "semantic_pooled"):
        if embed_fn is None:
            raise ValueError(f"chunk_method={method!r} requires an embed_fn")
        # semantic_pooled embeds each sentence once + mean-pools, and rounds
        # distances so the blocks are reproducible cross-host.
        #
        # NOT "the same chunker, cheaper". Measured on dev 2026-09-18 over
        # identical input (docs/plans/results/semantic-vs-pooled-2026-09-18.md):
        # the two share NO boundaries on documents that split, boundary-signal
        # rank correlation 0.4254, span Jaccard 0.111 — while each arm is
        # reproducible against itself (0.998/0.999), so the difference is
        # algorithmic, not fleet noise. They agree on chunk COUNT, which is how a
        # summary that counts chunks reports false agreement.
        #
        # Pooling is cheaper (4.47x fewer embed tokens at buffer_size=2, ~7x at
        # the default 3) AND it moves the boundaries. Choosing it to save tokens
        # also changes where the chunks are; chunk method is collection identity,
        # so that choice cannot be revisited in place.
        pooled = method == "semantic_pooled"
        return SemanticChunker(
            embed_fn=embed_fn,
            buffer_size=buffer_size,
            breakpoint_percentile_threshold=breakpoint_percentile_threshold,
            min_chunk_length=min_chunk_length,
            max_tokens=max_tokens, token_counter=token_counter,
            pool_sentences=pooled,
            distance_round=6 if pooled else None,
            breakpoint_max_tokens=breakpoint_max_tokens,
            breakpoint_token_counter=breakpoint_token_counter,
            max_breakpoint_sentences=max_breakpoint_sentences,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            size_cap_tokens=chunk_size if size_cap == SIZE_CAP_TOKENS else None,
            cap_split=cap_split,
        )
    raise ValueError(f"unknown chunk_method {method!r}; valid: {', '.join(CHUNK_METHODS)}")
