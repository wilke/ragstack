"""What ``stage()`` costs, per call, on the query path.

**What this budget is evidence of:** that instrumenting ~12 points on a request
costs microseconds against a request that spends its time in Qdrant, a vLLM
fleet and an LLM. The measured work per stage is one ``ContextVar.get``, two
``perf_counter`` calls and one dict update. The incident this came from was a
30-second vector search; if the instrumentation were anywhere near a millisecond
it would be a regression in the thing it exists to measure.

**What it is NOT evidence of.** It does not measure the query path end to end
(``test_request_middleware_overhead.py`` covers the middleware, and the pipeline
has no in-process budget), and it does not measure contention — everything here
runs on one loop with no concurrency.

The three arms are the three states a call site is actually in:

* **unset** — no request context. Every CLI script, the ingest pipeline and most
  of the unit suite. This is the one that must be free, because it is paid by
  code that gets nothing back for it.
* **recording** — inside a request. The production path.
* **bare** — the same block with no ``with`` at all, as the baseline the other
  two are differences against.

Budgets are absolute per-call bounds sized for a loaded box with no CI, not for
the expectation: the measured costs are ~0.3 µs (unset) and ~0.6 µs (recording),
budgeted at 20 µs and 50 µs. A regression that matters — someone reaching for an
``@asynccontextmanager``, or adding a lock — is 3-10x, and lands well inside a
budget this loose only if it is genuinely small.
"""
import pytest

from ragstack.observability.context import RequestContext, clear_context, set_context
from ragstack.observability.stages import StageTimings, stage
from tests.perf._budget import assert_budget

#: Calls per sample. One ``stage()`` is far below timer resolution, so each
#: sample is a batch and the budget is per batch.
BATCH = 1000

#: Per-call budgets, in seconds, expressed as a batch bound below.
UNSET_BUDGET_PER_CALL_S = 0.00002
RECORDING_BUDGET_PER_CALL_S = 0.00005

N = 30


@pytest.fixture(autouse=True)
def _clean_context():
    """Each arm installs (or refuses to install) its own context. A leaked one
    from another test would silently move an arm into the other arm's regime and
    the budget would be measuring the wrong thing."""
    clear_context()
    yield
    clear_context()


def _batch() -> None:
    for _ in range(BATCH):
        with stage("vector", "a-collection"):
            pass


@pytest.mark.perf
def test_stage_is_free_when_no_request_is_in_flight():
    """The no-op path. Paid by every ingest run and every CLI invocation, which
    get nothing in return, so it must round to nothing."""
    assert_budget(
        "stage_timer_unset",
        _batch,
        budget_s=UNSET_BUDGET_PER_CALL_S * BATCH,
        n=N,
    )


@pytest.mark.perf
def test_stage_recording_is_cheap_enough_to_ignore():
    """The production path: contextvar read, two ``perf_counter`` calls, one dict
    update. About a dozen of these run per query, against a request whose floor
    is a network round trip to a GPU."""
    set_context(RequestContext(request_id="r" * 16, stages=StageTimings()))
    assert_budget(
        "stage_timer_recording",
        _batch,
        budget_s=RECORDING_BUDGET_PER_CALL_S * BATCH,
        n=N,
    )


@pytest.mark.perf
def test_the_recorded_arm_is_within_a_few_microseconds_of_bare():
    """A *comparison*, so the two absolute budgets above cannot both pass by the
    machine being uniformly slow. The delta is the thing under test.

    Printed as a PERF line whatever it is, because the number is the point: if
    somebody later replaces this with an async context manager, this is where
    the 3x shows up.
    """
    import time

    def _bare() -> None:
        for _ in range(BATCH):
            pass

    def _p50(fn) -> float:
        samples = sorted(_timed(fn) for _ in range(N))
        return samples[len(samples) // 2]

    def _timed(fn) -> float:
        start = time.perf_counter()
        fn()
        return time.perf_counter() - start

    bare = _p50(_bare)
    set_context(RequestContext(request_id="r" * 16, stages=StageTimings()))
    recording = _p50(_batch)

    added_per_call = (recording - bare) / BATCH
    print(
        f"PERF stage_timer_added: per_call={added_per_call * 1e6:.3f}us "
        f"budget={RECORDING_BUDGET_PER_CALL_S * 1e6:.1f}us batch={BATCH} n={N}"
    )
    assert added_per_call <= RECORDING_BUDGET_PER_CALL_S, (
        f"stage() added {added_per_call * 1e6:.3f}us per call, over the "
        f"{RECORDING_BUDGET_PER_CALL_S * 1e6:.1f}us budget"
    )


@pytest.mark.perf
def test_a_request_worth_of_stages_is_lost_in_the_noise():
    """The number an operator would actually ask for: what the whole of #427 W3
    adds to one request. Twelve stages, plus the accumulator's rendering, which
    the middleware does once per request."""

    def _one_request() -> None:
        acc = StageTimings()
        set_context(RequestContext(request_id="r" * 16, stages=acc))
        for name in (
            "authz", "rewrite", "embed", "vector", "text", "graph",
            "fuse", "rerank", "expand", "generate", "fuse", "vector",
        ):
            with stage(name, "a-collection"):
                pass
        acc.fields()
        acc.self_seconds(0.5)

    # 200 µs for twelve timers and one render is ~100x the expectation; the
    # point is to catch an order-of-magnitude regression, not to police noise.
    assert_budget("stage_timer_full_request", _one_request, budget_s=0.0002, n=60)
