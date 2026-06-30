"""Token-aware chunking tests with a deterministic fake TokenCounter.

The fake counts whitespace-delimited tokens (``len(text.split())``), so a token
budget maps to a word count we can reason about exactly — no model download.
"""
from __future__ import annotations

from ragstack.ingestion.chunkers import (
    RecursiveCharacterChunker,
    SemanticChunker,
    SentenceChunker,
    WordChunker,
    make_chunker,
    split_text_to_token_budget,
)
from ragstack.models import Document


class WordTokenCounter:
    """Deterministic fake: token count = number of whitespace-separated tokens."""

    def count(self, text: str) -> int:
        return len(text.split())

    def count_batch(self, texts: list[str]) -> list[int]:
        return [self.count(t) for t in texts]


class CharTokenCounter:
    """Fake where every character is a token (lets us force single-unit splits)."""

    def count(self, text: str) -> int:
        return len(text)

    def count_batch(self, texts: list[str]) -> list[int]:
        return [self.count(t) for t in texts]


def _doc(content: str) -> Document:
    return Document(id="doc1", content=content, source="test")


def _reconstructs(doc: Document, chunks) -> bool:
    """Every chunk's content equals its span slice (offsets are honest)."""
    return all(c.content == doc.content[c.start_char : c.end_char] for c in chunks)


# --------------------------------------------------------------------------- #
# split_text_to_token_budget
# --------------------------------------------------------------------------- #
def test_split_helper_lossless_and_within_budget():
    counter = WordTokenCounter()
    text = " ".join(f"w{i}" for i in range(20))  # 20 tokens
    pieces = split_text_to_token_budget(text, max_tokens=5, token_counter=counter)
    assert "".join(pieces) == text  # lossless
    assert all(counter.count(p) <= 5 for p in pieces)
    assert len(pieces) >= 4


def test_split_helper_short_text_unchanged():
    counter = WordTokenCounter()
    text = "a b c"
    assert split_text_to_token_budget(text, 10, counter) == [text]


def test_split_helper_makes_progress_on_single_char_budget():
    # A char-token counter with budget 1 forces one char per piece, no infinite loop.
    counter = CharTokenCounter()
    pieces = split_text_to_token_budget("abcd", 1, counter)
    assert pieces == ["a", "b", "c", "d"]


# --------------------------------------------------------------------------- #
# SentenceChunker / WordChunker token packing
# --------------------------------------------------------------------------- #
def _sentences(n: int, words_each: int = 4) -> str:
    return " ".join(
        " ".join(f"w{s}_{w}" for w in range(words_each)) + "." for s in range(n)
    )


def test_sentence_chunker_respects_token_budget():
    counter = WordTokenCounter()
    doc = _doc(_sentences(10, words_each=4))  # 10 sentences x 4 words = 40 tokens
    chunker = SentenceChunker(
        chunk_size=512, chunk_overlap=0, max_tokens=9, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert chunks
    assert all(counter.count(c.content) <= 9 for c in chunks)
    assert _reconstructs(doc, chunks)


def test_word_chunker_respects_token_budget():
    counter = WordTokenCounter()
    doc = _doc(" ".join(f"word{i}" for i in range(50)))  # 50 word-tokens
    chunker = WordChunker(
        chunk_size=512, chunk_overlap=0, max_tokens=7, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert chunks
    assert all(counter.count(c.content) <= 7 for c in chunks)
    assert _reconstructs(doc, chunks)


def test_sentence_chunker_hard_splits_single_oversized_unit():
    # One sentence with more tokens than the budget must be hard-split (not dropped).
    counter = WordTokenCounter()
    big_sentence = " ".join(f"t{i}" for i in range(20)) + "."  # 20 tokens, one sentence
    doc = _doc(big_sentence)
    chunker = SentenceChunker(
        chunk_size=512, chunk_overlap=0, max_tokens=6, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) > 1  # got hard-split
    assert all(counter.count(c.content) <= 6 for c in chunks)
    # No source text dropped: concatenated spans reproduce the whole sentence.
    assert "".join(c.content for c in chunks) == big_sentence
    assert _reconstructs(doc, chunks)


def test_token_overlap_reemits_one_unit():
    counter = WordTokenCounter()
    doc = _doc(_sentences(8, words_each=3))  # 8 sentences x 3 = 24 tokens
    chunker = SentenceChunker(
        chunk_size=512, chunk_overlap=10, max_tokens=6, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) > 1
    # With overlap on, consecutive chunks share a boundary unit → spans overlap.
    assert any(
        chunks[i + 1].start_char < chunks[i].end_char for i in range(len(chunks) - 1)
    )
    assert all(counter.count(c.content) <= 6 for c in chunks)


# --------------------------------------------------------------------------- #
# RecursiveCharacterChunker token cap
# --------------------------------------------------------------------------- #
def test_recursive_char_chunker_token_caps_pieces():
    counter = CharTokenCounter()  # 1 token == 1 char
    doc = _doc("x" * 100)
    # Large char window (would emit one 100-char chunk) but token budget forces split.
    chunker = RecursiveCharacterChunker(
        chunk_size=1000, chunk_overlap=0, max_tokens=10, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert all(counter.count(c.content) <= 10 for c in chunks)
    assert "".join(c.content for c in chunks) == doc.content
    assert _reconstructs(doc, chunks)


# --------------------------------------------------------------------------- #
# SemanticChunker token cap (replaces the char cap)
# --------------------------------------------------------------------------- #
def test_semantic_chunker_token_caps_chunks():
    counter = WordTokenCounter()
    # Constant embeddings → no breakpoints → one big semantic chunk, which must
    # then be token-split down to the budget.
    def embed_fn(texts):
        return [[1.0, 0.0] for _ in texts]

    doc = _doc(_sentences(12, words_each=5))  # 12 sentences x 5 = 60 tokens
    chunker = SemanticChunker(
        embed_fn=embed_fn,
        buffer_size=1,
        min_chunk_length=0,
        max_tokens=8,
        token_counter=counter,
    )
    chunks = chunker.chunk(doc)
    assert chunks
    assert all(counter.count(c.content) <= 8 for c in chunks)
    assert "".join(c.content for c in chunks) == doc.content
    assert _reconstructs(doc, chunks)


# --------------------------------------------------------------------------- #
# Back-compat: max_tokens=None behaves exactly as before
# --------------------------------------------------------------------------- #
def test_max_tokens_none_is_backward_compatible():
    doc = _doc(_sentences(6, words_each=4))
    no_tok = SentenceChunker(chunk_size=40, chunk_overlap=8).chunk(doc)
    with_none = SentenceChunker(
        chunk_size=40, chunk_overlap=8, max_tokens=None, token_counter=None
    ).chunk(doc)
    assert [c.id for c in no_tok] == [c.id for c in with_none]
    assert [(c.start_char, c.end_char) for c in no_tok] == [
        (c.start_char, c.end_char) for c in with_none
    ]


def test_make_chunker_threads_token_params():
    counter = WordTokenCounter()
    doc = _doc(" ".join(f"word{i}" for i in range(40)))
    chunker = make_chunker(
        "words", chunk_size=512, chunk_overlap=0, max_tokens=6, token_counter=counter
    )
    chunks = chunker.chunk(doc)
    assert all(counter.count(c.content) <= 6 for c in chunks)
    assert _reconstructs(doc, chunks)
