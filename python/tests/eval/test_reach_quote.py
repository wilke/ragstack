"""reach_quote: the quote@10 coverage rule of the reach comparison (PREREG §3)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from reach_quote import (  # noqa: E402
    HIT_THRESHOLD,
    MIN_MATCH,
    cover_mask,
    is_verbatim,
    norm_ws,
    quote_cover,
    quote_hit,
)

QUOTE = ("To enable NIR imaging, the antibody was conjugated to the fluorophore IRDye 800CW "
         "and purified by size-exclusion chromatography before injection.")
PRE = "Unrelated preamble about the cohort and its recruitment. "
POST = " Unrelated text that follows and talks about something else entirely."


def test_constants():
    assert HIT_THRESHOLD == 0.90
    assert MIN_MATCH == 20


def test_norm_ws():
    assert norm_ws("  a\t b\n\nc d e  ") == "a b c d e"


def test_whole_quote_in_one_chunk():
    assert quote_cover(QUOTE, ["x", PRE + QUOTE + POST]) == 1.0
    assert quote_hit(QUOTE, [PRE + QUOTE + POST])


def test_no_texts_or_empty_quote():
    assert quote_cover(QUOTE, []) == 0.0
    assert quote_cover("   ", [QUOTE]) == 0.0
    assert quote_cover(QUOTE, ["", "   "]) == 0.0


def test_whitespace_differences_ignored():
    spaced = QUOTE.replace(" ", "  \n ")
    text = PRE + QUOTE.replace(", ", ",\n\t") + POST
    assert quote_cover(spaced, [text]) == 1.0
    assert is_verbatim(spaced, text)


@pytest.mark.parametrize("cut", [5, 37, 70, len(QUOTE) - 8])
def test_split_across_two_chunks_counts(cut):
    """A chunk boundary inside the quote: one chunk ends with the head, the next starts with the
    tail. Chunk texts are stored stripped, so the boundary space belongs to neither."""
    head, tail = QUOTE[:cut], QUOTE[cut:]
    a = (PRE + head).strip()
    b = (tail + POST).strip()
    assert quote_cover(QUOTE, [a, b]) == 1.0
    assert quote_hit(QUOTE, [b, "noise text", a])
    # either half alone is not a hit (each covers less than 90 %)
    assert not quote_hit(QUOTE, [a]) or len(head) >= 0.9 * len(norm_ws(QUOTE))
    assert not quote_hit(QUOTE, [b]) or len(tail) >= 0.9 * len(norm_ws(QUOTE))


def test_split_across_three_chunks_counts():
    q = norm_ws(QUOTE)
    i, j = 40, 100
    texts = [(PRE + q[:i]).strip(), q[i:j].strip(), (q[j:] + POST).strip()]
    assert quote_cover(QUOTE, texts) == 1.0


def test_partial_below_threshold_is_not_a_hit():
    q = norm_ws(QUOTE)
    part = q[: int(0.80 * len(q))]
    cov = quote_cover(QUOTE, [PRE + part + " and then something different" + POST])
    assert 0.75 <= cov < HIT_THRESHOLD
    assert not quote_hit(QUOTE, [PRE + part + " and then something different" + POST])


def test_partial_above_threshold_is_a_hit():
    q = norm_ws(QUOTE)
    part = q[: int(0.95 * len(q)) + 1]
    t = PRE + part + "XYZ completely different ending" + POST
    assert quote_cover(QUOTE, [t]) >= HIT_THRESHOLD
    assert quote_hit(QUOTE, [t])


def test_one_character_edit_breaks_only_its_neighbourhood():
    q = norm_ws(QUOTE)
    k = len(q) // 2
    edited = q[:k] + ("#" if q[k] != "#" else "%") + q[k + 1:]
    cov = quote_cover(QUOTE, [PRE + edited + POST])
    assert cov == pytest.approx((len(q) - 1) / len(q))


def test_short_interior_overlap_does_not_count():
    """A 19-character run in the middle of an unrelated text is below MIN_MATCH."""
    q = norm_ws(QUOTE)
    frag = q[30:49]
    assert len(frag) == 19
    assert quote_cover(QUOTE, ["lorem#" + frag + "#ipsum"]) == 0.0
    frag20 = q[30:50]
    assert quote_cover(QUOTE, ["lorem#" + frag20 + "#ipsum"]) == pytest.approx(20 / len(q))


def test_boundary_pieces_shorter_than_min_match_count():
    q = norm_ws(QUOTE)
    a = PRE + q[:7]  # ends with a 7-character head
    b = q[7:] + POST
    assert quote_cover(QUOTE, [a, b]) == 1.0
    # the same 7 characters in the middle of a text do not count
    assert quote_cover(QUOTE, ["lorem#" + q[:7] + "#ipsum"]) == 0.0


def test_interior_union_is_exact():
    q = norm_ws(QUOTE)
    texts = ["aaa#" + q[10:40] + "#bbb", "ccc#" + q[35:70] + "#ddd", "eee#" + q[100:121] + "#fff"]
    _, mask = cover_mask(QUOTE, texts)
    expected = set(range(10, 70)) | set(range(100, 121))
    assert {i for i, m in enumerate(mask) if m} == expected


def test_short_quote_needs_full_containment():
    short = "IRDye 800CW dye"  # 15 characters < MIN_MATCH
    assert quote_cover(short, ["the IRDye 800CW dye was used"]) == 1.0
    assert quote_cover(short, ["the IRDye 800CW was used"]) == 0.0


def test_case_and_punctuation_are_significant():
    assert quote_cover(QUOTE, [QUOTE.upper()]) < HIT_THRESHOLD
    assert not is_verbatim(QUOTE, QUOTE.replace(",", ";"))


def test_order_of_texts_irrelevant():
    q = norm_ws(QUOTE)
    texts = [(PRE + q[:60]).strip(), (q[60:] + POST).strip(), "noise"]
    assert quote_cover(QUOTE, texts) == quote_cover(QUOTE, list(reversed(texts))) == 1.0


def test_min_match_must_be_positive():
    with pytest.raises(ValueError):
        quote_cover(QUOTE, [QUOTE], min_match=0)
