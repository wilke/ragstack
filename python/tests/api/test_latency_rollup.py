"""The rollup line and what feeds it (#427 W4).

The acceptance bullet, verbatim: *p95 query latency per collection is answerable
**without adding instrumentation first**.* So the assertion this file exists for
is ``test_a_rollup_line_names_the_collection_and_how_many_requests`` — grep the
log, read p95, done.

The rest guard the two ways this feature fails silently rather than loudly:

* it records a route it should not (the middleware sees the raw path, so an
  unguarded histogram spends its series budget on ``<job_id>``-shaped paths and
  the query rows get evicted), and
* the background task misbehaves at shutdown, which is a class of bug that only
  ever shows up in production because nothing else in the suite runs a lifespan.

Nothing here sleeps for a meaningful duration: the periodic task is driven with
an injected ``sleep``, so the loop's iterations are deterministic and the test
finishes in microseconds.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import pytest

from ragstack.observability import histogram as hist_mod
from ragstack.observability.context import MISSING, RequestContextFilter
from ragstack.observability.histogram import (
    WALL_STAGE,
    LatencyHistogram,
    RollupTask,
    emit_rollup,
    latency_histogram,
    rollup_running,
    start_rollup,
    stop_rollup,
)
from ragstack.observability.middleware import RequestContextMiddleware

pytestmark = pytest.mark.asyncio

ROLLUP_LOGGER = "ragstack.observability.histogram"
QUERY_ROUTE = "POST /v1/query"


@pytest.fixture(autouse=True)
def _fresh_latency_histogram():
    """The histogram is process-global and cumulative since start, so without
    this every test here would read whatever the rest of the suite recorded."""
    hist_mod.reset_for_tests()
    yield
    hist_mod.reset_for_tests()


async def _stopped(coro) -> None:
    """``await`` a shutdown with a deadline.

    Every teardown in this file goes through here rather than awaiting
    ``stop()`` directly, because a ``stop()`` that forgot to cancel would
    otherwise await a ``while True`` loop that never ends — turning a broken
    shutdown into a **hung test suite** instead of a failing test. (Observed:
    removing ``task.cancel()`` hung a mutation run indefinitely until this was
    added.) Five seconds is far above the microseconds a cancellation takes.

    It is a **safety net, not an assertion**: on timeout ``wait_for`` cancels
    what it is waiting on, which cancels the loop task too — so it repairs the
    exact defect it would otherwise expose. Nothing here may be read as evidence
    that ``stop()`` cancels anything; that is
    ``test_shutdown_with_a_rollup_pending_neither_hangs_nor_raises``'s job, and
    its docstring explains why it is written the way it is.
    """
    await asyncio.wait_for(coro, timeout=5.0)


def _rollups(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == ROLLUP_LOGGER and r.getMessage() == "latency rollup"
    ]


# --------------------------------------------------------------------------- #
# The acceptance criterion
# --------------------------------------------------------------------------- #


async def test_a_rollup_line_names_the_collection_and_how_many_requests(client, caplog):
    """**The assertion this PR exists for.**

    Three real queries through the real app, then a rollup: one INFO line that
    names the route, names the collection, says how many requests it covers, and
    carries a p95 — which is #427's "answerable without adding instrumentation
    first", in a single greppable row.

    The percentile fields are named ``*_ms_le`` and not ``p95_ms``, and that is
    asserted rather than assumed: the value is the upper bound of the bucket the
    95th percentile fell in, and a field name that did not say so would be
    printing precision this instrument does not have.
    """
    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(RequestContextFilter())

    for _ in range(3):
        r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
        assert r.status_code == 200, r.text

    assert emit_rollup() == 1
    lines = _rollups(caplog)
    assert len(lines) == 1
    line = lines[0]

    assert line.levelno == logging.INFO
    assert line.for_route == QUERY_ROUTE
    assert line.coll and line.coll != MISSING, "the line must name the collection"
    assert line.n == 3
    assert line.errors == 0
    assert float(line.p50_ms_le) > 0.0
    assert float(line.p95_ms_le) > 0.0
    assert float(line.max_ms) > 0.0
    assert int(line.pid) > 0
    # A per-stage p95 too, or "which leg is creeping" is unanswerable and the
    # line reduces to what a load balancer already knows.
    assert float(line.vector_p95_ms_le) >= 0.0
    assert float(line.embed_p95_ms_le) >= 0.0


async def test_since_advances_between_two_rollups_so_they_can_be_diffed(client, caplog):
    """The counters are cumulative since process start, so a single line says
    nothing about a trend. ``since_s`` is what makes two of them subtractable:
    ``(n2 - n1)`` requests over ``(since2 - since1)`` seconds.

    Also pins that the second line's ``n`` includes the first line's requests —
    a windowed implementation that reset the counters would fail here, and would
    be a different (and undocumented) instrument.
    """
    caplog.set_level(logging.INFO)
    assert (await client.post("/v1/query", json={"query": "a", "top_k": 1})).status_code == 200
    emit_rollup()
    first = _rollups(caplog)[-1]

    assert (await client.post("/v1/query", json={"query": "b", "top_k": 1})).status_code == 200
    # Busy-work rather than a sleep: `since` is monotonic elapsed time and the
    # two emissions must be separable without the test costing wall-clock time.
    for _ in range(20000):
        pass
    emit_rollup()
    second = _rollups(caplog)[-1]

    assert float(second.since_s) > float(first.since_s)
    assert second.n == 2 and first.n == 1, "the counters are cumulative, not windowed"


async def test_a_failing_query_is_counted_as_an_error_on_the_same_row(client, caplog):
    """The incident's own shape: a 503 out of the vector store. The rollup must
    carry it as ``errors``, because "p95 is fine but a tenth of them fail" and
    "p95 is fine" are different situations and the line has to tell them apart.
    """
    from ragstack.api.main import app
    from ragstack.retrieval.retriever import HybridRetriever
    from ragstack.stores.errors import KIND_TIMEOUT, StoreUnavailable

    class _Exploding:
        async def search(self, *_a, **_k):
            raise StoreUnavailable("qdrant", "timed out", kind=KIND_TIMEOUT, elapsed_s=30.0)

    caplog.set_level(logging.INFO)
    app.state.retriever = HybridRetriever(
        _Exploding(), app.state.text_index, app.state.embedder
    )
    r = await client.post("/v1/query", json={"query": "why so slow", "top_k": 1})
    assert r.status_code == 503, r.text

    emit_rollup()
    line = _rollups(caplog)[-1]
    assert line.n == 1
    assert line.errors == 1


async def test_the_route_on_the_line_survives_being_emitted_inside_a_request(caplog):
    """The rollup's route field must be the route the ROW describes, not the
    route of whatever request happened to trigger the emission.

    ``route`` is one of ``context.CONTEXT_FIELDS``, and ``RequestContextFilter``
    **overwrites** those on every record while a request context is current — so
    a rollup logged under ``extra={"route": …}`` from inside a request would come
    out relabelled with the *caller's* route while still reading like the truth.
    Nothing does that today (the emitter is a background task), but W5's deferred
    ``GET /v1/admin/stats/latency`` would, and this is what makes that a test
    failure rather than a mystery. Hence ``for_route``.
    """
    from ragstack.observability.context import (
        RequestContext,
        clear_context,
        set_context,
    )

    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(RequestContextFilter())
    hist = LatencyHistogram()
    hist.record(QUERY_ROUTE, "lib_open_access", 0.4)

    set_context(RequestContext(request_id="deadbeefdeadbeef", route="GET /v1/admin/stats"))
    try:
        emit_rollup(hist)
    finally:
        clear_context()

    line = _rollups(caplog)[-1]
    assert line.for_route == QUERY_ROUTE
    assert line.coll == "lib_open_access"
    assert line.route == "GET /v1/admin/stats", (
        "the context filter still owns `route`; that is precisely why the "
        "rollup's own route lives under a different key"
    )


async def test_nothing_is_logged_when_no_query_has_run(caplog):
    """A process serving no queries must not write a line every five minutes
    saying so."""
    caplog.set_level(logging.INFO)
    assert emit_rollup() == 0
    assert _rollups(caplog) == []


# --------------------------------------------------------------------------- #
# The allowlist, through the real middleware
# --------------------------------------------------------------------------- #


async def test_a_parameterised_route_creates_no_series(client):
    """**The cardinality guard.**

    ``GET /v1/ingest/<job_id>`` is the shape that would eat the 512-series
    budget one job at a time, because this middleware runs above routing and
    sees the raw path rather than the template. The request's status does not
    matter — a 404 travels through the same ``finally`` a 200 does, which is
    exactly why the guard has to be in the histogram and not in a handler.

    ``/health`` is here as the second half of the same property: the summary
    *line* covers every route (``test_query_summary_line`` pins that), and only
    the histogram is restricted.
    """
    await client.get(f"/v1/ingest/{uuid.uuid4()}")
    await client.get("/health")
    assert latency_histogram().size == 0, (
        f"a non-allowlisted route created series: {latency_histogram().groups()}"
    )

    r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text
    routes = {route for route, _coll, _stages in latency_histogram().groups()}
    assert routes == {QUERY_ROUTE}, "only the allowlisted route may be recorded"


async def test_the_retrieve_route_is_recorded_too(client):
    r = await client.post("/v1/retrieve", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text
    assert {route for route, _c, _s in latency_histogram().groups()} == {"POST /v1/retrieve"}


async def test_a_query_records_wall_and_the_stages_it_actually_ran(client):
    r = await client.post("/v1/query", json={"query": "hello", "top_k": 1})
    assert r.status_code == 200, r.text

    (route, coll, stages), = latency_histogram().groups()
    assert route == QUERY_ROUTE
    assert coll and coll != MISSING
    assert WALL_STAGE in stages
    assert stages[WALL_STAGE].count == 1
    # The legs the in-memory fixture actually runs, and nothing invented for the
    # ones it does not (no reranker, no LLM).
    assert {"embed", "vector", "text"} <= set(stages)
    assert "generate" not in stages


async def test_a_client_that_went_away_is_not_recorded_at_all(caplog):
    """A disconnect's wall time measures how long the CLIENT stayed, not how
    long the server took, so it is not an observation of server latency — and
    W3's middleware docstring commits in writing that a closed tab "must never
    page anyone or enter W4's error rate". Both halves are honoured by not
    recording the request.

    Driven at the ASGI layer because httpx cannot produce a mid-request client
    disconnect against ``ASGITransport``.
    """
    caplog.set_level(logging.INFO)

    async def _app(scope, receive, send):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await RequestContextMiddleware(_app)(
            {"type": "http", "method": "POST", "path": "/v1/query", "headers": []},
            None,
            None,
        )

    assert latency_histogram().size == 0, "a disconnect entered the latency distribution"
    assert emit_rollup() == 0


async def test_an_unhandled_exception_IS_recorded_as_an_error():
    """The contrast to the test above, and the reason that one is not simply
    "exceptions are skipped": a server fault must land in both ``n`` and
    ``errors``. The incident this issue exists for was a 503, and a histogram
    that dropped failures would report a healthy p95 for a broken service.
    """

    async def _app(scope, receive, send):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await RequestContextMiddleware(_app)(
            {"type": "http", "method": "POST", "path": "/v1/query", "headers": []},
            None,
            None,
        )

    wall = latency_histogram().get(QUERY_ROUTE, MISSING, WALL_STAGE)
    assert wall is not None
    assert wall.count == 1
    assert wall.errors == 1


# --------------------------------------------------------------------------- #
# The periodic task
# --------------------------------------------------------------------------- #


def _driven_task(interval: float = 300.0, *, iterations: int = 3, emit=None):
    """A ``RollupTask`` whose sleeps return immediately for ``iterations`` turns
    and then park forever — so the loop runs a known number of times, in
    microseconds, and the test never waits on wall time."""
    delays: list[float] = []
    forever = asyncio.Event()

    async def _sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) > iterations:
            await forever.wait()

    return RollupTask(interval, sleep=_sleep, emit=emit or (lambda: 0)), delays


async def test_the_task_emits_once_per_interval():
    done = asyncio.Event()
    emitted: list[int] = []

    def _emit() -> int:
        emitted.append(1)
        if len(emitted) == 3:
            done.set()
        return 1

    task, delays = _driven_task(300.0, iterations=3, emit=_emit)
    assert task.start() is True
    try:
        await asyncio.wait_for(done.wait(), timeout=5.0)
    finally:
        await _stopped(task.stop())

    assert len(emitted) == 3
    assert delays[:3] == [300.0, 300.0, 300.0], (
        "the configured interval must be what is waited on"
    )


async def test_a_rollup_that_raises_does_not_kill_the_loop(caplog):
    """An observability feature that can stop itself permanently on one bad
    iteration is worse than one that logs a warning: the failure is invisible
    until somebody goes looking for a line that stopped arriving weeks ago."""
    caplog.set_level(logging.WARNING)
    done = asyncio.Event()
    calls: list[int] = []

    def _emit() -> int:
        calls.append(1)
        if len(calls) == 3:
            done.set()
        raise ValueError("nope")

    task, _delays = _driven_task(1.0, iterations=3, emit=_emit)
    task.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=5.0)
    finally:
        await _stopped(task.stop())

    assert len(calls) == 3, "the loop stopped after the first failure"
    assert any("latency rollup failed" in r.getMessage() for r in caplog.records)


async def test_zero_starts_no_task():
    """``LATENCY_ROLLUP_SECONDS=0`` must mean *no task*, not a task that sleeps
    for zero seconds — which would be a busy loop pinning a core and writing a
    line as fast as the disk allows."""
    before = {t for t in asyncio.all_tasks() if not t.done()}
    task = RollupTask(0.0)
    assert task.start() is False
    assert task.running is False
    assert {t for t in asyncio.all_tasks() if not t.done()} == before, (
        "a task was created for a disabled rollup"
    )


async def test_zero_starts_no_task_through_the_settings_path(monkeypatch):
    """The same, through the entry point ``lifespan`` actually calls — because
    ``RollupTask(0).start()`` returning ``False`` proves nothing about whether
    the setting reaches it."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "latency_rollup_seconds", 0.0)
    try:
        assert start_rollup() is False
        assert rollup_running() is False
    finally:
        await _stopped(stop_rollup())


async def test_a_positive_setting_does_start_one(monkeypatch):
    """The control for the test above: if ``start_rollup`` returned ``False``
    unconditionally the zero test would pass for the wrong reason."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "latency_rollup_seconds", 300.0)
    try:
        assert start_rollup() is True
        assert rollup_running() is True
    finally:
        await _stopped(stop_rollup())
    assert rollup_running() is False


async def test_shutdown_with_a_rollup_pending_neither_hangs_nor_raises():
    """The shutdown property, and it is a real one: a 300-second sleep is
    genuinely pending when the process is asked to stop.

    **This test is written the awkward way on purpose, and the obvious version
    of it does not work.** The obvious version is
    ``await asyncio.wait_for(stop_rollup(), timeout=2)`` plus
    ``assert inner.cancelled()``. That version passes even with ``task.cancel()``
    DELETED from ``stop()`` — verified by mutation. The reason: when ``wait_for``
    times out it cancels the coroutine it is waiting on, that cancellation
    propagates into the task that coroutine is awaiting, and so ``wait_for``
    performs the very cancellation the implementation forgot. ``stop()`` then
    swallows the ``CancelledError`` it was written to swallow, returns normally,
    and every assertion downstream — including ``inner.cancelled()`` — is
    satisfied by the test harness rather than by the code under test.

    So the assertion here is instead **that ``stop()`` returns within a handful
    of event-loop turns with nobody cancelling anything from outside**. A
    correct ``stop()`` needs about three: cancel, let the loop task raise, let
    the await resume. One that only awaits needs 300 seconds, and ``done()`` is
    ``False`` when we look.
    """
    assert start_rollup(300.0) is True
    task = hist_mod._rollup
    assert task is not None and task.running

    inner = task._task
    stopper = asyncio.create_task(stop_rollup())
    try:
        for _ in range(20):
            if stopper.done():
                break
            await asyncio.sleep(0)
        assert stopper.done(), (
            "stop() had not returned after 20 event-loop turns: it is awaiting a "
            "loop it never cancelled, and shutdown would hang for a full interval"
        )
        assert stopper.exception() is None, "shutdown raised"
    finally:
        # Only reached when the assertion above already failed; without it the
        # orphaned tasks would leak into the next test in this loop.
        if not stopper.done():
            stopper.cancel()
        if inner is not None and not inner.done():
            inner.cancel()

    assert rollup_running() is False
    assert inner is not None and inner.done()
    assert inner.cancelled(), "the pending task was dropped rather than cancelled"


async def test_stopping_a_rollup_that_never_started_is_a_no_op():
    """Lifespan's ``finally`` runs even when startup failed before the task was
    created, so this path is reached in exactly the situation where an
    exception would be least welcome."""
    await _stopped(stop_rollup())
    await _stopped(stop_rollup())
    assert rollup_running() is False


async def test_starting_twice_does_not_leave_an_orphan():
    try:
        assert start_rollup(300.0) is True
        first = hist_mod._rollup
        assert start_rollup(300.0) is True
        assert hist_mod._rollup is first, "a second start replaced the running task"
    finally:
        await _stopped(stop_rollup())


# --------------------------------------------------------------------------- #
# The lifespan wiring — one behavioural test, one fast structural guard
# --------------------------------------------------------------------------- #

#: Run inside a subprocess by the test below. Enters the app's REAL lifespan and
#: reports whether the rollup task was armed inside it and disarmed after.
_LIFESPAN_PROBE = '''
import asyncio
from ragstack.api.main import app
from ragstack.observability.histogram import rollup_running

async def main():
    async with app.router.lifespan_context(app):
        print("INSIDE", rollup_running(), flush=True)
    print("AFTER", rollup_running(), flush=True)

asyncio.run(main())
'''

#: Every outbound endpoint the lifespan touches, pinned to a dead port.
#:
#: Not a formality. A scratch run of this probe with only the STORES pinned
#: silently reached the live cross-encoder sidecar on ``:50052`` — the reranker
#: model-verification probe at the end of ``lifespan`` is a real HTTP call and
#: this host runs a real sidecar there. Reaching a live service from a test is
#: the failure mode CLAUDE.md names outright, so the model backends are pinned
#: too, not just the stores.
_DEAD = "http://127.0.0.1:1"
_PINNED_ENV = {
    "QDRANT_URL": _DEAD,
    "ELASTICSEARCH_URL": _DEAD,
    "EMBEDDING_SIDECAR_URL": _DEAD,
    "CROSSENCODER_SIDECAR_URL": _DEAD,
    "FAISS_SIDECAR_URL": _DEAD,
    "GOWE_URL": _DEAD,
    "WORKSPACE_URL": _DEAD,
    "NEO4J_URI": "bolt://127.0.0.1:1",
    "REDIS_URL": "redis://127.0.0.1:1",
}


async def test_the_real_lifespan_arms_the_rollup_and_disarms_it(tmp_path):
    """**The behavioural test of the production wiring.**

    ``deps.lifespan`` holds the only two lines that arm and disarm this feature
    on a real deployment, and ``ASGITransport`` does not run a lifespan — so
    without this, deleting them would leave the whole suite green while the
    rollup never ran anywhere. That is the "unarmed timer" shape this series has
    already been bitten by once.

    **A subprocess, and both reasons are load-bearing.** In-process, the real
    lifespan rebuilds ``app.state``'s singletons on the shared module-level app
    and would clobber the conftest's in-memory doubles for every test after this
    one. And it is the only way to guarantee the environment: a fresh process
    with every backend pinned to a dead port, ``cwd`` in ``tmp_path`` so no
    checkout ``.env`` is read (``Settings.model_config`` sets ``env_file=".env"``
    relative to the working directory), and a private ``HOME``.

    Every startup probe in ``lifespan`` is best-effort — it warns and continues —
    so with nothing reachable the whole thing completes in about a tenth of a
    second. It was **not**, as an earlier version of this file claimed, dependent
    on live infrastructure. That claim was wrong, and this test is what disproves
    it.
    """
    import os
    import subprocess
    import sys

    import ragstack

    probe = tmp_path / "probe_lifespan.py"
    probe.write_text(_LIFESPAN_PROBE)
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(ragstack.__file__)))

    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "PYTHONPATH": package_root,
            **_PINNED_ENV,
        },
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, f"the lifespan raised:\n{result.stdout}\n{result.stderr}"
    assert "INSIDE True" in result.stdout, (
        "the rollup task was NOT running inside the lifespan — the feature would "
        f"never arm on a real deployment.\n{result.stdout}\n{result.stderr}"
    )
    assert "AFTER False" in result.stdout, (
        "the rollup task was still running after the lifespan exited — shutdown "
        f"leaks it.\n{result.stdout}\n{result.stderr}"
    )
    # The startup line an operator greps to confirm the interval that was applied.
    assert "latency rollup every" in result.stderr


async def test_the_lifespan_starts_the_rollup_and_stops_it_in_its_finally():
    """The fast structural guard beside the subprocess test above.

    Kept because it costs microseconds and pins one thing the behavioural test
    cannot: that the stop lives in a ``finally``. **Measured, not assumed** —
    moving ``stop_rollup()`` onto the success path only (out of the ``finally``,
    just after the ``yield``) is caught by *this* test and **passes** the
    subprocess one, because that probe exits cleanly and never exercises the
    path where a startup fails after the task was created. Same shape as
    ``test_bearer_admin.test_default_role_appears_nowhere_in_the_bearer_role_resolution``.

    **Exactly what it catches, measured rather than claimed.** Deleting the
    ``start_rollup()`` call fails this test; moving ``stop_rollup()`` out of the
    ``finally`` fails this test; wrapping the call in ``if False and …`` — still
    a call, as far as an AST is concerned — **passes**. A structural test sees
    shape, not reachability, which is exactly why it is no longer the only thing
    covering this wiring.
    """
    import ast
    import pathlib

    import ragstack.api.deps as deps_mod

    tree = ast.parse(pathlib.Path(deps_mod.__file__).read_text())
    lifespan = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    called = {
        node.func.attr
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "start_rollup" in called, "lifespan never arms the latency rollup"

    tries = [node for node in ast.walk(lifespan) if isinstance(node, ast.Try) and node.finalbody]
    stopped_in_finally = {
        node.func.attr
        for t in tries
        for stmt in t.finalbody
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "stop_rollup" in stopped_in_finally, (
        "the rollup is not stopped from a `finally`; a startup that fails after "
        "the task is created would leak it"
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


async def test_overflow_is_reported_on_every_line_not_once(caplog):
    """A truncated histogram must not be something you only learn by reading
    the first row — the row an operator greps for is the one about *their*
    collection."""
    caplog.set_level(logging.INFO)
    hist = LatencyHistogram()
    for i in range(hist_mod.MAX_SERIES + 5):
        hist.record(QUERY_ROUTE, f"c{i}", 0.1)
    assert hist.overflow == 5

    assert emit_rollup(hist) == hist_mod.MAX_SERIES
    lines = _rollups(caplog)
    assert len(lines) == hist_mod.MAX_SERIES
    assert all(line.series_overflow == 5 for line in lines)


async def test_a_request_slower_than_every_bucket_reports_inf_rather_than_the_top_bound(
    caplog,
):
    caplog.set_level(logging.INFO)
    hist = LatencyHistogram()
    hist.record(QUERY_ROUTE, "lib_open_access", 300.0)
    emit_rollup(hist)
    line = _rollups(caplog)[-1]
    assert line.p95_ms_le == "inf"
    assert line.max_ms == "300000.0", "max is measured, so it is exact"
