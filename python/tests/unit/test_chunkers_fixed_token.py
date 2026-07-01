"""Unit tests for FixedTokenWindowChunker (the ``fixed_token`` sliding token window).

The chunker needs an HF *fast* tokenizer (offset mapping) via the injected
``TokenCounter``'s ``_tokenizer`` attribute. We fake it with a whitespace
tokenizer that returns exact ``offset_mapping`` char spans, so a token budget maps
to a word count we can reason about exactly — no model download.
"""
from __future__ import annotations

import re
import uuid

from ragstack.ingestion.chunkers import (
    CHUNK_METHODS,
    FixedTokenWindowChunker,
    make_chunker,
)
from ragstack.models import Document

_WORD_RE = re.compile(r"\S+")


class _FakeFastTokenizer:
    """Whitespace tokenizer with an offset mapping, mimicking an HF fast tokenizer.

    Calling it like ``tokenizer(text, return_offsets_mapping=True,
    add_special_tokens=False)`` returns ``{"offset_mapping": [(s, e), ...]}`` for
    each non-whitespace run — the same interface FixedTokenWindowChunker uses.
    ``encode`` counts those tokens so the counter's ``.count`` agrees with the
    window boundaries.
    """

    def __call__(self, text, return_offsets_mapping=False, add_special_tokens=True):
        offsets = [(m.start(), m.end()) for m in _WORD_RE.finditer(text)]
        return {"offset_mapping": offsets}

    def encode(self, text, add_special_tokens=True):
        return _WORD_RE.findall(text)


class HFLikeWordCounter:
    """Fake HFTokenCounter: token = whitespace-run, and it exposes ``_tokenizer``."""

    def __init__(self) -> None:
        self._tok = _FakeFastTokenizer()

    def _tokenizer(self):
        return self._tok

    def count(self, text: str) -> int:
        return len(_WORD_RE.findall(text))


class PlainWordCounter:
    """A counter with NO ``_tokenizer`` attr (e.g. the estimate/endpoint backend)."""

    def count(self, text: str) -> int:
        return len(text.split())


def _doc(content: str, doc_id: str = "doc1") -> Document:
    return Document(id=doc_id, content=content, source="test")


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def _reconstructs(doc: Document, chunks) -> bool:
    return all(c.content == doc.content[c.start_char : c.end_char] for c in chunks)


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_fixed_token_registered_in_methods_and_factory():
    assert "fixed_token" in CHUNK_METHODS
    counter = HFLikeWordCounter()
    chunker = make_chunker(
        "fixed_token", chunk_size=5, chunk_overlap=1, token_counter=counter
    )
    assert isinstance(chunker, FixedTokenWindowChunker)


def test_fixed_token_requires_token_counter():
    import pytest

    with pytest.raises(ValueError):
        make_chunker("fixed_token", chunk_size=5, chunk_overlap=1, token_counter=None)


# --------------------------------------------------------------------------- #
# Correctness
# --------------------------------------------------------------------------- #
def test_all_source_content_preserved():
    counter = HFLikeWordCounter()
    doc = _doc(_words(23))  # 23 tokens
    chunker = FixedTokenWindowChunker(
        chunk_size=5, chunk_overlap=0, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) > 1
    # Windows span from the first token's start to the last token's end, so the
    # union covers [0, len) — every source character is inside some chunk's span.
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(doc.content)
    covered = [False] * len(doc.content)
    for c in chunks:
        for i in range(c.start_char, c.end_char):
            covered[i] = True
    # Token offsets tile the token chars; the only chars a non-overlapping window
    # boundary can leave uncovered are inter-token whitespace, never any token
    # (non-whitespace) character. Assert every non-whitespace char is covered.
    for i, ch in enumerate(doc.content):
        if not ch.isspace():
            assert covered[i], f"non-whitespace char {i!r} not covered"
    assert _reconstructs(doc, chunks)


def test_offsets_exact_and_content_is_source_slice():
    counter = HFLikeWordCounter()
    doc = _doc("alpha beta gamma delta epsilon zeta eta")  # 7 tokens
    chunker = FixedTokenWindowChunker(
        chunk_size=3, chunk_overlap=1, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert _reconstructs(doc, chunks)
    # First window = first 3 tokens: "alpha beta gamma" (offsets from the map).
    assert chunks[0].content == "alpha beta gamma"
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == len("alpha beta gamma")


def test_every_chunk_within_token_budget():
    counter = HFLikeWordCounter()
    doc = _doc(_words(40))
    chunker = FixedTokenWindowChunker(
        chunk_size=7, chunk_overlap=2, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert chunks
    assert all(counter.count(c.content) <= 7 for c in chunks)


def test_overlap_reemits_trailing_tokens():
    counter = HFLikeWordCounter()
    doc = _doc(_words(20))
    chunker = FixedTokenWindowChunker(
        chunk_size=5, chunk_overlap=2, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) > 1
    # Overlap on → consecutive chunks share tokens → spans overlap.
    assert any(
        chunks[i + 1].start_char < chunks[i].end_char for i in range(len(chunks) - 1)
    )
    assert all(counter.count(c.content) <= 5 for c in chunks)


class _MergeBoundaryCounter(HFLikeWordCounter):
    """A counter whose ``.count`` over-counts by one whenever a window slice starts
    exactly at a specific char offset — simulating the HF 'a boundary-cut re-encode
    adds a merge token' behaviour the chunker's trim loop must defend against.
    """

    def __init__(self, trip_prefix: str) -> None:
        super().__init__()
        self._trip = trip_prefix

    def count(self, text: str) -> int:
        base = super().count(text)
        # Emulate the re-encode adding one token for a slice that includes the
        # trip prefix (forces the chunker's while-loop to trim by a token).
        if text.startswith(self._trip):
            return base + 1
        return base


def test_boundary_trim_case_keeps_recount_within_budget():
    # The re-tokenized slice can exceed the window by one merge token; the chunker
    # must trim by a whole token until the RE-COUNTED content fits the budget.
    doc = _doc(_words(12))
    counter = _MergeBoundaryCounter(trip_prefix="w0")
    chunker = FixedTokenWindowChunker(
        chunk_size=5, chunk_overlap=0, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert chunks
    # Every emitted chunk re-counts to <= window even under the +1 merge inflation.
    assert all(counter.count(c.content) <= 5 for c in chunks)
    # Still lossless overall (no source text dropped by the trimming).
    assert _reconstructs(doc, chunks)


def test_deterministic_unique_ids():
    counter = HFLikeWordCounter()
    doc = _doc(_words(30))
    chunker = FixedTokenWindowChunker(
        chunk_size=6, chunk_overlap=2, token_counter=counter
    )
    first = chunker.chunk(doc)
    second = chunker.chunk(doc)
    ids = [c.id for c in first]
    assert len(set(ids)) == len(ids)  # unique
    assert [c.id for c in second] == ids  # deterministic
    # ids are uuid5(doc_id:start:end).
    for c in first:
        assert c.id == str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.id}:{c.start_char}:{c.end_char}")
        )
    # A different doc id → different chunk ids.
    other = chunker.chunk(_doc(doc.content, doc_id="doc2"))
    assert [c.id for c in other] != ids


def test_doc_shorter_than_window_is_single_chunk():
    counter = HFLikeWordCounter()
    doc = _doc(_words(3))  # 3 tokens, window 10
    chunker = FixedTokenWindowChunker(
        chunk_size=10, chunk_overlap=2, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == len(doc.content)
    assert chunks[0].content == doc.content


def test_empty_doc_yields_no_chunks():
    counter = HFLikeWordCounter()
    assert FixedTokenWindowChunker(token_counter=counter).chunk(_doc("")) == []


def test_non_hf_counter_degrades_to_whole_doc():
    # A counter without ``_tokenizer`` (estimate/endpoint) → single whole-doc chunk.
    doc = _doc(_words(50))
    chunker = FixedTokenWindowChunker(
        chunk_size=5, chunk_overlap=0, token_counter=PlainWordCounter()
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].content == doc.content


def test_interior_trim_at_zero_overlap_keeps_all_tokens_covered():
    # Regression for the interior-trim gap: when the boundary trim fires on a
    # NON-first window with chunk_overlap=0, the advance must not skip the
    # trimmed-off token(s). We trip the +1 merge inflation on the window that
    # starts at token "w5"; under the old fixed `start += window` advance, token
    # w9 was dropped from all chunks. Now every non-whitespace char is covered.
    doc = _doc(_words(15))  # tokens w0..w14
    counter = _MergeBoundaryCounter(trip_prefix="w5")  # trips the interior window
    chunker = FixedTokenWindowChunker(
        chunk_size=5, chunk_overlap=0, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert chunks
    covered = [False] * len(doc.content)
    for c in chunks:
        for i in range(c.start_char, c.end_char):
            covered[i] = True
    for i, ch in enumerate(doc.content):
        if not ch.isspace():
            assert covered[i], f"non-whitespace char {i} ({doc.content[i]!r}) dropped"
    assert all(counter.count(c.content) <= 5 for c in chunks)
