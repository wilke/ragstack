"""Unit tests for the multi-endpoint embedder pool."""
import asyncio

import httpx
import pytest

from ragstack.embed_pool import Endpoint, PooledEmbedder


@pytest.fixture
async def http():
    """A real AsyncClient that is always closed (no ResourceWarning leaks)."""
    async with httpx.AsyncClient() as client:
        yield client


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://e/embed")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))


class _FakeEmbedder:
    """Returns a tagged vector; optionally always raises ``fail``."""

    def __init__(self, tag: float, fail: Exception | None = None) -> None:
        self.tag = tag
        self.fail = fail
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return [[self.tag] for _ in texts]


def _pool(http, *embedders, **kw):
    eps = [Endpoint(e, health_url=f"http://e{i}/health") for i, e in enumerate(embedders)]
    return PooledEmbedder(eps, http=http, **kw)


async def test_routes_to_an_endpoint(http):
    pool = _pool(http, _FakeEmbedder(1.0))
    assert await pool.embed(["a", "b"]) == [[1.0], [1.0]]


async def test_failover_on_network_error(http):
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    good = _FakeEmbedder(2.0)
    pool = _pool(http, bad, good)
    out = await pool.embed(["x"])
    assert out == [[2.0]]
    assert good.calls == 1


async def test_failover_marks_endpoint_unhealthy(http):
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    good = _FakeEmbedder(2.0)
    eps = [Endpoint(bad, "http://bad/health"), Endpoint(good, "http://good/health")]
    pool = PooledEmbedder(eps, http=http)
    await pool.embed(["x"])
    assert eps[0].healthy is False
    assert eps[1].healthy is True


async def test_all_endpoints_fail_raises(http):
    pool = _pool(
        http,
        _FakeEmbedder(1.0, fail=httpx.ConnectError("down")),
        _FakeEmbedder(2.0, fail=httpx.ConnectError("down")),
    )
    with pytest.raises(RuntimeError):
        await pool.embed(["x"])


async def test_4xx_propagates_without_failover(http):
    # A bad-input 4xx (400) must propagate (so BatchingEmbedder can quarantine the
    # input) rather than trigger failover, and not demote the endpoint.
    bad_input = _FakeEmbedder(1.0, fail=_status_error(400))
    other = _FakeEmbedder(2.0)
    eps = [Endpoint(bad_input, "http://a/health"), Endpoint(other, "http://b/health")]
    pool = PooledEmbedder(eps, http=http)
    with pytest.raises(httpx.HTTPStatusError):
        await pool.embed(["x"])
    assert eps[0].healthy is True  # not demoted
    assert other.calls == 0  # no failover attempted


async def test_5xx_fails_over(http):
    bad = _FakeEmbedder(1.0, fail=_status_error(503))
    good = _FakeEmbedder(2.0)
    pool = _pool(http, bad, good)
    assert await pool.embed(["x"]) == [[2.0]]


async def test_retriable_4xx_fails_over_without_demotion(http):
    # A 429 (rate-limited / busy) is NOT bad input: it must fail over to another
    # endpoint rather than propagate (which would make BatchingEmbedder quarantine
    # good chunks), and the busy endpoint must NOT be demoted.
    busy = _FakeEmbedder(1.0, fail=_status_error(429))
    good = _FakeEmbedder(2.0)
    eps = [Endpoint(busy, "http://busy/health"), Endpoint(good, "http://good/health")]
    pool = PooledEmbedder(eps, http=http)
    assert await pool.embed(["x"]) == [[2.0]]
    assert good.calls == 1
    assert eps[0].healthy is True  # 429 = busy, not down


async def test_backpressure_caps_concurrency(http):
    active = 0
    peak = 0
    lock = asyncio.Lock()

    class _Slow:
        async def embed(self, texts):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return [[1.0] for _ in texts]

    pool = _pool(http, _Slow(), _Slow(), max_concurrency=2)
    await asyncio.gather(*(pool.embed(["x"]) for _ in range(8)))
    assert peak <= 2


async def test_least_loaded_distributes_across_endpoints(http):
    # Headline claim: under concurrency, requests spread to the least-loaded
    # endpoint instead of funnelling to the first one.
    class _Slow(_FakeEmbedder):
        async def embed(self, texts):
            self.calls += 1
            await asyncio.sleep(0.02)
            return [[self.tag] for _ in texts]

    a, b = _Slow(1.0), _Slow(2.0)
    eps = [Endpoint(a, "http://a/health"), Endpoint(b, "http://b/health")]
    pool = PooledEmbedder(eps, http=http, max_concurrency=4)
    await asyncio.gather(*(pool.embed(["x"]) for _ in range(4)))
    assert a.calls >= 1 and b.calls >= 1  # not all funnelled to one endpoint
    assert abs(a.calls - b.calls) <= 1  # balanced by in-flight load


async def test_check_health_updates_flags():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if "good" in str(request.url) else 500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        eps = [Endpoint(_FakeEmbedder(1.0), "http://good/health"),
               Endpoint(_FakeEmbedder(2.0), "http://bad/health")]
        pool = PooledEmbedder(eps, http=http)
        await pool.check_health()
        assert eps[0].healthy is True
        assert eps[1].healthy is False


async def test_recovered_endpoint_rejoins_after_health_probe():
    # End-to-end recovery: an endpoint demoted by failover rejoins the rotation
    # once a health probe finds it healthy again.
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    good = _FakeEmbedder(2.0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200))
    ) as http:
        eps = [Endpoint(bad, "http://bad/health"), Endpoint(good, "http://good/health")]
        pool = PooledEmbedder(eps, http=http)  # default 30s interval: no auto-probe
        await pool.embed(["x"])  # bad fails over to good, bad demoted
        assert eps[0].healthy is False
        bad.fail = None  # backend recovers
        await pool.check_health()  # re-probe restores the flag
        assert eps[0].healthy is True
        # Least-loaded ties go to the first endpoint, so the recovered one is reused.
        assert await pool.embed(["y"]) == [[1.0]]
        assert bad.calls == 2


class _PerTextEmbedder:
    """Embeds each text unless it's in ``bad`` (raises 4xx) — models a poison input
    that fails only when its sub-batch reaches it via bisection."""

    def __init__(self, tag: float, bad: set[str], status: int = 400) -> None:
        self.tag = tag
        self.bad = bad
        self.status = status
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        for t in texts:
            if t in self.bad:
                raise _status_error(self.status)
        return [[self.tag] for _ in texts]


async def test_embed_isolated_quarantines_bad_input(http):
    # A single genuinely-bad (4xx) input is bisected out; the rest embed, and the
    # returned vectors align to the inputs with None in the quarantined slot.
    emb = _PerTextEmbedder(1.0, bad={"bad"})
    pool = _pool(http, emb)
    vecs, quarantined = await pool.embed_isolated(["a", "bad", "c"])
    assert quarantined == 1
    assert vecs == [[1.0], None, [1.0]]


async def test_embed_isolated_no_bad_inputs(http):
    emb = _PerTextEmbedder(2.0, bad=set())
    pool = _pool(http, emb)
    vecs, quarantined = await pool.embed_isolated(["a", "b", "c"])
    assert quarantined == 0
    assert vecs == [[2.0], [2.0], [2.0]]


async def test_embed_isolated_5xx_propagates(http):
    # Infra failure (5xx on every endpoint) must PROPAGATE, not quarantine, so
    # --resume / --batch-retries re-feed the batch (no data loss). The pool raises
    # RuntimeError once failover is exhausted.
    bad = _FakeEmbedder(1.0, fail=_status_error(503))
    pool = _pool(http, bad)
    with pytest.raises(RuntimeError):
        await pool.embed_isolated(["x", "y"])


async def test_embed_isolated_network_error_propagates(http):
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    pool = _pool(http, bad)
    with pytest.raises(RuntimeError):
        await pool.embed_isolated(["x", "y"])


async def test_embed_isolated_bad_input_fails_over_then_quarantines(http):
    # A bad-input 4xx propagates from embed() straight through (no failover), so a
    # second healthy endpoint is never consulted for the bad text — it's bisected
    # and quarantined. Good texts still embed on the first endpoint.
    emb = _PerTextEmbedder(1.0, bad={"poison"})
    other = _FakeEmbedder(9.0)
    eps = [Endpoint(emb, "http://a/health"), Endpoint(other, "http://b/health")]
    pool = PooledEmbedder(eps, http=http)
    vecs, quarantined = await pool.embed_isolated(["ok1", "poison", "ok2", "ok3"])
    assert quarantined == 1
    assert vecs[1] is None
    assert vecs[0] == [1.0] and vecs[2] == [1.0] and vecs[3] == [1.0]


async def test_health_refresh_is_interval_gated(http, monkeypatch):
    pool = _pool(http, _FakeEmbedder(1.0), health_interval=100.0)
    probes = 0

    async def fake_check():
        nonlocal probes
        probes += 1

    monkeypatch.setattr(pool, "check_health", fake_check)
    await pool._maybe_refresh_health()
    assert probes == 0  # first probe waits a full interval, not the first request
    pool._last_health -= 200.0  # simulate the interval elapsing
    await pool._maybe_refresh_health()
    assert probes == 1
    await pool._maybe_refresh_health()  # gate closes again immediately after
    assert probes == 1


# --------------------------------------------------------------------------- #
# request fan-out (#308): one oversized embed() call must use the whole fleet
# --------------------------------------------------------------------------- #


class _RecordingEmbedder:
    """Fake endpoint embedder: records request sizes and live concurrency."""

    def __init__(self, tag: str, log: list, gauge: dict):
        self.tag, self.log, self.gauge = tag, log, gauge

    async def embed(self, texts):
        self.gauge["now"] += 1
        self.gauge["max"] = max(self.gauge["max"], self.gauge["now"])
        try:
            self.log.append((self.tag, len(texts)))
            await asyncio.sleep(0.01)  # hold the slot so overlap is observable
            return [[float(hash((self.tag, t)) % 97)] for t in texts]
        finally:
            self.gauge["now"] -= 1


def _fanout_pool(n_eps: int, max_concurrency: int, request_batch: int):
    log: list = []
    gauge = {"now": 0, "max": 0}
    eps = [Endpoint(_RecordingEmbedder(f"ep{i}", log, gauge), f"http://e{i}/health")
           for i in range(n_eps)]
    pool = PooledEmbedder(eps, http=None, max_concurrency=max_concurrency,
                          request_batch=request_batch)
    pool._maybe_refresh_health = _no_health(pool)
    return pool, log, gauge


def _no_health(pool):
    async def noop():
        return None
    return noop


@pytest.mark.asyncio
async def test_oversized_call_is_split_and_runs_concurrently():
    """The OA-pilot failure shape: one whole-shard embed() call rode a single
    endpoint while five idled (58 texts/s against a 2,606 texts/s fleet)."""
    pool, log, gauge = _fanout_pool(n_eps=3, max_concurrency=6, request_batch=10)
    texts = [f"t{i}" for i in range(100)]
    out = await pool.embed(texts)
    assert len(out) == 100
    assert len(log) == 10                       # 100/10 sub-requests
    assert all(size <= 10 for _, size in log)   # bounded request size
    assert gauge["max"] > 1, "sub-requests must overlap, not serialize"
    assert len({tag for tag, _ in log}) > 1, "fan-out must reach several endpoints"


@pytest.mark.asyncio
async def test_split_preserves_order():
    pool, _, _ = _fanout_pool(n_eps=2, max_concurrency=4, request_batch=7)
    texts = [f"text-{i}" for i in range(40)]
    out = await pool.embed(texts)
    # each vector is a deterministic function of its text per endpoint; re-embed
    # single texts to check alignment irrespective of which endpoint served them
    for i in (0, 6, 7, 13, 39):
        candidates = {float(hash((f"ep{e}", texts[i])) % 97) for e in range(2)}
        assert out[i][0] in candidates, f"vector {i} not derived from texts[{i}]"


@pytest.mark.asyncio
async def test_small_call_is_a_single_request():
    pool, log, _ = _fanout_pool(n_eps=3, max_concurrency=6, request_batch=128)
    await pool.embed([f"t{i}" for i in range(50)])
    assert len(log) == 1


@pytest.mark.asyncio
async def test_concurrency_cap_still_bounds_the_fanout():
    pool, _, gauge = _fanout_pool(n_eps=4, max_concurrency=2, request_batch=5)
    await pool.embed([f"t{i}" for i in range(60)])
    assert gauge["max"] <= 2
