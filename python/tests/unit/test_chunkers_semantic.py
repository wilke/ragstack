"""Unit tests for the sentence, word, and semantic chunkers.

The semantic chunker is tested with a deterministic STUB embed function — no
live embedding endpoint is touched. Every test asserts that
``start_char``/``end_char`` reconstruct the source exactly, since the
deterministic ``uuid5(doc.id:start:end)`` chunk id depends on accurate spans.
"""
import uuid

import pytest

from ragstack.ingestion.chunkers import (
    SemanticChunker,
    SentenceChunker,
    WordChunker,
    make_chunker,
    sentence_spans,
    word_spans,
)
from ragstack.models import Document


def _make_doc(content: str, doc_id: str = "doc1") -> Document:
    return Document(id=doc_id, content=content, source="test")


def _check(doc: Document, chunks) -> None:
    for ch in chunks:
        assert ch.content == doc.content[ch.start_char : ch.end_char]
        assert ch.doc_id == doc.id
        # id is the deterministic uuid5 of doc.id:start:end
        expected = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.id}:{ch.start_char}:{ch.end_char}")
        )
        assert ch.id == expected


# --------------------------------------------------------------------------- #
# span helpers
# --------------------------------------------------------------------------- #


def test_sentence_spans_tile_text_gaplessly():
    text = "First sentence. Second one! Third?\n\nFourth para."
    spans = sentence_spans(text)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    # gapless: each span starts where the previous ended
    for a, b in zip(spans, spans[1:], strict=False):
        assert a[1] == b[0]
    # concatenation reproduces the source exactly
    assert "".join(text[s:e] for s, e in spans) == text


def test_word_spans_tile_text_gaplessly():
    text = "  alpha beta   gamma\tdelta "
    spans = word_spans(text)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    assert "".join(text[s:e] for s, e in spans) == text


def test_empty_text_has_no_spans():
    assert sentence_spans("") == []
    assert word_spans("") == []


# --------------------------------------------------------------------------- #
# SentenceChunker
# --------------------------------------------------------------------------- #


def test_sentence_chunker_offsets_reconstruct():
    text = (
        "Alpha is the first letter. Beta follows it closely. "
        "Gamma comes third in line. Delta sits at position four. "
        "Epsilon rounds out the set."
    )
    doc = _make_doc(text)
    chunks = SentenceChunker(chunk_size=60, chunk_overlap=20).chunk(doc)
    assert len(chunks) > 1
    _check(doc, chunks)


def test_sentence_chunker_no_overlap_tiles_source():
    text = "One. Two. Three. Four. Five. Six."
    doc = _make_doc(text)
    chunks = SentenceChunker(chunk_size=12, chunk_overlap=0).chunk(doc)
    _check(doc, chunks)
    # with zero overlap, chunk spans are contiguous and cover the whole source
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(text)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_char == b.start_char


def test_sentence_chunker_overlap_replays_units():
    # Short sentences so several fit in a chunk and the overlap tail replays at
    # least one whole sentence into the next chunk.
    text = "Aa. Bb. Cc. Dd. Ee. Ff. Gg. Hh."
    doc = _make_doc(text)
    chunks = SentenceChunker(chunk_size=12, chunk_overlap=8).chunk(doc)
    _check(doc, chunks)
    assert len(chunks) > 1
    # overlap means the next chunk starts before the previous chunk ended
    assert chunks[1].start_char < chunks[0].end_char


def test_sentence_chunker_chunk_size_minus_one_returns_whole_doc():
    text = "Sentence one. Sentence two. Sentence three."
    doc = _make_doc(text)
    chunks = SentenceChunker(chunk_size=-1).chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == len(text)
    _check(doc, chunks)


def test_sentence_chunker_single_sentence():
    text = "Just one sentence with no terminal boundary"
    doc = _make_doc(text)
    chunks = SentenceChunker(chunk_size=512).chunk(doc)
    assert len(chunks) == 1
    _check(doc, chunks)


def test_sentence_chunker_empty():
    assert SentenceChunker(chunk_size=512).chunk(_make_doc("")) == []


# --------------------------------------------------------------------------- #
# WordChunker
# --------------------------------------------------------------------------- #


def test_word_chunker_offsets_reconstruct():
    text = " ".join(f"word{i}" for i in range(40))
    doc = _make_doc(text)
    chunks = WordChunker(chunk_size=40, chunk_overlap=10).chunk(doc)
    assert len(chunks) > 1
    _check(doc, chunks)


def test_word_chunker_no_overlap_tiles_source():
    text = "one two three four five six seven eight"
    doc = _make_doc(text)
    chunks = WordChunker(chunk_size=10, chunk_overlap=0).chunk(doc)
    _check(doc, chunks)
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(text)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_char == b.start_char


def test_word_chunker_overlap_replays_units():
    text = "aaaa bbbb cccc dddd eeee ffff gggg hhhh"
    doc = _make_doc(text)
    chunks = WordChunker(chunk_size=12, chunk_overlap=8).chunk(doc)
    _check(doc, chunks)
    assert len(chunks) > 1
    assert chunks[1].start_char < chunks[0].end_char


def test_word_chunker_chunk_size_minus_one_returns_whole_doc():
    text = "alpha beta gamma delta"
    doc = _make_doc(text)
    chunks = WordChunker(chunk_size=-1).chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].end_char == len(text)
    _check(doc, chunks)


def test_word_chunker_single_word():
    doc = _make_doc("singleword")
    chunks = WordChunker(chunk_size=512).chunk(doc)
    assert len(chunks) == 1
    _check(doc, chunks)


def test_word_chunker_empty():
    assert WordChunker(chunk_size=512).chunk(_make_doc("")) == []


def test_word_chunker_oversized_single_word_still_emits():
    # one word longer than chunk_size must not be dropped or loop forever
    doc = _make_doc("supercalifragilisticexpialidocious tiny")
    chunks = WordChunker(chunk_size=5, chunk_overlap=2).chunk(doc)
    _check(doc, chunks)
    assert "".join(c.content for c in [chunks[0]])  # first chunk non-empty
    assert chunks[-1].end_char == len(doc.content)


# --------------------------------------------------------------------------- #
# SemanticChunker (stub embedder — NO live endpoint)
# --------------------------------------------------------------------------- #


def _topic_embed_fn(texts):
    """Deterministic stub: vectors keyed on whether the buffer is about cats or
    dogs, so a clear topic shift produces a large cosine distance (breakpoint)."""
    out = []
    for t in texts:
        tl = t.lower()
        cats = tl.count("cat")
        dogs = tl.count("dog")
        out.append([float(cats) + 0.01, float(dogs) + 0.01])
    return out


def test_semantic_chunker_offsets_reconstruct_and_split():
    text = (
        "Cats are wonderful. Cats purr softly. Cats love to nap. "
        "Dogs are loyal companions. Dogs bark loudly. Dogs fetch balls."
    )
    doc = _make_doc(text)
    chunker = SemanticChunker(
        embed_fn=_topic_embed_fn,
        buffer_size=1,
        breakpoint_percentile_threshold=50.0,
        min_chunk_length=0,
    )
    chunks = chunker.chunk(doc)
    _check(doc, chunks)
    # the cat->dog topic shift should produce more than one chunk
    assert len(chunks) > 1
    # chunks tile the source: contiguous and full coverage
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(text)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_char == b.start_char
    # no text dropped
    assert "".join(c.content for c in chunks) == text


def test_semantic_chunker_merges_short_chunks():
    text = (
        "Cats are wonderful. Cats purr softly. Cats love to nap. "
        "Dogs are loyal companions. Dogs bark loudly. Dogs fetch balls."
    )
    doc = _make_doc(text)
    # huge min_chunk_length forces everything to merge back into one chunk
    chunker = SemanticChunker(
        embed_fn=_topic_embed_fn,
        buffer_size=1,
        breakpoint_percentile_threshold=50.0,
        min_chunk_length=10_000,
    )
    chunks = chunker.chunk(doc)
    _check(doc, chunks)
    assert len(chunks) == 1
    assert chunks[0].content == text


def test_semantic_chunker_single_sentence():
    doc = _make_doc("Only a single sentence here")
    chunker = SemanticChunker(embed_fn=_topic_embed_fn)
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1
    _check(doc, chunks)


def test_semantic_chunker_empty():
    chunker = SemanticChunker(embed_fn=_topic_embed_fn)
    assert chunker.chunk(_make_doc("")) == []


def test_semantic_chunker_ids_deterministic():
    text = (
        "Cats are wonderful. Cats purr softly. "
        "Dogs are loyal companions. Dogs bark loudly."
    )
    doc = _make_doc(text)
    chunker = SemanticChunker(
        embed_fn=_topic_embed_fn,
        buffer_size=1,
        breakpoint_percentile_threshold=50.0,
        min_chunk_length=0,
    )
    first = [c.id for c in chunker.chunk(doc)]
    second = [c.id for c in chunker.chunk(doc)]
    assert first == second


# --------------------------------------------------------------------------- #
# make_chunker factory
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# SemanticChunker pooled mode (embed-once + mean-pool) — issue: faster segmentation
# --------------------------------------------------------------------------- #


def test_mean_pool_elementwise_mean():
    from ragstack.ingestion.chunkers import _mean_pool

    assert _mean_pool([[1.0, 2.0, 3.0]]) == [1.0, 2.0, 3.0]  # single passes through
    assert _mean_pool([[0.0, 0.0], [2.0, 4.0]]) == [1.0, 2.0]
    assert _mean_pool([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]]) == [2.0, 2.0]


def test_semantic_pooled_embeds_each_sentence_once():
    """Pooled mode embeds SENTENCES (one per span), not overlapping buffer texts —
    one embed call whose inputs are the individual sentences."""
    text = (
        "Cats are wonderful. Cats purr softly. Cats love to nap. "
        "Dogs are loyal companions. Dogs bark loudly. Dogs fetch balls."
    )
    doc = _make_doc(text)
    n_sentences = len(sentence_spans(text))
    calls: list[list[str]] = []

    def recording(texts):
        calls.append(list(texts))
        return _topic_embed_fn(texts)

    chunker = make_chunker(
        "semantic_pooled", embed_fn=recording, buffer_size=1,
        breakpoint_percentile_threshold=50.0, min_chunk_length=0,
    )
    chunks = chunker.chunk(doc)
    assert len(calls) == 1, "one embed call per document"
    assert len(calls[0]) == n_sentences, "inputs are the individual sentences"
    # each input is a single sentence (matches a span slice), not a multi-sentence buffer
    for txt, (s, e) in zip(calls[0], sentence_spans(text), strict=True):
        assert txt == text[s:e]
    _check(doc, chunks)
    assert len(chunks) > 1  # cat->dog shift still splits
    assert chunks[0].start_char == 0 and chunks[-1].end_char == len(text)
    assert "".join(c.content for c in chunks) == text  # tiles, no text dropped


def test_semantic_pooled_ids_stable_under_sub_epsilon_perturbation():
    """Reproducibility safeguard: distance rounding (distance_round=6, the default
    for semantic_pooled) makes block boundaries — and thus chunk ids — identical
    under a sub-6-decimal embedding perturbation (i.e. low-bit float differences
    across GPU/kernel versions can't flip a boundary)."""
    text = (
        "Cats are wonderful. Cats purr softly. Cats love to nap here. "
        "Dogs are loyal companions. Dogs bark loudly. Dogs fetch the balls."
    )
    doc = _make_doc(text)

    def perturbed(texts):
        return [[x + 1e-9 for x in v] for v in _topic_embed_fn(texts)]

    clean = make_chunker(
        "semantic_pooled", embed_fn=_topic_embed_fn, buffer_size=1,
        breakpoint_percentile_threshold=50.0, min_chunk_length=0,
    ).chunk(doc)
    noisy = make_chunker(
        "semantic_pooled", embed_fn=perturbed, buffer_size=1,
        breakpoint_percentile_threshold=50.0, min_chunk_length=0,
    ).chunk(doc)
    assert [c.id for c in clean] == [c.id for c in noisy]


def test_semantic_pooled_token_caps_long_sentence():
    """A sentence over the token budget is bounded before embedding (so it can't
    overflow the context window), without altering the emitted chunk text."""
    class _FakeCounter:
        def count(self, text: str) -> int:
            return len(text)  # 1 token per char

    long_sentence = "x " * 200  # 400 chars -> over a tiny budget
    text = long_sentence + ". Short tail sentence here."
    doc = _make_doc(text)
    seen: list[str] = []

    def recording(texts):
        seen.extend(texts)
        return _topic_embed_fn(texts)

    chunker = SemanticChunker(
        embed_fn=recording, buffer_size=1, breakpoint_percentile_threshold=50.0,
        min_chunk_length=0, max_tokens=50, token_counter=_FakeCounter(),
        pool_sentences=True, distance_round=6,
    )
    chunks = chunker.chunk(doc)
    assert all(len(t) <= 50 for t in seen), "each embedded sentence bounded to budget"
    _check(doc, chunks)  # emitted chunk offsets still reconstruct the FULL source


def test_make_chunker_dispatch():
    from ragstack.ingestion.chunkers import (
        RecursiveCharacterChunker,
    )

    assert isinstance(make_chunker("fixed"), RecursiveCharacterChunker)
    assert isinstance(make_chunker("sentence"), SentenceChunker)
    assert isinstance(make_chunker("words"), WordChunker)
    assert isinstance(
        make_chunker("semantic", embed_fn=_topic_embed_fn), SemanticChunker
    )
    pooled = make_chunker("semantic_pooled", embed_fn=_topic_embed_fn)
    assert isinstance(pooled, SemanticChunker)
    assert pooled.pool_sentences is True and pooled.distance_round == 6
    # legacy semantic stays non-pooled with no distance rounding
    legacy = make_chunker("semantic", embed_fn=_topic_embed_fn)
    assert legacy.pool_sentences is False and legacy.distance_round is None


def test_make_chunker_semantic_pooled_requires_embed_fn():
    with pytest.raises(ValueError, match="embed_fn"):
        make_chunker("semantic_pooled")


def test_make_chunker_semantic_requires_embed_fn():
    with pytest.raises(ValueError, match="embed_fn"):
        make_chunker("semantic")


def test_make_chunker_unknown_method():
    with pytest.raises(ValueError, match="unknown chunk_method"):
        make_chunker("nonsense")
