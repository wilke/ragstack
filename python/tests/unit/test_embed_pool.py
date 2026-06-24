"""Unit tests for the multi-endpoint embedder pool."""
import asyncio

import httpx
import pytest

from ragstack.embed_pool import Endpoint, PooledEmbedder


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


def _pool(*embedders, **kw):
    http = httpx.AsyncClient()
    eps = [Endpoint(e, health_url=f"http://e{i}/health") for i, e in enumerate(embedders)]
    return PooledEmbedder(eps, http=http, **kw)


@pytest.mark.asyncio
async def test_routes_to_an_endpoint():
    pool = _pool(_FakeEmbedder(1.0))
    assert await pool.embed(["a", "b"]) == [[1.0], [1.0]]


@pytest.mark.asyncio
async def test_failover_on_network_error():
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    good = _FakeEmbedder(2.0)
    pool = _pool(bad, good)
    out = await pool.embed(["x"])
    assert out == [[2.0]]
    assert good.calls == 1


@pytest.mark.asyncio
async def test_failover_marks_endpoint_unhealthy():
    bad = _FakeEmbedder(1.0, fail=httpx.ConnectError("down"))
    good = _FakeEmbedder(2.0)
    http = httpx.AsyncClient()
    eps = [Endpoint(bad, "http://bad/health"), Endpoint(good, "http://good/health")]
    pool = PooledEmbedder(eps, http=http)
    await pool.embed(["x"])
    assert eps[0].healthy is False
    assert eps[1].healthy is True


@pytest.mark.asyncio
async def test_all_endpoints_fail_raises():
    pool = _pool(
        _FakeEmbedder(1.0, fail=httpx.ConnectError("down")),
        _FakeEmbedder(2.0, fail=httpx.ConnectError("down")),
    )
    with pytest.raises(RuntimeError):
        await pool.embed(["x"])


@pytest.mark.asyncio
async def test_4xx_propagates_without_failover():
    # A 4xx is a bad-input error — it must propagate (so BatchingEmbedder can
    # quarantine the input) rather than trigger failover, and not demote the endpoint.
    bad_input = _FakeEmbedder(1.0, fail=_status_error(400))
    other = _FakeEmbedder(2.0)
    http = httpx.AsyncClient()
    eps = [Endpoint(bad_input, "http://a/health"), Endpoint(other, "http://b/health")]
    pool = PooledEmbedder(eps, http=http)
    with pytest.raises(httpx.HTTPStatusError):
        await pool.embed(["x"])
    assert eps[0].healthy is True  # not demoted
    assert other.calls == 0  # no failover attempted


@pytest.mark.asyncio
async def test_5xx_fails_over():
    bad = _FakeEmbedder(1.0, fail=_status_error(503))
    good = _FakeEmbedder(2.0)
    pool = _pool(bad, good)
    assert await pool.embed(["x"]) == [[2.0]]


@pytest.mark.asyncio
async def test_backpressure_caps_concurrency():
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

    pool = _pool(_Slow(), _Slow(), max_concurrency=2)
    await asyncio.gather(*(pool.embed(["x"]) for _ in range(8)))
    assert peak <= 2


@pytest.mark.asyncio
async def test_check_health_updates_flags():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if "good" in str(request.url) else 500)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    eps = [Endpoint(_FakeEmbedder(1.0), "http://good/health"),
           Endpoint(_FakeEmbedder(2.0), "http://bad/health")]
    pool = PooledEmbedder(eps, http=http)
    await pool.check_health()
    assert eps[0].healthy is True
    assert eps[1].healthy is False
