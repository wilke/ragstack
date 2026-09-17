"""The packed `yyyymmdd` publication date (docs/plans/date-filtering.md).

Until this landed, the only code that knew the rule lived in one-off backfill
scripts outside the repository — which is how three collections came to hold a
field nothing in the tree could produce or explain.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.enrich import packed_date, year_from_packed_date
from ragstack.metadata_schema import DECLARED_FIELDS, plausible_year_range


def test_date_is_declared_as_an_integer():
    """The field the stores already hold must be in the table, or the next
    producer is free to write a string into it — which is exactly how
    `year` became a keyword on 1.55M chunks."""
    assert DECLARED_FIELDS["date"].kind == "integer"
    assert DECLARED_FIELDS["date"].elasticsearch_mapping == {"type": "long"}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((1986, None, None), 19860000),   # year only — the open-access shape
        ((1982, 3, None), 19820300),      # year + month — the asm-semantic shape
        ((2015, 3, 1), 20150301),         # fully known
        ((1986, None, 15), 19860000),     # a day without a month is not a date
        (("1999", None, None), 19990000), # strings coerce
        ((2020, 13, 5), 20200000),        # out-of-range month drops it AND the day
        ((2020, 3, 99), 20200300),        # out-of-range day drops only the day
    ],
)
def test_packing(args, expected):
    assert packed_date(*args) == expected


@pytest.mark.parametrize("year", [None, "", "n/a", 1200, 9999, True, float("nan")])
def test_an_unusable_year_is_absent_not_zero(year):
    """None, never 0. A zero would sort before every real date and would read as
    a known date rather than a missing one."""
    assert packed_date(year) is None


def test_precision_is_self_describing():
    """The zeros ARE the precision field — this is why no second column exists."""
    assert packed_date(1986) % 10000 == 0            # no month
    assert packed_date(1982, 3) % 100 == 0           # no day
    assert packed_date(2015, 3, 1) % 100 != 0        # fully specified


def test_year_is_derived_not_captured():
    """`year == date // 10000` wherever both exist — verified across 61M chunks
    on 2026-09-17. Anything writing `year` separately can drift from `date`."""
    for y in (1986, 1982, 2015, plausible_year_range()[1]):
        assert year_from_packed_date(packed_date(y, 6, 15)) == y


@pytest.mark.parametrize("bad", [None, "", "x", True, 12, 999999999999])
def test_unpacking_refuses_what_is_not_a_packed_date(bad):
    assert year_from_packed_date(bad) is None
