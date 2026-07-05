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


@pytest.fixture
def det(monkeypatch):
    """Make weighted-random endpoint selection deterministic (first candidate), so
    failover-SEMANTICS tests can rely on a specific endpoint being tried first.
    Routing-DISTRIBUTION tests deliberately omit this to exercise the randomness."""
    import ragstack.embed_pool as _ep

    monkeypatch.setattr(
        _ep.random, "choices", lambda population, weights=None, k=1: [population[0]]
    )


async def test_routes_to_an_endpoint(http):
    pool = _pool(http, _FakeEmbedder(1.0))
    assert await pool.embed(["a", "b"]) == [[1.0], [1.0]]


async def test_failover_on_network_error(http, det):
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    good = _FakeEmbedder(2.0)
    pool = _pool(http, bad, good)
    out = await pool.embed(["x"])
    assert out == [[2.0]]
    assert good.calls == 1


async def test_failover_marks_endpoint_unhealthy(http, det):
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


async def test_4xx_propagates_without_failover(http, det):
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


async def test_5xx_fails_over(http, det):
    bad = _FakeEmbedder(1.0, fail=_status_error(503))
    good = _FakeEmbedder(2.0)
    pool = _pool(http, bad, good)
    assert await pool.embed(["x"]) == [[2.0]]


async def test_retriable_4xx_fails_over_without_demotion(http, det):
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
    pool = PooledEmbedder(eps, http=http, max_concurrency=8)
    await asyncio.gather(*(pool.embed(["x"]) for _ in range(20)))
    # Weighted-random by inverse in-flight load: both endpoints get a meaningful
    # share (spread, not funnelled), balanced by `active` during concurrency.
    assert a.calls >= 3 and b.calls >= 3


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
        for _ in range(30):  # drive until `bad` is picked, fails over, and is demoted
            await pool.embed(["x"])
            if not eps[0].healthy:
                break
        assert eps[0].healthy is False
        before = bad.calls
        bad.fail = None  # backend recovers
        await pool.check_health()  # re-probe restores the flag
        assert eps[0].healthy is True
        # The recovered endpoint rejoins the rotation (weighted-random picks it again).
        for _ in range(20):
            await pool.embed(["y"])
        assert bad.calls > before


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
    # A bad-input 4xx propagates from embed() straight through (no failover); it's
    # bisected and quarantined while good texts still embed. Bad input is bad on
    # EVERY replica of the same model, so this holds whichever endpoint the router
    # (weighted-random) picks — hence both endpoints reject "poison".
    emb = _PerTextEmbedder(1.0, bad={"poison"})
    other = _PerTextEmbedder(9.0, bad={"poison"})
    eps = [Endpoint(emb, "http://a/health"), Endpoint(other, "http://b/health")]
    pool = PooledEmbedder(eps, http=http)
    vecs, quarantined = await pool.embed_isolated(["ok1", "poison", "ok2", "ok3"])
    assert quarantined == 1
    assert vecs[1] is None
    assert vecs[0] is not None and vecs[2] is not None and vecs[3] is not None


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


# --- server-queue-aware routing (vllm:num_requests_waiting) ------------------

def test_parse_waiting_extracts_metric():
    from ragstack.embed_pool import _parse_waiting

    txt = (
        'vllm:num_requests_running{engine="0",model_name="x"} 1.0\n'
        'vllm:num_requests_waiting{engine="0",model_name="x"} 16373.0\n'
    )
    assert _parse_waiting(txt) == 16373
    assert _parse_waiting("no vllm metrics here") == 0  # non-vLLM backend -> 0


async def test_select_prefers_least_queued_but_spreads(http):
    # Weighted-random: least-queued wins the strong majority, swamped is essentially
    # never chosen — but NOT deterministic argmin (that herds independent processes).
    from collections import Counter

    pool = _pool(http, _FakeEmbedder(0), _FakeEmbedder(1), _FakeEmbedder(2))
    pool._eps[0].waiting, pool._eps[1].waiting, pool._eps[2].waiting = 5000, 0, 50
    c = Counter(id(pool._select(set())) for _ in range(300))
    assert c[id(pool._eps[1])] > 200   # least-queued wins the majority
    assert c[id(pool._eps[0])] < 30     # swamped weighted-away


async def test_select_spreads_across_equal_endpoints(http):
    # Anti-herd: equal (empty) endpoints -> selection spreads across ALL of them
    # rather than piling onto a single "minimum".
    pool = _pool(http, _FakeEmbedder(0), _FakeEmbedder(1), _FakeEmbedder(2), _FakeEmbedder(3))
    picks = {id(pool._select(set())) for _ in range(200)}
    assert len(picks) == 4


async def test_select_skips_swamped(http):
    # Only one endpoint under the ceiling -> deterministically chosen (no herd risk).
    pool = _pool(http, _FakeEmbedder(0), _FakeEmbedder(1), max_waiting=512)
    pool._eps[0].waiting = 16000   # swamped (over ceiling)
    pool._eps[1].waiting = 100     # under ceiling
    assert all(pool._select(set()) is pool._eps[1] for _ in range(20))
