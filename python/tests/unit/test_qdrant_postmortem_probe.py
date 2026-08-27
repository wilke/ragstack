"""The opt-in Qdrant post-mortem probe (#427 W9).

W2a already ships ``elapsed_s`` and ``reason`` on every store failure, which
answer *how long* and *which failure class*. Neither can see optimizer/indexing
churn — a different candidate cause from the cold page cache everyone assumes —
and that is the only thing this probe exists to make visible. It does NOT see
page-cache state, and nothing here should ever be read as though it does.

Every test below is a regression guard unless its docstring says "CONTROL".
"""
from __future__ import annotations

import asyncio
import logging
import re

import pytest

pytest.importorskip("qdrant_client")

import httpx  # noqa: E402
from qdrant_client.http.exceptions import (  # noqa: E402
    ResponseHandlingException,
    UnexpectedResponse,
)

from ragstack.observability.context import (  # noqa: E402
    RequestContext,
    RequestContextFilter,
    clear_context,
    set_context,
)
from ragstack.observability.logging_config import LogfmtFormatter  # noqa: E402
from ragstack.stores import qdrant as qdrant_mod  # noqa: E402
from ragstack.stores.errors import KIND_TIMEOUT, StoreUnavailable  # noqa: E402
from ragstack.stores.qdrant import QdrantVectorStore  # noqa: E402

COLLECTION = "sfr_tok256"

TIMEOUT_EXC = ResponseHandlingException(httpx.ReadTimeout("timed out"))
UNREACHABLE_EXC = ResponseHandlingException(httpx.ConnectError("connection refused"))
SERVER_ERROR_EXC = UnexpectedResponse(
    status_code=503, reason_phrase="Service Unavailable", content=b"busy", headers=None
)


class _Info:
    """What qdrant-client's ``get_collection`` returns, reduced to the fields
    ``collection_health`` reads."""

    def __init__(
        self,
        status: str = "yellow",
        optimizer_status: object = "ok",
        segments_count: int = 192,
        points_count: int = 1000,
        indexed_vectors_count: int = 600,
    ) -> None:
        self.status = status
        self.optimizer_status = optimizer_status
        self.segments_count = segments_count
        self.points_count = points_count
        self.indexed_vectors_count = indexed_vectors_count


class _FakeClient:
    """A Qdrant client whose search always fails and whose ``get_collection``
    records that it was called — so "no probe was made" is observable as a count
    of zero, not as the absence of a log line."""

    def __init__(self, exc: Exception, info: object | None = None) -> None:
        self._exc = exc
        self._info = info if info is not None else _Info()
        self.get_collection_calls = 0

    async def query_points(self, **_: object) -> object:
        raise self._exc

    async def get_collection(self, _name: str) -> object:
        self.get_collection_calls += 1
        return self._info


def _store(
    exc: Exception = TIMEOUT_EXC,
    *,
    probe: bool = True,
    info: object | None = None,
    client: object | None = None,
) -> QdrantVectorStore:
    kwargs = {} if probe is None else {"postmortem_probe": probe}
    s = QdrantVectorStore(
        url="http://qdrant.test:6333", collection=COLLECTION, timeout=30, **kwargs
    )
    s._client = client if client is not None else _FakeClient(exc, info)  # type: ignore[assignment]
    return s


async def _failed_search(store: QdrantVectorStore) -> StoreUnavailable:
    with pytest.raises(StoreUnavailable) as ei:
        await store.search([0.1, 0.2], top_k=3)
    return ei.value


def _probe_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "post-mortem probe" in r.getMessage()]


@pytest.fixture(autouse=True)
def _no_leaked_context():
    """One test's request context must not survive into the next — otherwise the
    rid assertions pass or fail on ordering. (context.clear_context exists for
    exactly this.)"""
    clear_context()
    yield
    clear_context()


# --------------------------------------------------------------------------
# Trigger: timeout only
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_fires_on_a_timeout(caplog):
    caplog.set_level(logging.WARNING)
    store = _store(TIMEOUT_EXC)
    err = await _failed_search(store)

    assert err.kind == KIND_TIMEOUT
    assert store._client.get_collection_calls == 1
    (rec,) = _probe_records(caplog)
    assert rec.levelno == logging.WARNING, (
        "the probe line must survive LOG_LEVEL=WARNING alongside the failure line "
        "it explains — at INFO the correlation is lost exactly when it is wanted"
    )
    assert rec.status == "yellow"
    assert rec.segments == 192
    assert rec.probe_collection == COLLECTION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc,expected_kind",
    [(UNREACHABLE_EXC, "unreachable"), (SERVER_ERROR_EXC, "error")],
    ids=["unreachable", "server_error"],
)
async def test_probe_does_not_fire_on_other_failure_kinds(exc, expected_kind, caplog):
    """A store that was never reached will not answer a probe either, and trying
    costs the caller the probe bound for nothing."""
    caplog.set_level(logging.WARNING)
    store = _store(exc)
    err = await _failed_search(store)

    assert err.kind == expected_kind
    assert store._client.get_collection_calls == 0
    assert _probe_records(caplog) == []


@pytest.mark.asyncio
async def test_no_probe_when_the_search_succeeds(caplog):
    """CONTROL — the probe is a failure-path feature; a healthy search must not
    pay for it. Guards against the gate being hoisted out of the except branch."""
    caplog.set_level(logging.WARNING)

    class _OkClient(_FakeClient):
        async def query_points(self, **_: object) -> object:
            class _R:
                points: list[object] = []

            return _R()

    store = _store(client=_OkClient(TIMEOUT_EXC))
    assert await store.search([0.1], top_k=1) == []
    assert store._client.get_collection_calls == 0


# --------------------------------------------------------------------------
# Opt-in
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_by_default_makes_no_request(caplog):
    """Not "logs nothing" — makes no REQUEST. A store that has just failed to
    answer must not be asked anything unless an operator opted in."""
    caplog.set_level(logging.WARNING)
    store = _store(TIMEOUT_EXC, probe=False)
    err = await _failed_search(store)

    assert err.kind == KIND_TIMEOUT
    assert store._client.get_collection_calls == 0
    assert _probe_records(caplog) == []


def test_the_setting_and_its_wiring_default_to_off():
    from ragstack.config import Settings

    assert Settings().qdrant_postmortem_probe is False
    # The constructor default is the one that holds for every non-API caller
    # (scripts, ingest, tests) that never passes the kwarg.
    assert QdrantVectorStore(url="http://x:1", collection="c")._postmortem_enabled is False

    # deps.py must pass it through at BOTH construction sites — the default
    # collection and each registry entry — or the setting silently does nothing
    # on the multi-collection path, which is where the incident happened.
    import inspect

    from ragstack.api import deps

    src = inspect.getsource(deps)
    assert src.count("postmortem_probe=settings.qdrant_postmortem_probe") == 2


# --------------------------------------------------------------------------
# Rate limit
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_to_one_probe_per_collection_per_window(monkeypatch, caplog):
    """Two failures inside the window produce ONE probe; a third after it
    produces a second. Driven by a controllable clock — never a sleep."""
    caplog.set_level(logging.WARNING)
    fake_now = 1000.0
    monkeypatch.setattr(qdrant_mod, "_monotonic", lambda: fake_now)
    store = _store(TIMEOUT_EXC)

    await _failed_search(store)
    assert store._client.get_collection_calls == 1

    fake_now = 1000.0 + qdrant_mod._PROBE_MIN_INTERVAL_S - 0.001  # still inside
    await _failed_search(store)
    assert store._client.get_collection_calls == 1, "second failure re-probed inside the window"

    fake_now = 1000.0 + qdrant_mod._PROBE_MIN_INTERVAL_S + 0.001  # window elapsed
    await _failed_search(store)
    assert store._client.get_collection_calls == 2

    assert len(_probe_records(caplog)) == 2


@pytest.mark.asyncio
async def test_concurrent_timeouts_yield_one_probe(monkeypatch):
    """The gate is checked and stamped with no await in between, so N searches
    failing together on one collection do not launch N probes at once.

    The fake probe **must yield** (``sleep(0)``): a real HTTP round trip does,
    and without it this test is vacuous — 3.12's ``wait_for`` awaits the
    coroutine directly rather than wrapping it in a task, so a non-yielding fake
    lets each search finish its probe before the next one starts and the
    interleaving this guards against never happens. Verified by mutation: with
    the stamp moved after the await, a non-yielding fake still passed.
    """
    monkeypatch.setattr(qdrant_mod, "_monotonic", lambda: 1000.0)

    class _YieldingClient(_FakeClient):
        async def get_collection(self, _name: str) -> object:
            self.get_collection_calls += 1
            await asyncio.sleep(0)
            return self._info

    store = _store(client=_YieldingClient(TIMEOUT_EXC))
    results = await asyncio.gather(
        *(store.search([0.1], top_k=1) for _ in range(5)), return_exceptions=True
    )
    assert all(isinstance(r, StoreUnavailable) for r in results)
    assert store._client.get_collection_calls == 1


# --------------------------------------------------------------------------
# It must never make the failure worse
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hanging_probe_is_bounded_and_cancelled(monkeypatch, caplog):
    """The probe's own bound, not the client's 30–60s one, ends a probe that
    hangs — and the hung request is actually cancelled, not left running.

    The outer ``wait_for`` is a CONTROL bound: it exists so that deleting the
    implementation's ``wait_for`` fails this test with a TimeoutError instead of
    hanging the suite. It is 100x the probe bound, so it can never be the thing
    that ends the probe.
    """
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(qdrant_mod, "_PROBE_TIMEOUT_S", 0.05)

    class _HangingClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(TIMEOUT_EXC)
            self.cancelled = False

        async def get_collection(self, _name: str) -> object:
            self.get_collection_calls += 1
            try:
                await asyncio.Event().wait()  # never set
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

    client = _HangingClient()
    store = _store(client=client)

    with pytest.raises(StoreUnavailable) as ei:
        await asyncio.wait_for(store.search([0.1], top_k=1), 5.0)  # CONTROL bound

    err = ei.value
    assert err.kind == KIND_TIMEOUT
    assert "ReadTimeout" in str(err), "the original failure's sentence must be unchanged"
    assert isinstance(err.__cause__, ResponseHandlingException)
    assert client.cancelled, "the probe was abandoned but its request left in flight"
    (rec,) = _probe_records(caplog)
    assert "probe failed" in rec.getMessage()


@pytest.mark.asyncio
async def test_a_raising_probe_does_not_change_the_failure(caplog):
    caplog.set_level(logging.WARNING)

    class _BrokenClient(_FakeClient):
        async def get_collection(self, _name: str) -> object:
            self.get_collection_calls += 1
            raise RuntimeError("probe blew up")

    store = _store(client=_BrokenClient(TIMEOUT_EXC))
    err = await _failed_search(store)

    assert err.store == "qdrant"
    assert err.kind == KIND_TIMEOUT
    assert isinstance(err.__cause__, ResponseHandlingException)
    assert "30s (QDRANT_TIMEOUT)" in str(err)
    (rec,) = _probe_records(caplog)
    assert "probe failed" in rec.getMessage() and "RuntimeError" in rec.getMessage()


@pytest.mark.asyncio
async def test_cancellation_during_the_probe_propagates(monkeypatch):
    """A cancellation mid-probe must propagate, NOT be swallowed into a 503.

    The docstring on ``_postmortem_probe`` claims this — ``except Exception``,
    deliberately not ``BaseException``, because the caller has gone away and
    answering a request nobody is waiting for is the wrong trade. Review found
    the claim had **no guard**: widening the catch to ``BaseException`` passed
    the entire suite. Under that mutant the search swallows the cancellation and
    raises ``StoreUnavailable``, doing exactly what the docstring says it
    refuses. This is that guard.

    The probe bound is raised well above the test's own timescale so that
    ``wait_for`` cannot be the thing that ends the probe — the cancellation must
    be, or the test is measuring the previous test's behaviour.
    """
    monkeypatch.setattr(qdrant_mod, "_PROBE_TIMEOUT_S", 30.0)
    entered = asyncio.Event()

    class _HangingClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(TIMEOUT_EXC)
            self.cancelled = False

        async def get_collection(self, _name: str) -> object:
            self.get_collection_calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()  # never set
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

    client = _HangingClient()
    store = _store(client=client)

    task = asyncio.ensure_future(store.search([0.1], top_k=1))
    # CONTROL bound: if the probe is never entered this fails in 5s instead of
    # hanging the suite. It can never end the probe — only the cancel below can.
    await asyncio.wait_for(entered.wait(), 5.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), (
        "the cancellation was swallowed and turned into some other outcome — a "
        "503 answered to a caller that had already gone away"
    )
    assert client.cancelled, "the probe's own request was left in flight"


@pytest.mark.asyncio
async def test_elapsed_s_excludes_the_probe(monkeypatch):
    """``elapsed_s`` is the STORE's latency — the measurement #427 could not
    make. Capturing it after the probe would fold up to 2s of our own probe into
    it. Scripted clock: search burns 0.5s, the probe burns 2.0s."""
    ticks = iter([100.0, 100.5, 100.5, 102.5])
    last = [102.5]

    def _clock() -> float:
        try:
            last[0] = next(ticks)
        except StopIteration:
            pass
        return last[0]

    monkeypatch.setattr(qdrant_mod, "perf_counter", _clock)
    store = _store(TIMEOUT_EXC)
    err = await _failed_search(store)

    assert store._client.get_collection_calls == 1
    assert err.elapsed_s == pytest.approx(0.5), (
        "elapsed_s absorbed the probe's own time — it would have read 2.5s for a "
        "0.5s store failure"
    )


# --------------------------------------------------------------------------
# The line itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_probe_line_carries_the_failing_requests_id(caplog):
    """The correlation IS the feature: the probe line is only usable if it sits
    under the same rid as the failure it explains. Uses the real
    RequestContextFilter, the same mechanism that stamps the failure line."""
    caplog.set_level(logging.WARNING)
    caplog.handler.addFilter(RequestContextFilter())
    set_context(RequestContext(request_id="0123456789abcdef", tenant="acme"))

    store = _store(TIMEOUT_EXC)
    await _failed_search(store)

    (rec,) = _probe_records(caplog)
    assert rec.rid == "0123456789abcdef"
    assert rec.tenant == "acme"


@pytest.mark.asyncio
async def test_raw_counters_only_no_derived_backlog(caplog):
    """CollectionHealth records, from live measurement, that ``indexed - points``
    is meaningless in both regimes. So the line carries the two numbers and never
    their difference — asserted on the RENDERED line, and as an exact field set
    so a later addition cannot slip a derived number in."""
    caplog.set_level(logging.WARNING)
    info = _Info(status="yellow", points_count=1000, indexed_vectors_count=600)
    store = _store(TIMEOUT_EXC, info=info)
    await _failed_search(store)

    (rec,) = _probe_records(caplog)
    line = LogfmtFormatter().format(rec)

    assert "status=yellow" in line
    assert "points=1000" in line and "indexed_vectors=600" in line
    # 1000 - 600. The only place it could come from is a derived backlog.
    assert "400" not in line
    assert "backlog" not in line.lower()

    keys = set(re.findall(r"(\w+)=", line)) - {"msg"}
    assert keys == {
        "store",
        "probe_collection",
        "status",
        "optimizer_ok",
        "segments",
        "points",
        "indexed_vectors",
        "probe_ms",
    }


@pytest.mark.asyncio
async def test_a_red_optimizer_is_reported_as_such(caplog):
    """The whole point of the probe: churn/error state that ``elapsed_s`` and
    ``reason`` cannot see."""
    caplog.set_level(logging.WARNING)

    class _Failed:
        error = "optimizer failed: disk full"

    info = _Info(status="red", optimizer_status=_Failed(), segments_count=257)
    store = _store(TIMEOUT_EXC, info=info)
    await _failed_search(store)

    (rec,) = _probe_records(caplog)
    assert rec.status == "red"
    assert rec.optimizer_ok is False
    assert rec.segments == 257
