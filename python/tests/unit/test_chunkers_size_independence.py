"""Tests for size-independent chunking (issues #77, #78).

Covers the three fixes that stop chunking cost from scaling super-linearly with
document size and stop a no-punctuation blob from becoming one giant span:

- Fix C: :func:`split_text_to_token_budget` is O(n) via the HF fast tokenizer's
  offset mapping (with a bounded, still-lossless fallback for offset-less
  counters), instead of the old O(n^2) prefix-rescanning binary search.
- Fix D: :func:`sentence_spans` sub-splits very long no-punctuation spans on
  secondary separators (newline/tab/``;``/whitespace) so a data table yields
  many units, while normal prose is unchanged.

The tokenizer is faked (whitespace tokens with exact offset mappings) so a token
budget maps to a word count we can reason about exactly — no model download.
"""
from __future__ import annotations

import re
import time

from ragstack.ingestion.chunkers import (
    _split_by_estimate,
    sentence_spans,
    split_text_to_token_budget,
)

_WORD_RE = re.compile(r"\S+")


class _FakeFastTokenizer:
    """Whitespace tokenizer with an offset mapping, mimicking an HF fast tokenizer."""

    def __call__(self, text, return_offsets_mapping=False, add_special_tokens=True):
        offsets = [(m.start(), m.end()) for m in _WORD_RE.finditer(text)]
        return {"offset_mapping": offsets}

    def encode(self, text, add_special_tokens=True):
        return _WORD_RE.findall(text)


class HFLikeCounter:
    """Fake HFTokenCounter: token = whitespace-run, exposes callable ``_tokenizer``."""

    def __init__(self) -> None:
        self._tok = _FakeFastTokenizer()

    def _tokenizer(self):
        return self._tok

    def count(self, text: str) -> int:
        return len(_WORD_RE.findall(text))


class EstimateCounter:
    """Offset-less counter (like the estimate/endpoint backends): count = words."""

    def count(self, text: str) -> int:
        return len(text.split())


# --------------------------------------------------------------------------- #
# Fix C: split_text_to_token_budget via offsets (O(n))
# --------------------------------------------------------------------------- #
def test_offset_split_lossless_and_within_budget():
    counter = HFLikeCounter()
    text = " ".join(f"w{i}" for i in range(50))  # 50 tokens
    pieces = split_text_to_token_budget(text, max_tokens=7, token_counter=counter)
    assert "".join(pieces) == text  # lossless: pieces tile the input exactly
    assert all(counter.count(p) <= 7 for p in pieces)
    assert len(pieces) >= 7


def test_offset_split_short_text_unchanged():
    counter = HFLikeCounter()
    text = "a b c"
    assert split_text_to_token_budget(text, 10, counter) == [text]


def test_offset_split_preserves_all_whitespace():
    # Irregular inter-token whitespace (tabs, runs) must survive concatenation.
    counter = HFLikeCounter()
    text = "aa\t\tbb    cc\ndd  ee ff gg hh"
    pieces = split_text_to_token_budget(text, 2, counter)
    assert "".join(pieces) == text
    assert all(counter.count(p) <= 2 for p in pieces)


def test_offset_split_single_char_per_token_budget():
    counter = HFLikeCounter()
    text = "a b c d"
    pieces = split_text_to_token_budget(text, 1, counter)
    assert "".join(pieces) == text
    assert all(counter.count(p) <= 1 for p in pieces)
    assert len(pieces) == 4


# --------------------------------------------------------------------------- #
# Fix C: bounded fallback for offset-less counters (estimate/endpoint)
# --------------------------------------------------------------------------- #
def test_estimate_fallback_lossless_and_within_budget():
    counter = EstimateCounter()
    text = " ".join(f"w{i}" for i in range(60))
    pieces = split_text_to_token_budget(text, max_tokens=8, token_counter=counter)
    assert "".join(pieces) == text
    assert all(counter.count(p) <= 8 for p in pieces)


def test_estimate_fallback_direct_makes_progress():
    counter = EstimateCounter()
    text = " ".join(f"w{i}" for i in range(30))
    pieces = _split_by_estimate(text, 5, counter)
    assert "".join(pieces) == text
    assert all(counter.count(p) <= 5 for p in pieces)


# --------------------------------------------------------------------------- #
# Fix C: O(n) scaling — the whole point. A big blob must split fast.
# --------------------------------------------------------------------------- #
def test_offset_split_scales_linearly_on_large_blob():
    counter = HFLikeCounter()
    # ~500k "tokens": the old O(n^2) prefix rescan would take many seconds; the
    # offset path is a single tokenization + linear slice.
    text = " ".join(f"w{i}" for i in range(500_000))
    t0 = time.perf_counter()
    pieces = split_text_to_token_budget(text, max_tokens=4080, token_counter=counter)
    elapsed = time.perf_counter() - t0
    assert "".join(pieces) == text
    assert all(counter.count(p) <= 4080 for p in pieces)
    assert elapsed < 10.0, f"offset split took {elapsed:.1f}s (expected O(n), < 10s)"


# --------------------------------------------------------------------------- #
# Fix D: sentence_spans sub-splits long no-punctuation spans
# --------------------------------------------------------------------------- #
def test_no_punctuation_blob_yields_many_spans():
    # A newline-delimited data table with NO sentence punctuation: one giant span
    # before the fix, many after.
    rows = "\n".join(f"col_a_{i}\tcol_b_{i}\tcol_c_{i}" for i in range(2000))
    spans = sentence_spans(rows)
    assert len(spans) > 1
    # Lossless: spans tile the source exactly, gaplessly.
    assert spans[0][0] == 0
    assert spans[-1][1] == len(rows)
    for a, b in zip(spans, spans[1:], strict=False):
        assert a[1] == b[0]
    assert "".join(rows[s:e] for s, e in spans) == rows


def test_whitespace_only_blob_still_subsplits():
    # No newline/tab/semicolon — only spaces. Whitespace is the last-resort sep.
    blob = " ".join(f"token{i}" for i in range(3000))
    spans = sentence_spans(blob)
    assert len(spans) > 1
    assert "".join(blob[s:e] for s, e in spans) == blob


def test_normal_prose_is_unchanged_by_subsplit():
    # Ordinary sentences are well under the long-span threshold, so Fix D is a
    # no-op: the same spans a plain sentence splitter would produce.
    prose = (
        "The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. "
        "How vexingly quick daft zebras jump!"
    )
    spans = sentence_spans(prose)
    # 3 sentences, none sub-split.
    assert len(spans) == 3
    assert "".join(prose[s:e] for s, e in spans) == prose


def test_semicolon_separated_long_span_subsplits():
    blob = ";".join(f"field_{i}=value_{i}" for i in range(500))
    spans = sentence_spans(blob)
    assert len(spans) > 1
    assert "".join(blob[s:e] for s, e in spans) == blob
