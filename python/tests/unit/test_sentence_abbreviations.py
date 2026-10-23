"""Sentence segmentation with biomedical/scholarly abbreviations (#318 P2).

The gold set (``fixtures/sentence_gold_biomedical.json``) is 20 hand-written
passages with their expected sentence units. Both backends — NLTK Punkt seeded
with :data:`chunkers.SENTENCE_ABBREVIATIONS`, and the regex fallback that
mirrors it — must reproduce every passage exactly. Before #318 P2 bare Punkt got
5/20 and the regex 3/20.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack import provenance
from ragstack.ingestion import chunkers

GOLD = json.loads(
    (Path(__file__).parent / "fixtures" / "sentence_gold_biomedical.json").read_text("utf-8"))
PASSAGES = GOLD["passages"]
IDS = [p["id"] for p in PASSAGES]

needs_nltk = pytest.mark.skipif(
    chunkers._punkt_sentence_spans("A b. C d.") is None, reason="nltk not installed")


def _text(p: dict) -> str:
    return " ".join(p["sentences"])


def _units(text: str, spans: list[tuple[int, int]]) -> list[str]:
    return [text[s:e].strip() for s, e in spans]


def _assert_tiles(text: str, spans: list[tuple[int, int]]) -> None:
    assert spans, "no spans"
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (_, e), (s, _) in zip(spans, spans[1:], strict=False):
        assert e == s
    assert "".join(text[s:e] for s, e in spans) == text


@pytest.fixture
def no_nltk(monkeypatch):
    """Make ``import nltk`` fail, as on an install without the [chunking] extra."""
    monkeypatch.setitem(sys.modules, "nltk", None)
    assert chunkers._punkt_sentence_spans("A b. C d.") is None


def test_gold_set_shape():
    assert len(PASSAGES) == 20
    assert len(set(IDS)) == 20
    groups = {g for p in PASSAGES for g in p["groups"]}
    # Every abbreviation group of SENTENCE_ABBREVIATIONS, the initial and number
    # heuristics, and true sentence ends after abbreviation-looking tokens.
    assert {"honorifics", "latin", "document-pointers", "measures", "routes",
            "taxonomy", "journals", "institutions", "months", "genus-initials",
            "number-heuristic", "negative"} <= groups


@needs_nltk
@pytest.mark.parametrize("passage", PASSAGES, ids=IDS)
def test_gold_punkt(passage):
    text = _text(passage)
    spans = chunkers._punkt_sentence_spans(text)
    assert spans is not None
    assert _units(text, spans) == passage["sentences"]


@pytest.mark.parametrize("passage", PASSAGES, ids=IDS)
def test_gold_regex_fallback(passage, no_nltk):
    text = _text(passage)
    spans = chunkers.sentence_spans(text)  # falls back: nltk is unimportable
    assert _units(text, spans) == passage["sentences"]
    assert spans == chunkers._fallback_sentence_spans(text)


@needs_nltk
def test_gold_backends_agree():
    for p in PASSAGES:
        t = _text(p)
        assert chunkers._punkt_sentence_spans(t) == chunkers._fallback_sentence_spans(t), p["id"]


@pytest.mark.parametrize("backend", ["punkt", "regex"])
def test_sentence_spans_still_tile(backend, monkeypatch):
    if backend == "regex":
        monkeypatch.setitem(sys.modules, "nltk", None)
    elif chunkers._punkt_sentence_spans("A b. C d.") is None:
        pytest.skip("nltk not installed")
    texts = [_text(p) for p in PASSAGES] + list(provenance.CANONICAL_SEGMENTATION_SAMPLE)
    texts += ["  leading space. Fig. 2 trailing  ", "e.g.", "Dr.", "x\n\n\nFig. 3.\n\n"]
    for t in texts:
        _assert_tiles(t, chunkers.sentence_spans(t))


def test_subsplit_long_spans_unchanged():
    """A no-punctuation blob over _LONG_SPAN_CHARS is still sub-split exactly as
    ``_subsplit_span`` would; abbreviations do not reach that path."""
    blob = provenance.CANONICAL_SEGMENTATION_SAMPLE[3]
    assert len(blob) > chunkers._LONG_SPAN_CHARS and "." not in blob
    expected = chunkers._subsplit_span(blob, 0, len(blob))
    assert len(expected) > 1
    assert chunkers.sentence_spans(blob) == expected
    # A long span made only of abbreviation-joined text is one sentence for the
    # detector, so it reaches the sub-splitter whole.
    long_ = " ".join(["Fig. 2 vs. Fig. 3 et al. i.e. approx. 5"] * 80)
    assert len(long_) > chunkers._LONG_SPAN_CHARS
    assert chunkers._fallback_sentence_spans(long_) == [(0, len(long_))]
    assert chunkers._subsplit_long_spans(long_, [(0, len(long_))]) == \
        chunkers._subsplit_span(long_, 0, len(long_))


@needs_nltk
def test_canonical_sample_first_text_is_three_sentences():
    """CANONICAL_SEGMENTATION_SAMPLE[0] was written to catch exactly this
    defect: bare Punkt made 8 units of it ("Dr." / "Smith et al." / …)."""
    t = provenance.CANONICAL_SEGMENTATION_SAMPLE[0]
    assert _units(t, chunkers.sentence_spans(t)) == [
        "Dr. Smith et al. reported a 2.5-fold increase (p < 0.05).",
        "Fig. 2 shows the effect in vivo, i.e. in mice.",
        "The U.S. cohort was smaller.",
    ]


def test_canonical_sample_first_text_is_three_sentences_regex(no_nltk):
    t = provenance.CANONICAL_SEGMENTATION_SAMPLE[0]
    assert len(chunkers.sentence_spans(t)) == 3


def test_abbreviation_entries_are_punkt_shaped():
    assert 100 <= len(chunkers.SENTENCE_ABBREVIATIONS) <= 140
    for a in chunkers.SENTENCE_ABBREVIATIONS:
        assert a == a.lower() and a.strip() == a and " " not in a, a
        assert not a.endswith("."), a
        # Single letters are left to Punkt's initial heuristic (and its regex
        # mirror); as entries they would only merge "group B. 3 patients".
        assert len(a) > 1, a
    # Deliberate exclusions: their period form is mostly a sentence end.
    for excluded in ("min", "max", "sec", "ed", "eds", "ms", "am", "resp"):
        assert excluded not in chunkers.SENTENCE_ABBREVIATIONS, excluded


@needs_nltk
def test_punkt_needs_no_data(monkeypatch):
    """The seeded tokenizer is built from PunktParameters in code: building it
    must not touch nltk.data (no punkt download, no data lookup)."""
    import nltk

    def _no_data(*a, **k):
        raise AssertionError("nltk.data accessed")

    monkeypatch.setattr(nltk.data, "load", _no_data)
    monkeypatch.setattr(nltk.data, "find", _no_data)
    monkeypatch.setattr(chunkers, "_PUNKT_TOKENIZER", None)
    t = provenance.CANONICAL_SEGMENTATION_SAMPLE[0]
    assert len(chunkers._punkt_sentence_spans(t) or []) == 3
    tok = chunkers._PUNKT_TOKENIZER
    assert tok is not None
    assert tok._params.abbrev_types == set(chunkers.SENTENCE_ABBREVIATIONS)
    chunkers._punkt_sentence_spans("Again. And again.")
    assert chunkers._PUNKT_TOKENIZER is tok  # built once, reused



PARAGRAPH_CASES = [
    # (text, expected units): an abbreviation never swallows a paragraph break.
    ("Shoulder pain, back pain, etc.\n\nTherefore, the review is needed.",
     ["Shoulder pain, back pain, etc.", "Therefore, the review is needed."]),
    ("It indexes the burden of MR.\n\nExercise echo can help.",
     ["It indexes the burden of MR.", "Exercise echo can help."]),
    ("As in (Fig. 2).\n  \nThe next paragraph. Dr. Smith agreed.",
     ["As in (Fig. 2).", "The next paragraph.", "Dr. Smith agreed."]),
    ("See Smith et al.\n\n", ["See Smith et al."]),
    # A single newline is not a paragraph break: the abbreviation still holds.
    ("Smith et al.\nreported it.", ["Smith et al.\nreported it."]),
]


@needs_nltk
@pytest.mark.parametrize(("text", "units"), PARAGRAPH_CASES)
def test_abbreviation_keeps_paragraph_break_punkt(text, units):
    spans = chunkers._punkt_sentence_spans(text)
    assert spans is not None
    _assert_tiles(text, spans)
    assert _units(text, spans) == units


@pytest.mark.parametrize(("text", "units"), PARAGRAPH_CASES)
def test_abbreviation_keeps_paragraph_break_regex(text, units, no_nltk):
    spans = chunkers.sentence_spans(text)
    _assert_tiles(text, spans)
    assert _units(text, spans) == units


def test_token_lookup_is_bounded():
    """A period after a huge no-space run is not mistaken for an abbreviation
    (and the lookup stays local, so a multi-MB blob is not quadratic)."""
    blob = "x" * 5000 + "fig. Next one. " + "y" * 100 + "-fig. Done."
    spans = chunkers._fallback_sentence_spans(blob)
    _assert_tiles(blob, spans)
    # "xxxx…fig." is not "fig." — a break; "yyyy…-fig." ends in the "fig" part,
    # which Punkt also matches (tok.split("-")[-1]) — no break.
    assert len(spans) == 3
    if chunkers._punkt_sentence_spans("A b. C d.") is not None:
        assert chunkers._punkt_sentence_spans(blob) == spans
