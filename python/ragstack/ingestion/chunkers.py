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

import math
import re
import sys
import uuid
from collections.abc import Callable, Sequence

from ragstack.ingestion.tokenization import TokenCounter
from ragstack.models import Chunk, Document

# A callable that turns a list of texts into a list of dense vectors. Sync on
# purpose: chunkers run synchronously inside the (already-async) ingestion
# pipeline, so the semantic chunker takes a plain sync embed function rather
# than the async Embedder protocol. ``api/deps.py`` bridges the configured
# async embedder into this shape.
EmbedFn = Callable[[Sequence[str]], Sequence[Sequence[float]]]

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

_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*\s+|\n{2,}")


def _fallback_sentence_spans(text: str) -> list[tuple[int, int]]:
    """Regex sentence splitter used when NLTK/punkt is unavailable.

    Splits *after* sentence-ending punctuation (keeping the trailing whitespace
    attached to the sentence it follows) so the spans tile ``text`` exactly.
    """
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _punkt_sentence_spans(text: str) -> list[tuple[int, int]] | None:
    """NLTK Punkt sentence spans, or ``None`` if NLTK is unavailable.

    Uses ``PunktSentenceTokenizer()`` with NLTK's built-in default parameters, so
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
        tokenizer = nltk.tokenize.PunktSentenceTokenizer()
        raw = list(tokenizer.span_tokenize(text))
    except LookupError:
        # Defensive: some NLTK builds load punkt data; fall back if it's absent.
        return None
    except Exception:
        return None
    if not raw:
        return []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for i in range(len(raw)):
        # Tile gaplessly: this sentence runs from the previous sentence's end up
        # to (but not including) the next sentence's start; the last one runs to
        # end-of-text. This mirrors the upstream span->sentence expansion while
        # guaranteeing full coverage of ``text``.
        next_start = raw[i + 1][0] if i < len(raw) - 1 else len(text)
        spans.append((cursor, next_start))
        cursor = next_start
    return spans


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
        spans = _fallback_sentence_spans(text)
    return _subsplit_long_spans(text, spans)


class SentenceChunker:
    """Group whole sentences into chunks of ~``chunk_size`` chars with overlap.

    Sentence boundaries come from NLTK Punkt when the ``chunking`` extra is
    installed, otherwise a regex fallback. Char spans are tracked from the
    sentence spans straight through to the chunk, so no chunk ever splits a
    sentence and ``start_char``/``end_char`` reconstruct the source exactly.

    ``chunk_size == -1`` disables chunking (the whole document is one chunk).

    When ``max_tokens`` + ``token_counter`` are supplied, packing is driven by the
    token budget instead of the char budget (whole sentences accumulated until the
    next would exceed ``max_tokens``), and any single over-budget sentence is
    hard-split by tokens. With ``max_tokens`` None the char-budget behaviour is
    unchanged.
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

    When ``max_tokens`` + ``token_counter`` are supplied, packing is driven by the
    token budget instead of the char budget, and any single over-budget word is
    hard-split by tokens. With ``max_tokens`` None the behaviour is unchanged.
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
) -> list[Chunk]:
    """Greedily pack consecutive unit spans (sentences/words) into chunks, then
    start the next chunk with a tail of units (sliding-window overlap).

    The packing *budget* is either characters (default) or tokens. When
    ``max_tokens`` + ``token_counter`` are given, a unit's "length" is its token
    count (memoized per span to limit counter calls) and a chunk grows until
    adding the next unit's tokens would exceed ``max_tokens``; the candidate
    combined chunk is then verified against the exact joined-token count, and if a
    single accumulated unit is itself over budget it's hard-split by tokens via
    :func:`_token_split_span`. With ``max_tokens`` None this is the original
    char-budget packing, byte-for-byte.

    Because units are consecutive and tile the source, each chunk's span is
    ``(units[first].start, units[last].end)`` — a contiguous char range that
    reconstructs that slice of the source exactly. Overlap re-emits earlier
    units, so chunk ranges may overlap (and their ids differ by start/end).
    """
    use_tokens = max_tokens is not None and token_counter is not None
    if use_tokens:
        assert token_counter is not None and max_tokens is not None  # narrow for mypy
        return _pack_spans_tokens(doc, spans, max_tokens, chunk_overlap, token_counter)

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
        chunks.append(_make_chunk(doc, cur_start, end))
        if j >= n:
            break
        i = _overlap_resume(spans, i, j, chunk_overlap)
    return chunks


def _pack_spans_tokens(
    doc: Document,
    spans: list[tuple[int, int]],
    max_tokens: int,
    chunk_overlap: int,
    token_counter: TokenCounter,
) -> list[Chunk]:
    """Token-budget variant of :func:`_pack_spans`.

    Packs whole units while their *memoized per-span* running token sum stays
    within ``max_tokens``. Each span is counted exactly once (via the ``tok_of``
    memo), so total tokenization is O(k) per chunk rather than O(k^2). The
    per-span sum *over*-counts the joined chunk (tokens that merge across a unit
    seam are double-counted), so packing by the sum already **guarantees** the
    emitted chunk is <= ``max_tokens`` — it just forgoes the seam-merge reclaim,
    yielding occasionally slightly smaller chunks. A single unit that alone
    exceeds the budget is hard-split by tokens so no text is dropped.

    Overlap honours ``chunk_overlap`` **chars** with the same semantics as the
    char path: after emitting a chunk, walk back from the end accumulating whole
    trailing units until their combined char length would exceed ``chunk_overlap``,
    then resume there. ``i`` always advances by at least one unit so a large
    overlap can't loop.
    """
    text = doc.content
    n = len(spans)
    # Per-span token counts, memoized (a span is re-counted across overlap).
    span_tok: dict[int, int] = {}

    def tok_of(idx: int) -> int:
        if idx not in span_tok:
            s, e = spans[idx]
            span_tok[idx] = token_counter.count(text[s:e])
        return span_tok[idx]

    chunks: list[Chunk] = []
    i = 0
    while i < n:
        # Single unit already over budget → hard-split it and advance.
        if tok_of(i) > max_tokens:
            s, e = spans[i]
            chunks.extend(_token_split_span(doc, s, e, max_tokens, token_counter))
            i += 1
            continue

        cur_start = spans[i][0]
        j = i + 1
        # Grow by the memoized per-span running sum only (each span counted once).
        # The sum >= the joined-token count, so staying <= max_tokens by the sum
        # keeps the joined chunk <= max_tokens without any per-step recount.
        running = tok_of(i)
        while j < n:
            nxt = tok_of(j)
            if running + nxt > max_tokens:
                break
            running += nxt
            j += 1
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
        # (estimate/endpoint) — including one that make_token_counter('hf') silently
        # fell back to because the tokenizer couldn't load — has no offset map and
        # would yield ONE whole-doc chunk per document: a silent, corpus-wide
        # chunking regression. Fail fast at construction instead of degrading.
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
    ) -> None:
        self.embed_fn = embed_fn
        self.buffer_size = buffer_size
        self.breakpoint_percentile_threshold = breakpoint_percentile_threshold
        self.min_chunk_length = min_chunk_length
        # When set, any semantic chunk over the token budget is split into
        # <=max_tokens pieces by tokens — replacing the harness's ad-hoc char cap
        # and guaranteeing no chunk overflows the embedder context.
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
        # deterministic ids) rather than flooding the fleet.
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
        """Emit a semantic chunk span, token-splitting it if over budget."""
        if self.max_tokens is None or self.token_counter is None:
            return [_make_chunk(doc, start, end)]
        return _token_split_span(doc, start, end, self.max_tokens, self.token_counter)

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
        out: list[Chunk] = []
        for s, e in chunk_spans:
            out.extend(self._emit(doc, s, e))
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
    char-budget behaviour is unchanged (back-compat).

    ``max_breakpoint_sentences`` (semantic only): oversized-doc fallback threshold —
    a doc that sub-splits into more than this many sentence spans is chunked with the
    fixed_token sliding window instead of the (per-span) breakpoint embed, keeping
    ingest cost independent of document size. ``None`` disables the fallback.
    """
    if method == "fixed":
        return RecursiveCharacterChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            max_tokens=max_tokens, token_counter=token_counter,
        )
    if method == "fixed_token":
        # chunk_size / chunk_overlap are TOKENS here; the window IS the cap so
        # max_tokens is not threaded (the chunker enforces <= chunk_size tokens).
        if token_counter is None:
            raise ValueError("chunk_method='fixed_token' requires a token_counter")
        return FixedTokenWindowChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            token_counter=token_counter,
        )
    if method == "sentence":
        return SentenceChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            max_tokens=max_tokens, token_counter=token_counter,
        )
    if method == "words":
        return WordChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            max_tokens=max_tokens, token_counter=token_counter,
        )
    if method in ("semantic", "semantic_pooled"):
        if embed_fn is None:
            raise ValueError(f"chunk_method={method!r} requires an embed_fn")
        # semantic_pooled embeds each sentence once + mean-pools (cheaper, GPU-
        # friendly) and rounds distances so the blocks are reproducible cross-host.
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
        )
    raise ValueError(f"unknown chunk_method {method!r}; valid: {', '.join(CHUNK_METHODS)}")
