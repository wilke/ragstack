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


def split_text_to_token_budget(
    text: str, max_tokens: int, token_counter: TokenCounter
) -> list[str]:
    """Split ``text`` into substrings each <= ``max_tokens`` tokens, lossless.

    The pieces concatenate back to ``text`` exactly (no characters added or
    dropped), so a caller mapping them onto char spans keeps offsets honest.

    Strategy: if the whole text already fits, return it unchanged. Otherwise grow
    a candidate prefix by binary search on character length up to the largest
    prefix that still fits ``max_tokens`` (token boundaries don't line up with
    char boundaries, so we search in char space and let the counter judge), emit
    it, and continue from there. A hard 1-char floor guarantees forward progress
    even for a pathological single character that the counter claims is over
    budget.
    """
    if not text or max_tokens <= 0:
        return [text] if text else []
    if token_counter.count(text) <= max_tokens:
        return [text]

    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        # Largest end in (start, n] whose slice fits the budget, by binary search.
        lo, hi = start + 1, n
        best = start + 1  # always make progress with at least one char
        while lo <= hi:
            mid = (lo + hi) // 2
            if token_counter.count(text[start:mid]) <= max_tokens:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        pieces.append(text[start:best])
        start = best
    return pieces


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


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return gapless sentence spans into ``text`` (NLTK Punkt, else regex)."""
    spans = _punkt_sentence_spans(text)
    if spans is None:
        spans = _fallback_sentence_spans(text)
    return spans


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
        if self.chunk_size == -1 and self.max_tokens is None:
            return [_make_chunk(doc, 0, len(text))]

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
        if self.chunk_size == -1 and self.max_tokens is None:
            return [_make_chunk(doc, 0, len(text))]

        spans = word_spans(text)
        if not spans:
            return [_make_chunk(doc, 0, len(text))]

        return _pack_spans(
            doc, spans, self.chunk_size, self.chunk_overlap,
            max_tokens=self.max_tokens, token_counter=self.token_counter,
        )


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
        # Overlap: walk back from j accumulating units until we'd exceed
        # chunk_overlap chars, then resume there. Always make forward progress
        # (i advances by at least one unit) so overlap >= chunk_size can't loop.
        if chunk_overlap > 0:
            overlap = 0
            k = j
            while k > i + 1:
                prev_len = spans[k - 1][1] - spans[k - 1][0]
                if overlap + prev_len > chunk_overlap:
                    break
                overlap += prev_len
                k -= 1
            i = k if k > i else i + 1
        else:
            i = j
    return chunks


def _pack_spans_tokens(
    doc: Document,
    spans: list[tuple[int, int]],
    max_tokens: int,
    overlap_units: int,
    token_counter: TokenCounter,
) -> list[Chunk]:
    """Token-budget variant of :func:`_pack_spans`.

    Packs whole units until the *exact* joined-token count of the candidate chunk
    would exceed ``max_tokens``. A per-span token cache keeps counter calls down;
    the joined candidate is counted exactly (per-span sums over-count because unit
    boundaries merge tokens), so the emitted chunk is guaranteed <= ``max_tokens``
    — except a single unit that alone exceeds the budget, which is hard-split by
    tokens so no text is dropped. Overlap is expressed in *whole trailing units*
    (``overlap_units`` is reinterpreted from the chars param: 0 disables overlap,
    any positive value re-emits one trailing unit) to keep boundaries on unit
    edges and offsets exact.
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
        # Grow while the next unit's token count *could* fit (cheap per-span gate),
        # then verify the exact joined count and back off if the merge tipped over.
        running = tok_of(i)
        while j < n:
            nxt = tok_of(j)
            if running + nxt > max_tokens:
                break
            # Exact check on the joined candidate (merging may add/remove a token
            # at the seam vs. the per-span sum).
            joined = token_counter.count(text[cur_start : spans[j][1]])
            if joined > max_tokens:
                break
            running = joined
            j += 1
        end = spans[j - 1][1]
        chunks.append(_make_chunk(doc, cur_start, end))
        if j >= n:
            break
        # Overlap by one whole trailing unit when requested; always advance.
        if overlap_units > 0 and j - 1 > i:
            i = j - 1
        else:
            i = j
    return chunks


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

    def _emit(self, doc: Document, start: int, end: int) -> list[Chunk]:
        """Emit a semantic chunk span, token-splitting it if over budget."""
        if self.max_tokens is None or self.token_counter is None:
            return [_make_chunk(doc, start, end)]
        return _token_split_span(doc, start, end, self.max_tokens, self.token_counter)

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

        # Build overlapping buffers (as text) for similarity comparison.
        buffers: list[str] = []
        for i in range(len(spans)):
            lo = max(0, i - self.buffer_size)
            hi = min(i + 1 + self.buffer_size, len(spans))
            buffers.append(text[spans[lo][0] : spans[hi - 1][1]])

        embeddings = self.embed_fn(buffers)

        distances = [
            _cosine_distance(embeddings[i], embeddings[i + 1])
            for i in range(len(embeddings) - 1)
        ]

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

CHUNK_METHODS = ("fixed", "sentence", "words", "semantic")


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
):
    """Build the chunker named by ``method``.

    ``fixed`` → :class:`RecursiveCharacterChunker`, ``sentence`` →
    :class:`SentenceChunker`, ``words`` → :class:`WordChunker`, ``semantic`` →
    :class:`SemanticChunker` (which requires ``embed_fn``). The return type is
    the protocol :class:`ragstack.protocols.Chunker`; the concrete classes are
    not a common base, so it is left unannotated.

    When ``max_tokens`` + ``token_counter`` are passed, every method sizes/caps by
    tokens so no emitted chunk exceeds the embedder context. With them None the
    char-budget behaviour is unchanged (back-compat).
    """
    if method == "fixed":
        return RecursiveCharacterChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            max_tokens=max_tokens, token_counter=token_counter,
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
    if method == "semantic":
        if embed_fn is None:
            raise ValueError("chunk_method='semantic' requires an embed_fn")
        return SemanticChunker(
            embed_fn=embed_fn,
            buffer_size=buffer_size,
            breakpoint_percentile_threshold=breakpoint_percentile_threshold,
            min_chunk_length=min_chunk_length,
            max_tokens=max_tokens, token_counter=token_counter,
        )
    raise ValueError(f"unknown chunk_method {method!r}; valid: {', '.join(CHUNK_METHODS)}")
