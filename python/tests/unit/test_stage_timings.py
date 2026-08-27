"""``observability.stages`` — the accumulator and the ``stage`` context manager.

These pin the five mechanics the module docstring names, because each of them
fails *silently*: a leaked accumulator, a ``set()`` that does not propagate out
of a ``gather`` child, or a timer that skips its record when the body raises all
produce a plausible-looking log line with wrong numbers in it. Wrong numbers on
an observability line are worse than no line, because they are believed.
"""
import asyncio

import pytest

from ragstack.observability.context import RequestContext, clear_context, set_context
from ragstack.observability.stages import (
    EXTERNAL_STAGES,
    MAX_SERIES,
    STAGE_NAMES,
    StageTimings,
    current_stages,
    note,
    note_query_sha,
    query_sha,
    stage,
)


@pytest.fixture(autouse=True)
def _no_ambient_context():
    """A context installed by one test must not survive into the next.

    Not hygiene theatre: the assertions here are about the *presence or absence*
    of a context, so a leaked one flips a result depending on test order. This is
    exactly what ``context.clear_context`` was written for.
    """
    clear_context()
    yield
    clear_context()


def _request() -> StageTimings:
    """Install a fresh request context, as the middleware does, and return its
    accumulator."""
    acc = StageTimings()
    set_context(RequestContext(request_id="r" * 16, stages=acc))
    return acc


# --------------------------------------------------------------------------- #
# Mechanic 4 — no context, no cost, no crash
# --------------------------------------------------------------------------- #


def test_stage_is_a_no_op_outside_a_request():
    """The ingest pipeline, ``python/scripts/*.py`` and most unit tests call the
    instrumented functions with no request in flight. None of them should have to
    know this module exists, and none of them should crash because of it."""
    assert current_stages() is None

    timer = stage("vector", "some-collection")
    with timer:
        pass
    note("embed_ep", "http://gpu-1:8000/health")
    note_query_sha("anything at all")

    # "Nothing raised" is NOT the assertion. A version that quietly built a
    # throwaway accumulator and timed into it would satisfy that, and would
    # satisfy it while doing exactly the work this path exists to avoid — a test
    # passing because of an absent crash rather than the behaviour it names. So
    # assert the mechanism: no accumulator was resolved, so no clock was read.
    assert timer._acc is None, "stage() did work with no request in flight"
    assert current_stages() is None


def test_note_query_sha_outside_a_request_does_not_raise():
    note_query_sha("no context here")


# --------------------------------------------------------------------------- #
# Mechanic 3 — a fresh accumulator per request (the leak regression)
# --------------------------------------------------------------------------- #


def test_two_sequential_requests_get_disjoint_accumulators():
    """The leak this guards against is not hypothetical: a module-scope or reused
    accumulator makes request N+1's line carry N's timings, and every number
    after the first request is quietly wrong for the life of the process."""
    first = _request()
    with stage("vector"):
        pass
    assert first.totals()["vector"][1] == 1

    second = _request()
    assert second is not first
    assert second.totals() == {}, "request N's timings leaked into request N+1"

    with stage("vector"):
        pass
    assert second.totals()["vector"][1] == 1
    assert first.totals()["vector"][1] == 1, "request N+1 wrote into request N"


# --------------------------------------------------------------------------- #
# Mechanic 2 — gather children share the object; a set() would not propagate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gather_children_all_record_into_the_same_accumulator():
    """``asyncio.gather`` copies the *context*, so every child sees the same
    ``RequestContext`` and the same accumulator, and their timings are visible to
    the middleware that renders the line afterwards.

    This is the mechanic that makes per-leg attribution possible at all: the
    multi-collection retriever runs its legs exactly this way, and if it did not
    hold, a slow leg would be invisible on the summary line.
    """
    acc = _request()

    async def _leg(name: str) -> None:
        with stage("vector", name):
            await asyncio.sleep(0)

    await asyncio.gather(_leg("coll-a"), _leg("coll-b"), _leg("coll-c"))

    seconds, count = acc.totals()["vector"]
    assert count == 3, "a gather child's timings did not reach the parent"
    assert seconds >= 0.0
    assert acc.fields()["vector_ms"].endswith("/3")


@pytest.mark.asyncio
async def test_a_set_in_a_gather_child_would_not_propagate():
    """The negative half of mechanic 2, pinned so the "mutate, never ``set()``"
    rule has a reason attached to it rather than being folklore.

    A child that installs its OWN context is invisible to the parent — so a
    ``stage()`` implementation that re-``set()`` the contextvar would drop every
    timing recorded under a ``gather``, which is most of the query path.
    """
    parent = _request()

    async def _child_that_sets() -> None:
        set_context(RequestContext(request_id="c" * 16, stages=StageTimings()))
        with stage("vector"):
            await asyncio.sleep(0)

    await asyncio.gather(_child_that_sets())
    assert parent.totals() == {}, (
        "a child's ContextVar.set() became visible to the parent — the whole "
        "mutate-never-set rule rests on it not being"
    )


# --------------------------------------------------------------------------- #
# The timer records on failure. This is the incident.
# --------------------------------------------------------------------------- #


def test_a_stage_whose_body_raises_still_records_and_re_raises():
    """#427's incident is a vector search that spent its entire 30-second bound
    and then raised. A timer that only recorded on success would have had
    *nothing* to say about the one request anybody cared about — and a context
    manager that swallowed the exception would have turned a 503 into a wrong
    answer.
    """
    acc = _request()

    with pytest.raises(ZeroDivisionError):
        with stage("vector", "big-collection"):
            raise ZeroDivisionError("the store timed out")

    assert "vector" in acc.totals(), "a raising stage recorded nothing"
    assert acc.fields()["by_coll"].startswith("vector@big-collection=")


# --------------------------------------------------------------------------- #
# Mechanic 5 — (sum, count), and the rendering that goes with it
# --------------------------------------------------------------------------- #


def test_totals_are_sum_and_count_and_render_as_such():
    acc = StageTimings()
    acc.add("vector", 9.0, "coll-a")
    acc.add("vector", 9.0, "coll-b")
    acc.add("embed", 0.041)

    assert acc.totals() == {"vector": (18.0, 2), "embed": (0.041, 1)}
    fields = acc.fields()
    # The /count is never elided, even at 1: two shapes on one line is how
    # somebody eventually reads a five-leg sum as a single call.
    assert fields["vector_ms"] == "18000.0/2"
    assert fields["embed_ms"] == "41.0/1"


def test_the_per_collection_breakdown_appears_only_when_something_is_tagged():
    untagged = StageTimings()
    untagged.add("embed", 0.01)
    assert "by_coll" not in untagged.fields()

    tagged = StageTimings()
    tagged.add("vector", 1.5, "coll-b")
    tagged.add("vector", 0.5, "coll-a")
    assert tagged.fields()["by_coll"] == "vector@coll-a=500.0/1 vector@coll-b=1500.0/1"


def test_an_unscoped_retriever_tag_renders_as_the_missing_marker():
    """``HybridRetriever.collection`` is ``None`` for unscoped dev/test
    retrievers, which is the normal case in this suite. It must aggregate, not
    produce a ``vector@None=`` entry."""
    acc = StageTimings()
    acc.add("vector", 0.1, None)
    acc.add("vector", 0.1, "")
    assert acc.totals()["vector"] == (0.2, 2)
    assert "by_coll" not in acc.fields()


# --------------------------------------------------------------------------- #
# self_ms is an upper bound, deliberately
# --------------------------------------------------------------------------- #


def test_self_ms_subtracts_the_mean_not_the_sum():
    """Five concurrent legs of 9 s occupy 9 s of wall time, not 45. Subtracting
    the sum would drive the residual to zero (or negative) on every
    multi-collection request and make ADR-0006's Go trigger unmeasurable."""
    acc = StageTimings()
    for name in ("a", "b", "c", "d", "e"):
        acc.add("vector", 9.0, name)

    assert acc.external_seconds() == pytest.approx(9.0)
    assert acc.self_seconds(10.0) == pytest.approx(1.0)


def test_self_ms_is_clamped_at_zero():
    acc = StageTimings()
    acc.add("generate", 5.0)
    assert acc.self_seconds(1.0) == 0.0


def test_an_unrecognised_stage_is_not_subtracted():
    """The conservative default, and the direction matters. ADR-0006's Go trigger
    fires when the Python-layer residual is too high, so an unclassified stage
    must inflate ``self_ms`` (visible, argued about) rather than deflate it
    (silently hides the thing the trigger exists to catch)."""
    acc = StageTimings()
    acc.add("something_new", 4.0)
    assert acc.external_seconds() == 0.0
    assert acc.self_seconds(5.0) == pytest.approx(5.0)


def test_fuse_is_in_process_and_stays_out_of_the_subtraction():
    assert "fuse" not in EXTERNAL_STAGES
    assert "fuse" in STAGE_NAMES
    acc = StageTimings()
    acc.add("fuse", 0.5)
    assert acc.external_seconds() == 0.0


# --------------------------------------------------------------------------- #
# Notes — the embed-endpoint attribution W1's dampening made W3's job
# --------------------------------------------------------------------------- #


def test_notes_accumulate_distinct_values_in_first_seen_order():
    """One ``embed()`` larger than the request batch fans out across the fleet,
    so a request legitimately touches several endpoints. "Was it always the same
    slow one?" needs all of them, deduplicated."""
    acc = StageTimings()
    acc.note("embed_ep", "http://gpu-2:8000/health")
    acc.note("embed_ep", "http://gpu-1:8000/health")
    acc.note("embed_ep", "http://gpu-2:8000/health")
    assert acc.fields()["embed_ep"] == "http://gpu-2:8000/health,http://gpu-1:8000/health"


def test_note_reaches_the_request_in_flight():
    acc = _request()
    note("embed_ep", "http://gpu-3:8000/health")
    assert acc.fields()["embed_ep"] == "http://gpu-3:8000/health"


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #


def test_series_are_capped_and_the_drop_is_reported():
    """An unbounded dict fed from request data is how a log line becomes a memory
    leak. The cap can only be hit by a bug — but a silent cap would hide that
    bug, so the overflow is counted and printed."""
    acc = StageTimings()
    for i in range(MAX_SERIES + 5):
        acc.add("vector", 0.001, f"coll-{i}")

    assert acc.totals()["vector"][1] == MAX_SERIES
    assert acc.overflow == 5
    assert acc.fields()["stage_overflow"] == "5"


def test_an_existing_series_still_accumulates_after_the_cap_is_reached():
    """The cap must bound *new* series, not stop recording. Otherwise a request
    that tripped it would report a stopped clock for the stage that mattered."""
    acc = StageTimings()
    for i in range(MAX_SERIES):
        acc.add("vector", 0.001, f"coll-{i}")
    acc.add("vector", 1.0, "coll-0")
    assert acc.totals()["vector"][1] == MAX_SERIES + 1


# --------------------------------------------------------------------------- #
# The query fingerprint — #114 says the text never gets logged
# --------------------------------------------------------------------------- #


def test_query_sha_is_short_stable_and_not_the_query():
    text = "what are the binding affinities of ACE2 variants"
    digest = query_sha(text)
    assert len(digest) == 8
    assert query_sha(text) == digest
    assert query_sha(text + "?") != digest
    assert text not in digest
    # Every character is hex, so nothing user-supplied can survive into the line
    # and forge a field separator.
    assert set(digest) <= set("0123456789abcdef")


def test_note_query_sha_stamps_the_context_and_not_the_text():
    _request()
    from ragstack.observability.context import current_context

    note_query_sha("a secret-ish question")
    ctx = current_context()
    assert ctx is not None
    assert ctx.qsha == query_sha("a secret-ish question")


def test_a_query_that_is_not_utf8_encodable_still_hashes():
    """A lone surrogate reaches here from a client that sent one. ``errors=
    "replace"`` means the fingerprint degrades rather than the request 500ing on
    the logging path — an observability feature must never be the thing that
    takes a request down."""
    assert len(query_sha("bad \ud800 surrogate")) == 8
