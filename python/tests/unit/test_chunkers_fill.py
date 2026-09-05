"""Chunk *fill* tests for the word/sentence token-budget packers.

The defect these pin: word/sentence chunking is meant to fill a chunk up to the
token budget and then cut back to the nearest word (or sentence) boundary. The
legacy packer instead accumulated per-unit token counts, each unit tokenized *in
isolation*, which systematically over-counts the joined chunk — a BPE tokenizer
merges the space before a word into that word's token, a merge a lone word can't
show. Measured with the SFR tokenizer on real text, per-WORD sums over-count by
1.47-1.50x (fill ~0.68) and per-SENTENCE sums by 1.00-1.04x (fill ~0.92-0.97).

``SpaceMergeCounter`` below reproduces exactly that arithmetic deterministically,
so these tests need no model download and run anywhere. It is a *fake*, not the
SFR tokenizer: it is calibrated to the same over-count factors, which is what the
fill assertions are about.
"""
from __future__ import annotations

import math
import re

import pytest

from ragstack.ingestion.chunkers import (
    SentenceChunker,
    WordChunker,
    make_chunker,
    sentence_spans,
    word_spans,
)
from ragstack.models import Document

_RUN = re.compile(r"\S+")


class SpaceMergeCounter:
    """Deterministic fake tokenizer with BPE's leading-space merge.

    Each maximal non-whitespace run costs ``ceil(len(run) / 4)`` tokens. A run
    that is preceded by whitespace *within the string being counted* merges that
    space into its first token and costs no more; a run with no visible preceding
    whitespace — i.e. the run at index 0 — costs one token extra.

    Consequence, and the whole point: unit spans carry their *trailing* whitespace
    (see ``word_spans``/``sentence_spans``), so every span text starts with a run
    and pays the +1 when counted alone, while the joined chunk pays it exactly
    once. Summing per-word counts therefore over-counts a chunk of k words by
    k - 1 tokens (~1.5x for ~5-char words); summing per-sentence counts
    over-counts by (#sentences - 1), a few percent.
    """

    def count(self, text: str) -> int:
        runs = list(_RUN.finditer(text))
        if not runs:
            return 0
        total = sum(math.ceil((m.end() - m.start()) / 4) for m in runs)
        if runs[0].start() == 0:
            total += 1  # no preceding space to merge
        return total


COUNTER = SpaceMergeCounter()
BUDGETS = (256, 512, 1024, 2048)


def _doc(content: str) -> Document:
    return Document(id="doc1", content=content, source="test")


def _prose(n_sentences: int = 900) -> str:
    """Realistic-shaped prose: varied word lengths, varied sentence lengths.

    Sentences stay short (<= ~12 fake tokens) so that at the tightest budget under
    test (256) a single sentence-granularity step is a small fraction of the
    budget — the fill bar is then about the packer, not about the fixture.
    """
    words = [
        "the", "genome", "assembly", "pipeline", "annotates", "a", "contig",
        "using", "curated", "reference", "clusters", "and", "reports",
        "coverage", "per", "sample", "in", "the", "final", "table",
    ]
    out: list[str] = []
    for s in range(n_sentences):
        length = 6 + (s * 7) % 7  # 6..12 words
        sent = " ".join(words[(s * 3 + w) % len(words)] for w in range(length))
        out.append(sent + ".")
    return " ".join(out)


PROSE = _prose()


def _fills(chunks, budget: int) -> list[float]:
    """Realised fill per chunk, dropping the final chunk.

    The last chunk is truncated by the end of the document, not by the packer, so
    including it would measure the fixture rather than the fill policy.
    """
    if len(chunks) < 2:
        raise AssertionError("fixture too small to measure fill")
    return [COUNTER.count(c.content) / budget for c in chunks[:-1]]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


# --------------------------------------------------------------------------- #
# 1. The fix: the new default actually fills the budget
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("budget", BUDGETS)
@pytest.mark.parametrize("cls", [WordChunker, SentenceChunker])
def test_joined_mode_fills_the_budget(cls, budget):
    doc = _doc(PROSE)
    chunks = cls(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget, token_counter=COUNTER
    ).chunk(doc)
    fills = _fills(chunks, budget)
    assert _mean(fills) >= 0.95, (cls.__name__, budget, _mean(fills))
    # Not just on average: no packed chunk is left badly short either.
    assert min(fills) >= 0.95, (cls.__name__, budget, min(fills))


@pytest.mark.parametrize("budget", BUDGETS)
def test_joined_mode_fills_better_than_legacy(budget):
    """Differential: the same input, same budget, strictly fuller chunks."""
    doc = _doc(PROSE)
    new = WordChunker(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget, token_counter=COUNTER
    ).chunk(doc)
    old = WordChunker(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget,
        token_counter=COUNTER, budget_mode="summed",
    ).chunk(doc)
    assert _mean(_fills(new, budget)) > _mean(_fills(old, budget))
    assert len(new) < len(old)  # fuller chunks → fewer of them


# --------------------------------------------------------------------------- #
# 2. Legacy mode still reproduces the OLD fill — this is what makes the already
#    completed Leg A / Leg B grids interpretable rather than lost.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("budget", BUDGETS)
def test_legacy_mode_reproduces_old_word_fill(budget):
    doc = _doc(PROSE)
    chunks = WordChunker(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget,
        token_counter=COUNTER, budget_mode="summed",
    ).chunk(doc)
    fill = _mean(_fills(chunks, budget))
    assert 0.60 <= fill <= 0.75, (budget, fill)  # the reported ~0.68 / study ~0.64


@pytest.mark.parametrize("budget", BUDGETS)
def test_legacy_mode_reproduces_old_sentence_fill(budget):
    doc = _doc(PROSE)
    chunks = SentenceChunker(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget,
        token_counter=COUNTER, budget_mode="summed",
    ).chunk(doc)
    fill = _mean(_fills(chunks, budget))
    assert 0.88 <= fill <= 0.98, (budget, fill)  # the reported ~0.92-0.97


# --------------------------------------------------------------------------- #
# 3. Never-exceed-budget invariant, both modes, including the hard-split path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["joined", "summed"])
@pytest.mark.parametrize("budget", BUDGETS)
@pytest.mark.parametrize("cls", [WordChunker, SentenceChunker])
def test_never_exceeds_budget(cls, budget, mode):
    doc = _doc(PROSE)
    chunks = cls(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget,
        token_counter=COUNTER, budget_mode=mode,
    ).chunk(doc)
    assert chunks
    assert all(COUNTER.count(c.content) <= budget for c in chunks)


@pytest.mark.parametrize("mode", ["joined", "summed"])
@pytest.mark.parametrize("cls", [WordChunker, SentenceChunker])
def test_never_exceeds_budget_with_single_over_budget_unit(cls, mode):
    """A single unit larger than the whole budget must be hard-split, not emitted."""
    budget = 32
    monster = "x" * 4000  # one word / one sentence, ~1000 fake tokens
    text = f"a short lead in sentence. {monster} and a short tail after it."
    doc = _doc(text)
    chunks = cls(
        chunk_size=10**9, chunk_overlap=0, max_tokens=budget,
        token_counter=COUNTER, budget_mode=mode,
    ).chunk(doc)
    assert len(chunks) > 1
    assert all(COUNTER.count(c.content) <= budget for c in chunks)
    # Nothing dropped: with no overlap the chunks tile the source exactly.
    assert "".join(c.content for c in chunks) == text
    assert all(c.content == text[c.start_char : c.end_char] for c in chunks)


@pytest.mark.parametrize("mode", ["joined", "summed"])
def test_never_exceeds_budget_with_overlap_on(mode):
    doc = _doc(PROSE)
    chunks = WordChunker(
        chunk_size=10**9, chunk_overlap=200, max_tokens=512,
        token_counter=COUNTER, budget_mode=mode,
    ).chunk(doc)
    assert len(chunks) > 2
    assert all(COUNTER.count(c.content) <= 512 for c in chunks)
    # Overlap really is on (consecutive chunks share source text).
    assert any(chunks[i + 1].start_char < chunks[i].end_char for i in range(len(chunks) - 1))


# --------------------------------------------------------------------------- #
# 4. No word is split mid-token in the new mode: every boundary is a span edge
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("budget", BUDGETS)
def test_joined_mode_never_splits_a_word(budget):
    doc = _doc(PROSE)
    chunks = WordChunker(
        chunk_size=10**9, chunk_overlap=64, max_tokens=budget, token_counter=COUNTER
    ).chunk(doc)
    spans = word_spans(PROSE)
    starts = {s for s, _ in spans}
    ends = {e for _, e in spans}
    for c in chunks:
        assert c.start_char in starts, c.start_char
        assert c.end_char in ends, c.end_char
    # And the same for sentence units.
    schunks = SentenceChunker(
        chunk_size=10**9, chunk_overlap=64, max_tokens=budget, token_counter=COUNTER
    ).chunk(doc)
    sspans = sentence_spans(PROSE)
    sstarts = {s for s, _ in sspans}
    sends = {e for _, e in sspans}
    for c in schunks:
        assert c.start_char in sstarts, c.start_char
        assert c.end_char in sends, c.end_char


# --------------------------------------------------------------------------- #
# 5. Determinism: same input, byte-identical output
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["joined", "summed"])
@pytest.mark.parametrize("cls", [WordChunker, SentenceChunker])
def test_deterministic(cls, mode):
    doc = _doc(PROSE)

    def run():
        chunks = cls(
            chunk_size=10**9, chunk_overlap=64, max_tokens=512,
            token_counter=SpaceMergeCounter(), budget_mode=mode,
        ).chunk(doc)
        return [(c.id, c.start_char, c.end_char, c.content) for c in chunks]

    first = run()
    assert first == run()
    # A fresh chunker/counter instance too (no hidden per-instance state).
    assert first == run()


# --------------------------------------------------------------------------- #
# 6. Wiring: the mode is selectable through the factory, and typos are loud
# --------------------------------------------------------------------------- #
def test_make_chunker_threads_budget_mode():
    doc = _doc(PROSE)
    new = make_chunker(
        "words", chunk_size=10**9, chunk_overlap=0, max_tokens=512, token_counter=COUNTER
    ).chunk(doc)
    old = make_chunker(
        "words", chunk_size=10**9, chunk_overlap=0, max_tokens=512,
        token_counter=COUNTER, budget_mode="summed",
    ).chunk(doc)
    assert len(new) < len(old)
    assert _mean(_fills(new, 512)) >= 0.95
    assert _mean(_fills(old, 512)) <= 0.75


@pytest.mark.parametrize("cls", [WordChunker, SentenceChunker])
def test_unknown_budget_mode_raises(cls):
    with pytest.raises(ValueError, match="unknown budget_mode"):
        cls(chunk_size=512, budget_mode="jonied")
    with pytest.raises(ValueError, match="unknown budget_mode"):
        make_chunker("words", budget_mode="jonied")


# --------------------------------------------------------------------------- #
# 7. The fake counter itself: it must actually exhibit the bug it stands in for
# --------------------------------------------------------------------------- #
def test_fake_counter_reproduces_the_measured_over_count_factors():
    text = PROSE[:20000]
    joined = COUNTER.count(text)
    word_sum = sum(COUNTER.count(text[s:e]) for s, e in word_spans(text))
    sent_sum = sum(COUNTER.count(text[s:e]) for s, e in sentence_spans(text))
    assert 1.40 <= word_sum / joined <= 1.60  # measured on SFR: 1.47-1.50
    assert 1.00 <= sent_sum / joined <= 1.10  # measured on SFR: 1.00-1.04
