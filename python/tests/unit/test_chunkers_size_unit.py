"""``size_unit``: ``chunk_size`` as a token budget for sentence/word chunking (#318 P1).

The defect: with a token budget (``max_tokens`` + ``token_counter``) the sentence
and word packers fill to ``max_tokens`` and never read ``chunk_size``. The bulk
ingest path always resolves ``max_tokens`` to the model window, so
``--chunk-method sentence --chunk-size 512`` produced ~4080-token chunks.

The fix is opt-in (``size_unit="tokens"``) because flipping the default re-sizes
every existing invocation. These tests pin both halves: the opt-in honours
``chunk_size`` as a hard **maximum** — including for a single unit longer than
``chunk_size``, which is the case the naive fix gets wrong — and the default is
byte-identical to the code before the knob existed.

``SpaceMergeCounter`` (a deterministic BPE-like fake) comes from the fill tests,
so no tokenizer is downloaded.
"""
from __future__ import annotations

import logging

import pytest

from ragstack.ingestion.chunker_config import build_chunker
from ragstack.ingestion.chunkers import (
    SIZE_UNITS,
    SentenceChunker,
    WordChunker,
    make_chunker,
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
# (d): the default (size_unit="chars") is byte-identical to before the knob
# --------------------------------------------------------------------------- #
# Captured from origin/main 0b43ebb's chunkers.py (before size_unit existed) on
# the fixture below — chunk_size=100, chunk_overlap=30, and on the token path
# max_tokens=40 with SpaceMergeCounter. chunk_size is IGNORED on the token path
# (the defect, preserved by default): that is what these rows pin.
SNAP_TEXT = _prose(24) + " " + "q" * 300 + " " + _prose(6)
SNAPSHOT: dict[tuple[str, str | None], list[tuple[int, int]]] = {
    ('SentenceChunker', None): [(0, 85), (85, 175), (175, 242), (242, 329), (329, 419), (419, 495), (495, 572), (572, 659), (659, 744), (744, 812), (812, 897), (897, 987), (987, 1373), (1373, 1463), (1463, 1529)],  # noqa: E501
    ('SentenceChunker', 'joined'): [(0, 130), (130, 242), (242, 373), (373, 495), (495, 616), (616, 744), (744, 812), (812, 942), (942, 987), (987, 1143), (1143, 1299), (1299, 1373), (1373, 1499), (1499, 1529)],  # noqa: E501
    ('SentenceChunker', 'summed'): [(0, 130), (130, 242), (242, 373), (373, 495), (495, 616), (616, 744), (744, 812), (812, 942), (942, 987), (987, 1143), (1143, 1299), (1299, 1373), (1373, 1499), (1499, 1529)],  # noqa: E501
    ('WordChunker', None): [(0, 98), (70, 170), (140, 237), (211, 309), (284, 379), (354, 453), (428, 526), (498, 590), (561, 659), (631, 729), (708, 802), (775, 875), (851, 942), (918, 987), (961, 987), (965, 987), (973, 987), (982, 987), (987, 1288), (1288, 1386), (1358, 1458), (1428, 1525), (1499, 1529)],  # noqa: E501
    ('WordChunker', 'joined'): [(0, 140), (116, 265), (237, 373), (348, 498), (470, 609), (581, 720), (690, 832), (802, 942), (918, 987), (961, 987), (965, 987), (973, 987), (982, 987), (987, 1143), (1143, 1288), (1288, 1428), (1404, 1529)],  # noqa: E501
    ('WordChunker', 'summed'): [(0, 92), (63, 161), (140, 231), (203, 291), (265, 362), (339, 432), (406, 502), (474, 561), (532, 625), (600, 690), (667, 762), (733, 816), (791, 882), (854, 952), (928, 987), (961, 987), (965, 987), (973, 987), (982, 987), (987, 1143), (1143, 1288), (1288, 1380), (1351, 1449), (1428, 1519), (1491, 1529)],  # noqa: E501
}


@pytest.mark.parametrize("key", sorted(SNAPSHOT, key=repr))
@pytest.mark.parametrize("explicit", [False, True])
def test_default_size_unit_is_byte_identical(key, explicit):
    cls_name, mode = key
    cls = {"SentenceChunker": SentenceChunker, "WordChunker": WordChunker}[cls_name]
    kwargs: dict = {"chunk_size": 100, "chunk_overlap": 30}
    if mode is not None:
        kwargs.update(max_tokens=40, token_counter=COUNTER, budget_mode=mode)
    if explicit:
        kwargs["size_unit"] = "chars"
    chunks = cls(**kwargs).chunk(_doc(SNAP_TEXT, doc_id="snap"))
    assert [(c.start_char, c.end_char) for c in chunks] == SNAPSHOT[key]


def test_snapshot_shows_chunk_size_is_dead_on_the_default_token_path():
    """The row the opt-in exists for: on the default token path chunk_size has no
    effect at all — 100 and 10**9 give the same chunks."""
    doc = _doc(SNAP_TEXT, doc_id="snap")
    a = SentenceChunker(chunk_size=100, chunk_overlap=30, max_tokens=40, token_counter=COUNTER)
    b = SentenceChunker(chunk_size=10**9, chunk_overlap=30, max_tokens=40, token_counter=COUNTER)
    assert [(c.start_char, c.end_char) for c in a.chunk(doc)] == [
        (c.start_char, c.end_char) for c in b.chunk(doc)
    ]


# --------------------------------------------------------------------------- #
# (e): make_chunker warns exactly on the ambiguous combination
# --------------------------------------------------------------------------- #
_LOGGER = "ragstack.ingestion.chunkers"


def _warnings(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING and "chunk_size" in r.getMessage()
    ]


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_warns_on_ambiguous_combination(caplog, method):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    make_chunker(method, chunk_size=512, max_tokens=WINDOW, token_counter=COUNTER)
    recs = _warnings(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert "512" in msg and str(WINDOW) in msg
    assert "size_unit='tokens'" in msg


@pytest.mark.parametrize(
    "method,kwargs",
    [
        # opted in → chunk_size IS used
        ("sentence", {"max_tokens": WINDOW, "token_counter": COUNTER, "size_unit": "tokens"}),
        ("words", {"max_tokens": WINDOW, "token_counter": COUNTER, "size_unit": "tokens"}),
        # no token budget → chunk_size (chars) IS used
        ("sentence", {}),
        ("words", {"token_counter": COUNTER}),
        # max_tokens without a counter → the char path, chunk_size IS used
        ("sentence", {"max_tokens": WINDOW}),
        # chunk_size=-1 is "disable chunking", not a size
        ("sentence", {"max_tokens": WINDOW, "token_counter": COUNTER, "chunk_size": -1}),
        # other methods have their own sizing
        ("fixed", {"max_tokens": WINDOW, "token_counter": COUNTER}),
    ],
)
def test_no_warning_off_the_ambiguous_combination(caplog, method, kwargs):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    kwargs = {"chunk_size": 512, **kwargs}
    make_chunker(method, **kwargs)
    assert _warnings(caplog) == []


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


def test_build_chunker_default_is_window_sized_and_warns(caplog):
    """The production defect, end to end through build_chunker: chunk_size=128
    under the default unit yields chunks far larger than 128 — and says so."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    chunker, counter, resolved = build_chunker(
        "sentence", chunk_size=128, chunk_overlap=0, token_backend="estimate",
    )
    assert len(_warnings(caplog)) == 1
    chunks = chunker.chunk(_doc(_prose(900)))  # type: ignore[attr-defined]
    assert max(counter.count(c.content) for c in chunks) > 128
    assert max(counter.count(c.content) for c in chunks) <= resolved
