"""The bucketed histogram behind the rollup line (#427 W4).

Four things are worth testing here and everything else is bookkeeping:

* **bucket assignment at the boundaries**, because that is the one place a
  one-character change (``bisect_left`` -> ``bisect_right``) silently shifts
  every reported percentile up by one bucket and nothing else notices;
* **that a percentile is a bucket UPPER BOUND**, because the moment somebody
  "improves" it into a linear interpolation the line starts printing four digits
  of precision this instrument does not have;
* **that the series cap stops the map growing**, because ``collection`` is a
  user-created axis and the cap is the only thing between it and a process that
  grows all week;
* **the route allowlist**, because the middleware sees the raw path and an
  unfiltered histogram would spend its whole budget on ``<job_id>``-shaped
  paths.
"""
import math

import pytest

from ragstack.observability.context import MISSING
from ragstack.observability.histogram import (
    ALLOWED_ROUTES,
    BOUNDS,
    MAX_SERIES,
    WALL_STAGE,
    LatencyHistogram,
    Series,
    _bucket,
    render_bound,
    route_key,
)

ROUTE = "POST /v1/query"


# --------------------------------------------------------------------------- #
# Bucket assignment, and specifically the boundaries
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("index", "bound"), list(enumerate(BOUNDS)))
def test_a_value_exactly_on_a_boundary_lands_in_the_bucket_that_bound_closes(index, bound):
    """**Which side: the LOWER one.** Intervals are ``(lower, upper]``, so a
    value exactly equal to a boundary belongs to the bucket that boundary
    *closes*, not to the one above it. Concretely: ``5.0`` seconds is reported as
    "at most 5000 ms", never as "at most 10000 ms".

    This is the assertion that pins ``bisect_left``. Swap it for ``bisect_right``
    and every one of these values moves up one bucket — a change that inflates
    every reported percentile at exactly the round numbers a human would use to
    sanity-check the output, and which no other test in this suite would see.
    """
    assert _bucket(bound) == index

    series = Series()
    series.observe(bound)
    assert series.percentile(0.50) == bound
    assert series.counts[index] == 1
    assert sum(series.counts) == 1


@pytest.mark.parametrize(("index", "bound"), list(enumerate(BOUNDS)))
def test_a_value_just_above_a_boundary_lands_in_the_next_bucket_up(index, bound):
    """The other side of the same edge, so ``(lower, upper]`` is pinned from
    both directions rather than only from the one that happens to hold."""
    assert _bucket(bound * 1.000001) == index + 1


def test_a_value_above_every_boundary_lands_in_the_unbounded_top_bucket():
    series = Series()
    series.observe(500.0)
    assert series.counts[-1] == 1
    assert math.isinf(series.percentile(0.50))
    assert render_bound(series.percentile(0.50)) == "inf", (
        "the top bucket has no upper bound; rendering 120000.0 there would "
        "understate a 500-second request by a factor of four"
    )


def test_a_nonsensical_duration_is_clamped_rather_than_raising():
    """This runs in a ``finally``. A ``ValueError`` out of the histogram would
    replace whatever exception the request was already failing with."""
    for value in (0.0, -1.0, float("nan"), float("inf")):
        assert _bucket(value) in range(len(BOUNDS) + 1)


# --------------------------------------------------------------------------- #
# Percentiles are bucket bounds, not interpolations
# --------------------------------------------------------------------------- #


def test_the_percentile_is_the_bucket_upper_bound_and_not_an_interpolation():
    """100 observations spread across ``(2.5, 5]`` — every one of them a
    different value, none of them equal to the bound.

    An interpolating implementation would answer something like ``4.2``; the
    honest answer is ``5.0``, because a bucketed histogram does not know where
    inside ``(2.5 s, 5 s]`` those observations sat. The assertion is equality
    with the bound, which fails for any interpolation however good.
    """
    series = Series()
    for i in range(100):
        series.observe(2.6 + i * 0.02)  # 2.60 .. 4.58, all inside (2.5, 5]
    assert series.percentile(0.50) == 5.0
    assert series.percentile(0.95) == 5.0
    assert series.maximum == pytest.approx(4.58)
    assert render_bound(series.percentile(0.95)) == "5000.0"


def test_the_percentile_moves_to_the_bucket_the_tail_actually_reaches():
    """95 fast requests and 5 slow ones: p50 stays low, p95 reports the slow
    bucket. This is the creeping-bound signal in miniature — the whole point of
    the instrument is that the p95 column moves while p50 does not."""
    series = Series()
    for _ in range(95):
        series.observe(0.02)
    for _ in range(5):
        series.observe(25.0)
    assert series.percentile(0.50) == 0.025
    assert series.percentile(0.95) == 0.025
    assert series.percentile(0.96) == 30.0, (
        "the 5 slow requests are the top 5%; the 96th percentile must reach them"
    )
    assert series.maximum == 25.0


def test_an_empty_series_reports_zero_rather_than_inventing_a_percentile():
    assert Series().percentile(0.95) == 0.0


# --------------------------------------------------------------------------- #
# The cap: overflow counter, NOT a growing map
# --------------------------------------------------------------------------- #


def test_the_cap_increments_the_overflow_counter_rather_than_growing_the_map():
    """The assertion that matters is ``hist.size == MAX_SERIES`` — the *map size*,
    not the counter. A "cap" that logs a warning and then inserts anyway would
    satisfy an overflow-counter-only assertion while leaking exactly as before.

    Driven with one series per request (a unique collection, wall only), so the
    arithmetic is exact: request k creates series k.
    """
    hist = LatencyHistogram()
    for i in range(MAX_SERIES):
        hist.record(ROUTE, f"coll_{i}", 0.1)
    assert hist.size == MAX_SERIES
    assert hist.overflow == 0

    for i in range(MAX_SERIES, MAX_SERIES + 50):
        hist.record(ROUTE, f"coll_{i}", 0.1)

    assert hist.size == MAX_SERIES, "the map grew past the cap"
    assert hist.overflow == 50
    assert hist.get(ROUTE, f"coll_{MAX_SERIES}", WALL_STAGE) is None


def test_a_series_that_already_exists_keeps_recording_after_the_cap_is_reached():
    """The cap must stop NEW series, not stop the histogram. A cap that froze
    every counter would quietly turn the rollup line into a stale snapshot on
    exactly the busy deployments that reach it."""
    hist = LatencyHistogram()
    for i in range(MAX_SERIES):
        hist.record(ROUTE, f"coll_{i}", 0.1)
    hist.record(ROUTE, "coll_0", 0.1)
    hist.record(ROUTE, "overflowing", 0.1)

    established = hist.get(ROUTE, "coll_0", WALL_STAGE)
    assert established is not None
    assert established.count == 2
    assert hist.overflow == 1


def test_the_stage_axis_counts_against_the_same_cap():
    """One request with many stages consumes many series. Stated as a test
    because it is the thing that makes ``MAX_SERIES`` a per-collection budget of
    ``1 + len(stages)`` rather than of 1."""
    hist = LatencyHistogram()
    hist.record(ROUTE, "c", 1.0, {"embed": (0.1, 1), "vector": (0.9, 1)})
    assert hist.size == 3
    assert {stage for _, _, stages in hist.groups() for stage in stages} == {
        WALL_STAGE,
        "embed",
        "vector",
    }


# --------------------------------------------------------------------------- #
# What gets recorded
# --------------------------------------------------------------------------- #


def test_a_stage_observation_is_the_per_request_sum_not_the_per_call_time():
    """Five legs of 9 s under fan-out is ONE observation of 45 s, not five of 9.

    Documented in the module docstring and asserted here because it is the kind
    of thing a later refactor would "fix" by dividing by the count — which would
    turn "how much vector time does a query cost me" into a per-leg number that
    no longer sums to anything an operator can compare against ``wall``.
    """
    hist = LatencyHistogram()
    hist.record(ROUTE, "c", 10.0, {"vector": (45.0, 5)})
    series = hist.get(ROUTE, "c", "vector")
    assert series is not None
    assert series.count == 1
    assert series.total == 45.0


def test_errors_are_counted_on_the_wall_series_only():
    """"Was it an error" is a property of the REQUEST, so counting it on each
    stage would multiply one failure by the number of legs it happened to have
    timed before it fell over."""
    hist = LatencyHistogram()
    hist.record(ROUTE, "c", 30.0, {"vector": (30.0, 1)}, is_error=True)
    hist.record(ROUTE, "c", 0.2, {"vector": (0.1, 1)}, is_error=False)

    wall = hist.get(ROUTE, "c", WALL_STAGE)
    vector = hist.get(ROUTE, "c", "vector")
    assert wall is not None and vector is not None
    assert wall.count == 2
    assert wall.errors == 1
    assert vector.errors == 0


def test_an_unscoped_request_is_labelled_rather_than_dropped():
    hist = LatencyHistogram()
    hist.record(ROUTE, "", 0.1)
    assert hist.get(ROUTE, MISSING, WALL_STAGE) is not None


def test_since_advances():
    hist = LatencyHistogram()
    first = hist.since_seconds()
    for _ in range(2000):
        hist.record(ROUTE, "c", 0.1)
    assert hist.since_seconds() > first


# --------------------------------------------------------------------------- #
# The route allowlist — the cardinality guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/ingest/9f3a2b1c-4d5e-6f70-8901-234567890abc"),
        ("GET", "/v1/collections/lib_open_access"),
        ("DELETE", "/v1/documents/doc-42"),
        ("GET", "/health"),
        ("GET", "/v1/query"),  # right path, wrong method
        ("POST", "/v1/queryx"),
        ("POST", "/v1/ingest"),
    ],
)
def test_a_route_outside_the_allowlist_produces_no_series_key(method, path):
    """The cardinality guard, stated as the property it protects.

    An unfiltered histogram keyed on the raw path would mint one permanent
    series per ingest job id — the 512-series budget would be gone in an
    afternoon and the query rows, the only ones anybody wants, would be the ones
    evicted. ``/v1/queryx`` is here because a substring match would admit it.
    """
    assert route_key(method, path) is None


@pytest.mark.parametrize(("method", "suffix"), ALLOWED_ROUTES)
@pytest.mark.parametrize(
    "prefix",
    [
        "",  # the gateway strips its prefix today
        "/ragstack/asm/api",  # ...but ROOT_PATH's mounted-not-stripped mode does not
    ],
)
def test_an_allowlisted_route_maps_to_ONE_canonical_label_under_any_prefix(
    method, suffix, prefix
):
    """Both deployment arrangements must produce the SAME label.

    If they did not, a proxied deployment would either record nothing (exact
    match) or record a per-prefix series (raw label) — and the first of those is
    the one that matters, because it makes the histogram silently empty on
    precisely the production topology the incident happened on.
    """
    assert route_key(method, prefix + suffix) == f"{method} {suffix}"
    assert route_key(method, prefix + suffix + "/") == f"{method} {suffix}"


def test_the_allowlist_contains_only_parameter_free_paths():
    """A control on the allowlist itself: adding ``/v1/ingest/{job_id}`` to it
    would defeat every guarantee above, and this is the assertion that makes
    that a test failure rather than a code review someone skipped."""
    for _method, path in ALLOWED_ROUTES:
        assert "{" not in path and "}" not in path
        assert path.startswith("/v1/")
