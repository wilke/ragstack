"""SemanticChunker respects ``max_tokens`` WITHOUT cutting inside a sentence.

Owner requirements (#318 follow-up): every chunker respects ``max_tokens``, and a
chunk boundary must not fall inside a sentence unless that one sentence alone
exceeds the budget. Before this change an over-budget semantic block was handed
to ``_token_split_span``, which cuts wherever the token count runs out —
mid-sentence. Now the block is re-packed at sentence boundaries.

``semantic`` and ``semantic_pooled`` share the emit path, so every test runs on
both. Deterministic stub embedder and counters; no model, no network.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.chunkers import SemanticChunker, make_chunker, sentence_spans
from ragstack.models import Document
from tests.unit.test_chunkers_fill import SpaceMergeCounter
from tests.unit.test_chunkers_semantic import _topic_embed_fn
from tests.unit.test_chunkers_tokens import WordTokenCounter

POOLED = [False, True]


def _doc(content: str, doc_id: str = "sem") -> Document:
    return Document(id=doc_id, content=content, source="test")


def _constant_embed(texts):
    """No distance anywhere → no breakpoints → the whole doc is ONE block."""
    return [[1.0, 0.0] for _ in texts]


def _chunker(pooled: bool, embed=_topic_embed_fn, **kw) -> SemanticChunker:
    base: dict = {
        "embed_fn": embed, "buffer_size": 1, "breakpoint_percentile_threshold": 70.0,
        "min_chunk_length": 40, "pool_sentences": pooled,
        "distance_round": 6 if pooled else None,
    }
    base.update(kw)
    return SemanticChunker(**base)


def _topic_text() -> str:
    """Six alternating cat/dog topic blocks of 5-10 sentences, 4-12 words each."""
    out = []
    for blk in range(6):
        animal = "cat" if blk % 2 == 0 else "dog"
        for k in range(5 + blk):
            n = 4 + (blk * 5 + k * 3) % 9
            words = " ".join(
                f"{animal}{(k + w) % 3}" if w == 0 else f"w{(blk + k + w) % 11}"
                for w in range(n)
            )
            out.append(words.capitalize() + ".")
    return " ".join(out)


TEXT = _topic_text()
# The same, plus one 30-word sentence: over the 20-token budget used below, so it
# is the one place a mid-sentence cut is allowed.
LONG_SENTENCE = "Cat0 " + " ".join(f"w{i % 11}" for i in range(29)) + "."
TEXT_LONG = TEXT.replace("Dog", LONG_SENTENCE + " Dog", 1)


def _assert_tiles(doc: Document, chunks) -> None:
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(doc.content)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_char == b.start_char
    assert all(c.content == doc.content[c.start_char : c.end_char] for c in chunks)


def _assert_sentence_boundaries(doc, chunks, counter, max_tokens) -> int:
    """Every chunk edge is a sentence-span edge, EXCEPT a chunk lying wholly inside
    one sentence that alone is over budget. Returns how many such pieces exist."""
    spans = sentence_spans(doc.content)
    edges = {s for s, _ in spans} | {e for _, e in spans}
    inside_oversize = 0
    for c in chunks:
        if c.start_char in edges and c.end_char in edges:
            continue
        host = [(s, e) for s, e in spans if s <= c.start_char and c.end_char <= e]
        assert host, f"chunk {c.start_char}:{c.end_char} straddles a sentence edge mid-sentence"
        s, e = host[0]
        assert counter.count(doc.content[s:e]) > max_tokens, (
            f"chunk {c.start_char}:{c.end_char} cuts inside sentence {s}:{e}, which "
            f"fits the budget ({counter.count(doc.content[s:e])} <= {max_tokens})"
        )
        inside_oversize += 1
    return inside_oversize


# --------------------------------------------------------------------------- #
# The budget and the boundaries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pooled", POOLED)
@pytest.mark.parametrize("counter", [WordTokenCounter(), SpaceMergeCounter()], ids=["words", "bpe"])
@pytest.mark.parametrize("max_tokens", [20, 35, 60])
def test_no_chunk_over_budget_and_no_mid_sentence_cut(pooled, counter, max_tokens):
    doc = _doc(TEXT_LONG)
    chunks = _chunker(pooled, max_tokens=max_tokens, token_counter=counter).chunk(doc)
    assert max(counter.count(c.content) for c in chunks) <= max_tokens
    _assert_tiles(doc, chunks)
    inside = _assert_sentence_boundaries(doc, chunks, counter, max_tokens)
    long_tokens = counter.count(LONG_SENTENCE + " ")
    if long_tokens > max_tokens:
        assert inside >= 2  # the over-budget sentence WAS split (and only it)
    else:
        assert inside == 0


@pytest.mark.parametrize("pooled", POOLED)
def test_two_sentence_over_budget_block_splits_between_the_sentences(pooled):
    """The narrowest case: one block of two sentences, each fitting the budget,
    together over it. The only acceptable split is between them."""
    text = "Alpha beta gamma delta epsilon zeta. Eta theta iota kappa lambda mu."
    doc = _doc(text)
    counter = WordTokenCounter()
    chunks = _chunker(
        pooled, embed=_constant_embed, min_chunk_length=0,
        max_tokens=8, token_counter=counter,
    ).chunk(doc)
    assert [c.content for c in chunks] == [
        "Alpha beta gamma delta epsilon zeta. ",
        "Eta theta iota kappa lambda mu.",
    ]


@pytest.mark.parametrize("pooled", POOLED)
def test_over_budget_block_packs_sentences_greedily(pooled):
    """One 12-sentence block (constant embeddings) at a budget of 3 sentences:
    4 chunks of exactly 3 whole sentences — packed full, not one per sentence."""
    text = " ".join(f"Word{i} a b c d e." for i in range(12))  # 6 words each
    doc = _doc(text)
    counter = WordTokenCounter()
    chunks = _chunker(
        pooled, embed=_constant_embed, min_chunk_length=0,
        max_tokens=18, token_counter=counter,
    ).chunk(doc)
    assert [counter.count(c.content) for c in chunks] == [18, 18, 18, 18]
    _assert_tiles(doc, chunks)
    _assert_sentence_boundaries(doc, chunks, counter, 18)


@pytest.mark.parametrize("pooled", POOLED)
def test_single_sentence_doc_still_hard_split(pooled):
    """A document that is one sentence has no boundary to respect: still capped."""
    text = " ".join(f"w{i}" for i in range(50)) + "."
    doc = _doc(text)
    counter = WordTokenCounter()
    chunks = _chunker(pooled, max_tokens=8, token_counter=counter).chunk(doc)
    assert len(chunks) > 1
    assert max(counter.count(c.content) for c in chunks) <= 8
    _assert_tiles(doc, chunks)


@pytest.mark.parametrize("method", ["semantic", "semantic_pooled"])
def test_through_make_chunker(method):
    counter = WordTokenCounter()
    ch = make_chunker(
        method, embed_fn=_topic_embed_fn, buffer_size=1,
        breakpoint_percentile_threshold=70.0, min_chunk_length=40,
        max_tokens=20, token_counter=counter,
    )
    doc = _doc(TEXT_LONG)
    chunks = ch.chunk(doc)
    assert max(counter.count(c.content) for c in chunks) <= 20
    _assert_sentence_boundaries(doc, chunks, counter, 20)


# --------------------------------------------------------------------------- #
# Unchanged where nothing is over budget
# --------------------------------------------------------------------------- #
# Captured from origin/main 0b43ebb (before this change) on TEXT with the stub
# topic embedder: with no budget, and with a budget every block fits.
SNAPSHOT = {
    False: [(0, 113), (113, 297), (297, 482), (482, 680), (680, 956), (956, 1241)],
    True: [(0, 143), (143, 315), (315, 516), (516, 701), (701, 916), (916, 993), (993, 1241)],
}


@pytest.mark.parametrize("pooled", POOLED)
@pytest.mark.parametrize("max_tokens", [None, 10_000])
def test_unchanged_when_no_block_is_over_budget(pooled, max_tokens):
    kw = {} if max_tokens is None else {"max_tokens": max_tokens, "token_counter": WordTokenCounter()}
    chunks = _chunker(pooled, **kw).chunk(_doc(TEXT))
    assert [(c.start_char, c.end_char) for c in chunks] == SNAPSHOT[pooled]


@pytest.mark.parametrize("pooled", POOLED)
def test_no_budget_never_splits_a_block(pooled):
    """max_tokens=None: an arbitrarily large block is one chunk, as before."""
    text = " ".join(f"Word{i} a b c d." for i in range(200))
    chunks = _chunker(pooled, embed=_constant_embed, min_chunk_length=0).chunk(_doc(text))
    assert [(c.start_char, c.end_char) for c in chunks] == [(0, len(text))]
