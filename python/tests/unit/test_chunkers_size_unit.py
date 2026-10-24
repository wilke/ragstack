"""``size_unit``: ``chunk_size`` as a token budget for sentence/word chunking (#318 P1).

The defect: with a token budget (``max_tokens`` + ``token_counter``) the sentence
and word packers fill to ``max_tokens`` and never read ``chunk_size``. The bulk
ingest path always resolves ``max_tokens`` to the model window, so
``--chunk-method sentence --chunk-size 512`` produced ~4080-token chunks.

Now ``chunk_size`` is always used. ``size_unit="tokens"`` makes it a token budget
and honours it as a hard **maximum** — including for a single unit longer than
``chunk_size``, which is the case the naive fix gets wrong. ``size_unit="chars"``
(default) packs ``chunk_size`` chars exactly as the char path always has, with
``max_tokens`` as a cap that re-packs an over-cap chunk at unit boundaries. The
pre-#318 "pack to the window" behaviour is removed, not preserved (owner,
2026-10-23: no production sentence/words collection was built with it).

``SpaceMergeCounter`` (a deterministic BPE-like fake) comes from the fill tests,
so no tokenizer is downloaded.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.chunkers import (
    SIZE_UNITS,
    SentenceChunker,
    WordChunker,
    make_chunker,
    sentence_spans,
    word_spans,
)
from ragstack.models import Document
from tests.unit.test_chunkers_fill import SpaceMergeCounter, _prose

COUNTER = SpaceMergeCounter()
CHUNKERS = (SentenceChunker, WordChunker)
SIZES = (128, 256, 512)
#: The model window build_chunker resolves for SFR (4096 - 16 reserve).
WINDOW = 4080


def _doc(content: str, doc_id: str = "doc1") -> Document:
    return Document(id=doc_id, content=content, source="test")


# A single unit far longer than any requested chunk_size but SHORTER than the
# model window: 12,000 chars with no whitespace or punctuation is one sentence
# span (no separator for _subsplit_long_spans to cut on) and one word span, and
# costs ceil(12000/4) + 1 = 3001 fake tokens. That is the issue's "3000-token unit
# against a 512 request": not > 4080, so a hard split keyed on max_tokens would
# emit it whole.
GIANT_UNIT = "x" * 12_000

# A real (whitespace-bearing) sentence of ~950 fake tokens, kept under the
# 2000-char threshold so sentence_spans leaves it as ONE sentence: 950
# single-letter words cost 1 token each. Longer than every size under test.
LONG_SENTENCE = " ".join("a" for _ in range(950)) + "."

CORPUS = " ".join([_prose(120), LONG_SENTENCE, _prose(60), GIANT_UNIT, _prose(40)])


def _assert_tiles(doc: Document, chunks) -> None:
    """No overlap configured → chunks tile the source exactly: nothing dropped,
    nothing duplicated, offsets honest."""
    assert chunks
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(doc.content)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end_char == b.start_char
    assert all(c.content == doc.content[c.start_char : c.end_char] for c in chunks)
    assert "".join(c.content for c in chunks) == doc.content


def test_fixture_contains_units_longer_than_every_size():
    assert COUNTER.count(GIANT_UNIT) > 3000
    assert max(SIZES) < COUNTER.count(LONG_SENTENCE) < WINDOW
    assert COUNTER.count(GIANT_UNIT) < WINDOW


# --------------------------------------------------------------------------- #
# (a) + (b): the MAX realised chunk is within chunk_size; the oversize unit is
# split at the budget, losslessly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("cls", CHUNKERS)
@pytest.mark.parametrize("budget_mode", ["joined", "summed"])
def test_max_realised_tokens_within_chunk_size(cls, size, budget_mode):
    doc = _doc(CORPUS)
    chunks = cls(
        chunk_size=size, chunk_overlap=0, max_tokens=WINDOW, token_counter=COUNTER,
        budget_mode=budget_mode, size_unit="tokens",
    ).chunk(doc)
    realised = [COUNTER.count(c.content) for c in chunks]
    # MAX, not mean or median: a single 3000-token chunk among hundreds of
    # in-budget ones is invisible to an average.
    assert max(realised) <= size, f"max realised {max(realised)} > chunk_size {size}"
    _assert_tiles(doc, chunks)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("cls", CHUNKERS)
def test_oversize_unit_is_split_at_the_budget_not_the_window(cls, size):
    doc = _doc(CORPUS)
    chunks = cls(
        chunk_size=size, chunk_overlap=0, max_tokens=WINDOW, token_counter=COUNTER,
        size_unit="tokens",
    ).chunk(doc)
    g0 = CORPUS.index(GIANT_UNIT)
    g1 = g0 + len(GIANT_UNIT)
    pieces = [c for c in chunks if c.start_char < g1 and c.end_char > g0]
    # The 3001-token unit becomes many pieces, each within the budget, together
    # covering every one of its chars.
    assert len(pieces) >= COUNTER.count(GIANT_UNIT) // size
    assert all(COUNTER.count(p.content) <= size for p in pieces)
    covered = "".join(doc.content[max(p.start_char, g0) : min(p.end_char, g1)] for p in pieces)
    assert covered == GIANT_UNIT


@pytest.mark.parametrize("cls", CHUNKERS)
def test_size_with_no_cap_is_still_a_hard_maximum(cls):
    """``max_tokens=None`` with ``size_unit="tokens"``: chunk_size alone is the
    budget, for packing and hard split alike."""
    doc = _doc(CORPUS)
    chunks = cls(
        chunk_size=256, chunk_overlap=0, token_counter=COUNTER, size_unit="tokens"
    ).chunk(doc)
    assert max(COUNTER.count(c.content) for c in chunks) <= 256
    _assert_tiles(doc, chunks)


@pytest.mark.parametrize("cls", CHUNKERS)
def test_overlap_stays_in_chars_and_within_budget(cls):
    doc = _doc(_prose(200))
    chunks = cls(
        chunk_size=128, chunk_overlap=80, max_tokens=WINDOW, token_counter=COUNTER,
        size_unit="tokens",
    ).chunk(doc)
    assert max(COUNTER.count(c.content) for c in chunks) <= 128
    # Overlap is replayed (chunks overlap), and bounded by the char overlap.
    overlaps = [a.end_char - b.start_char for a, b in zip(chunks, chunks[1:], strict=False)]
    assert any(o > 0 for o in overlaps)
    assert all(o <= 80 for o in overlaps)


# --------------------------------------------------------------------------- #
# (c): chunk_size larger than the window → the window wins
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cls", CHUNKERS)
def test_chunk_size_above_max_tokens_is_capped(cls):
    doc = _doc(CORPUS)
    capped = cls(
        chunk_size=1000, chunk_overlap=0, max_tokens=256, token_counter=COUNTER,
        size_unit="tokens",
    ).chunk(doc)
    assert max(COUNTER.count(c.content) for c in capped) <= 256
    # Exactly the chunking you get by asking for 256 directly.
    direct = cls(
        chunk_size=256, chunk_overlap=0, token_counter=COUNTER, size_unit="tokens"
    ).chunk(doc)
    assert [(c.start_char, c.end_char) for c in capped] == [
        (c.start_char, c.end_char) for c in direct
    ]


# --------------------------------------------------------------------------- #
# (d): size_unit="chars" — chunk_size chars on every path, max_tokens a CAP
# --------------------------------------------------------------------------- #
# Captured from origin/main 0b43ebb's chunkers.py (before size_unit existed): the
# CHAR path (no token budget) on the fixture below, chunk_size=100,
# chunk_overlap=30. The pre-#318 token path (pack to max_tokens, ignore
# chunk_size) is deliberately NOT pinned: it was removed (owner, 2026-10-23 — no
# production sentence/words collection was built with it).
SNAP_TEXT = _prose(24) + " " + "q" * 300 + " " + _prose(6)
SNAPSHOT: dict[str, list[tuple[int, int]]] = {
    "SentenceChunker": [(0, 85), (85, 175), (175, 242), (242, 329), (329, 419), (419, 495), (495, 572), (572, 659), (659, 744), (744, 812), (812, 897), (897, 987), (987, 1373), (1373, 1463), (1463, 1529)],  # noqa: E501
    "WordChunker": [(0, 98), (70, 170), (140, 237), (211, 309), (284, 379), (354, 453), (428, 526), (498, 590), (561, 659), (631, 729), (708, 802), (775, 875), (851, 942), (918, 987), (961, 987), (965, 987), (973, 987), (982, 987), (987, 1288), (1288, 1386), (1358, 1458), (1428, 1525), (1499, 1529)],  # noqa: E501
}
_BY_NAME = {"SentenceChunker": SentenceChunker, "WordChunker": WordChunker}


def _spans(chunks) -> list[tuple[int, int]]:
    return [(c.start_char, c.end_char) for c in chunks]


@pytest.mark.parametrize("name", sorted(SNAPSHOT))
@pytest.mark.parametrize("explicit", [False, True])
def test_chars_without_cap_is_byte_identical_to_main(name, explicit):
    kwargs: dict = {"chunk_size": 100, "chunk_overlap": 30}
    if explicit:
        kwargs["size_unit"] = "chars"
    chunks = _BY_NAME[name](**kwargs).chunk(_doc(SNAP_TEXT, doc_id="snap"))
    assert _spans(chunks) == SNAPSHOT[name]


@pytest.mark.parametrize("name", sorted(SNAPSHOT))
@pytest.mark.parametrize("budget_mode", ["joined", "summed"])
def test_chars_with_a_cap_no_chunk_reaches_is_byte_identical(name, budget_mode):
    """A cap larger than every chunk changes nothing — the in-process sentence/
    words collections keep their chunks when the API starts resolving max_tokens."""
    doc = _doc(SNAP_TEXT, doc_id="snap")
    widest = max(COUNTER.count(doc.content[s:e]) for s, e in SNAPSHOT[name])
    chunks = _BY_NAME[name](
        chunk_size=100, chunk_overlap=30, max_tokens=widest, token_counter=COUNTER,
        budget_mode=budget_mode,
    ).chunk(doc)
    assert _spans(chunks) == SNAPSHOT[name]


@pytest.mark.parametrize("cls", CHUNKERS)
def test_chars_chunk_size_is_used_with_a_cap(cls):
    """The pre-#318 defect, inverted: chunk_size now matters on the capped path."""
    doc = _doc(_prose(300))
    small = cls(chunk_size=200, chunk_overlap=0, max_tokens=WINDOW, token_counter=COUNTER)
    large = cls(chunk_size=800, chunk_overlap=0, max_tokens=WINDOW, token_counter=COUNTER)
    a, b = small.chunk(doc), large.chunk(doc)
    assert len(a) > len(b)
    assert max(c.end_char - c.start_char for c in a) <= 200
    assert max(c.end_char - c.start_char for c in b) <= 800


def _units(cls, text: str) -> list[tuple[int, int]]:
    return sentence_spans(text) if cls is SentenceChunker else word_spans(text)


#: chunk_size (chars) and cap (tokens) for (c): a ~300-char prose chunk is
#: ~65-80 fake tokens, so a 64-token cap re-packs some chunks and leaves others.
_C_SIZE, _C_CAP = 300, 64


def _capped(cls, overlap: int) -> tuple[Document, list, list]:
    doc = _doc(CORPUS)
    ref = cls(chunk_size=_C_SIZE, chunk_overlap=overlap).chunk(doc)
    got = cls(
        chunk_size=_C_SIZE, chunk_overlap=overlap, max_tokens=_C_CAP, token_counter=COUNTER,
    ).chunk(doc)
    return doc, ref, got


@pytest.mark.parametrize("cls", CHUNKERS)
@pytest.mark.parametrize("overlap", [0, 40])
def test_chars_small_cap_every_chunk_within_cap(cls, overlap):
    doc, ref, got = _capped(cls, overlap)
    # The fixture must actually exercise both branches.
    assert any(COUNTER.count(c.content) > _C_CAP for c in ref)
    assert any(COUNTER.count(c.content) <= _C_CAP for c in ref)
    assert max(COUNTER.count(c.content) for c in got) <= _C_CAP
    assert all(c.content == doc.content[c.start_char : c.end_char] for c in got)


@pytest.mark.parametrize("cls", CHUNKERS)
def test_chars_small_cap_tiles_without_overlap(cls):
    doc, _ref, got = _capped(cls, 0)
    _assert_tiles(doc, got)


@pytest.mark.parametrize("cls", CHUNKERS)
@pytest.mark.parametrize("overlap", [0, 40])
def test_chars_small_cap_cuts_only_on_unit_edges(cls, overlap):
    """Every chunk edge is a unit edge, except a chunk lying wholly inside ONE unit
    that alone is over the cap (the only place a mid-unit cut is allowed)."""
    doc, _ref, got = _capped(cls, overlap)
    units = _units(cls, doc.content)
    edges = {s for s, _ in units} | {e for _, e in units}
    inside = 0
    for c in got:
        if c.start_char in edges and c.end_char in edges:
            continue
        host = [(s, e) for s, e in units if s <= c.start_char and c.end_char <= e]
        assert host, f"{c.start_char}:{c.end_char} cuts mid-unit across a unit edge"
        s, e = host[0]
        assert COUNTER.count(doc.content[s:e]) > _C_CAP, (
            f"{c.start_char}:{c.end_char} cuts inside unit {s}:{e}, which fits the cap"
        )
        inside += 1
    assert inside > 0  # GIANT_UNIT (and, for sentences, LONG_SENTENCE) was split


@pytest.mark.parametrize("cls", CHUNKERS)
@pytest.mark.parametrize("overlap", [0, 40])
def test_chars_small_cap_only_moves_over_cap_chunks(cls, overlap):
    """The cap re-packs a chunk only when it must: every char-packed chunk within
    the cap appears unchanged (so its char length is still <= chunk_size, or it is
    one oversize unit exactly as the char path emits it), and every other emitted
    chunk lies inside a char-packed chunk that was over the cap."""
    doc, ref, got = _capped(cls, overlap)
    ref_spans = _spans(ref)
    got_spans = set(_spans(got))
    over = [(s, e) for s, e in ref_spans if COUNTER.count(doc.content[s:e]) > _C_CAP]
    for s, e in ref_spans:
        if (s, e) not in over:
            assert (s, e) in got_spans
    for s, e in got_spans - set(ref_spans):
        assert any(os_ <= s and e <= oe for os_, oe in over), (s, e)
    for s, e in got_spans & set(ref_spans):
        if (s, e) not in over:
            assert e - s <= _C_SIZE or COUNTER.count(doc.content[s:e]) <= _C_CAP


def test_make_chunker_logs_no_size_warning(caplog):
    """The pre-#318 'chunk_size is not used' warning is gone: nothing is ambiguous."""
    import logging

    caplog.set_level(logging.WARNING, logger="ragstack.ingestion.chunkers")
    make_chunker("sentence", chunk_size=512, max_tokens=WINDOW, token_counter=COUNTER)
    assert not [r for r in caplog.records if r.name == "ragstack.ingestion.chunkers"]


# --------------------------------------------------------------------------- #
# (f) + (g): refusals at construction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cls", CHUNKERS)
def test_tokens_without_counter_raises(cls):
    with pytest.raises(ValueError, match="requires a token_counter"):
        cls(chunk_size=512, max_tokens=WINDOW, size_unit="tokens")


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_make_chunker_tokens_without_counter_raises(method):
    with pytest.raises(ValueError, match="requires a token_counter"):
        make_chunker(method, chunk_size=512, size_unit="tokens")


@pytest.mark.parametrize("cls", CHUNKERS)
@pytest.mark.parametrize("bad", ["token", "Tokens", "chars ", "bytes", ""])
def test_typod_size_unit_raises(cls, bad):
    with pytest.raises(ValueError, match="unknown size_unit"):
        cls(chunk_size=512, token_counter=COUNTER, size_unit=bad)


@pytest.mark.parametrize("method", ["fixed", "sentence", "words"])
def test_make_chunker_typod_size_unit_raises(method):
    with pytest.raises(ValueError, match="unknown size_unit"):
        make_chunker(method, chunk_size=512, token_counter=COUNTER, size_unit="token")


@pytest.mark.parametrize("cls", CHUNKERS)
def test_tokens_with_nonpositive_size_raises(cls):
    with pytest.raises(ValueError, match="chunk_size >= 1"):
        cls(chunk_size=0, token_counter=COUNTER, size_unit="tokens")


def test_size_units_constant():
    assert SIZE_UNITS == ("chars", "tokens")


# --------------------------------------------------------------------------- #
# (h): factory threading
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method,cls", [("sentence", SentenceChunker), ("words", WordChunker)])
def test_make_chunker_threads_size_unit(method, cls):
    ch = make_chunker(
        method, chunk_size=128, chunk_overlap=0, max_tokens=WINDOW,
        token_counter=COUNTER, size_unit="tokens",
    )
    assert isinstance(ch, cls)
    assert ch.size_unit == "tokens"
    chunks = ch.chunk(_doc(CORPUS))
    assert max(COUNTER.count(c.content) for c in chunks) <= 128
    # Default stays "chars".
    assert make_chunker(method, chunk_size=128).size_unit == "chars"


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_build_chunker_threads_size_unit(method):
    chunker, counter, resolved = build_chunker(
        method, chunk_size=128, chunk_overlap=0, token_backend="estimate",
    )
    assert resolved > 128  # build_chunker always resolves a window
    assert chunker.size_unit == "chars"  # type: ignore[attr-defined]

    chunker, counter, resolved = build_chunker(
        method, chunk_size=128, chunk_overlap=0, token_backend="estimate",
        size_unit="tokens",
    )
    assert chunker.size_unit == "tokens"  # type: ignore[attr-defined]
    chunks = chunker.chunk(_doc(CORPUS))  # type: ignore[attr-defined]
    assert max(counter.count(c.content) for c in chunks) <= 128
    _assert_tiles(_doc(CORPUS), chunks)


def test_build_chunker_default_is_char_sized_and_capped():
    """End to end through build_chunker (which always resolves a window): the
    default unit gives chunk_size-char chunks — not window-filled ones — capped."""
    chunker, counter, resolved = build_chunker(
        "sentence", chunk_size=128, chunk_overlap=0, token_backend="estimate",
    )
    chunks = chunker.chunk(_doc(_prose(900)))  # type: ignore[attr-defined]
    assert max(c.end_char - c.start_char for c in chunks) <= 128
    assert max(counter.count(c.content) for c in chunks) <= resolved
