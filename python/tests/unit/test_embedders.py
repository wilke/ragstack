"""Unit tests for BatchingEmbedder: bounded batching + poison isolation."""
import httpx
import pytest

from ragstack.embedders import BatchingEmbedder, make_embedder


def _capture_auth_transport(seen: dict) -> httpx.MockTransport:
    """A transport that records the Authorization header and returns one vector."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_openai_embedder_sends_bearer_when_api_key_set():
    seen: dict = {}
    async with httpx.AsyncClient(transport=_capture_auth_transport(seen)) as http:
        emb = make_embedder(
            api="openai", base_url="http://embed", model="m", http=http, api_key="test-key-not-real"
        )
        await emb.embed(["hello"])
    assert seen["authorization"] == "Bearer test-key-not-real"


@pytest.mark.asyncio
async def test_openai_embedder_omits_auth_header_when_no_key():
    seen: dict = {}
    async with httpx.AsyncClient(transport=_capture_auth_transport(seen)) as http:
        emb = make_embedder(api="openai", base_url="http://embed", model="m", http=http)
        await emb.embed(["hello"])
    assert seen["authorization"] is None


class _RecordingBase:
    """Records the size of each batch it receives; returns 1-D vectors."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batch_sizes.append(len(texts))
        return [[float(len(t))] for t in texts]


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://embed/v1/embeddings")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError("err", request=req, response=resp)


class _PoisonBase:
    """Raises an HTTP error for any batch containing the poison text."""

    def __init__(self, poison: str, status: int = 400) -> None:
        self.poison = poison
        self.status = status

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.poison in texts:
            raise _http_error(self.status)
        return [[1.0] for _ in texts]


@pytest.mark.asyncio
async def test_batches_by_item_count():
    base = _RecordingBase()
    emb = BatchingEmbedder(base, max_batch_items=2, max_batch_tokens=10**9)
    out = await emb.embed(["a", "b", "c", "d", "e"])
    assert len(out) == 5
    assert base.batch_sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_batches_by_token_budget():
    base = _RecordingBase()
    # ~11 estimated tokens each (40 // 4 + 1); budget 20 -> one per batch.
    emb = BatchingEmbedder(base, max_batch_items=1000, max_batch_tokens=20, chars_per_token=4)
    await emb.embed(["x" * 40, "x" * 40, "x" * 40])
    assert base.batch_sizes == [1, 1, 1]


@pytest.mark.asyncio
async def test_embed_preserves_order_across_batches():
    base = _RecordingBase()
    emb = BatchingEmbedder(base, max_batch_items=2)
    out = await emb.embed(["aa", "bbbb", "c"])
    assert out == [[2.0], [4.0], [1.0]]


@pytest.mark.asyncio
async def test_embed_isolated_quarantines_poison_input():
    base = _PoisonBase(poison="BAD")
    emb = BatchingEmbedder(base, max_batch_items=4)
    vectors, quarantined = await emb.embed_isolated(["ok1", "BAD", "ok2", "ok3"])
    assert quarantined == 1
    assert vectors[1] is None
    assert vectors[0] == [1.0] and vectors[2] == [1.0] and vectors[3] == [1.0]


@pytest.mark.asyncio
async def test_embed_isolated_reraises_on_infrastructure_error():
    base = _PoisonBase(poison="ok1", status=503)  # 5xx -> backend down, must propagate
    emb = BatchingEmbedder(base, max_batch_items=4)
    with pytest.raises(httpx.HTTPStatusError):
        await emb.embed_isolated(["ok1", "ok2"])


@pytest.mark.asyncio
async def test_embed_isolated_all_ok_returns_no_quarantine():
    base = _PoisonBase(poison="never-present")
    emb = BatchingEmbedder(base, max_batch_items=2)
    vectors, quarantined = await emb.embed_isolated(["a", "b", "c"])
    assert quarantined == 0
    assert all(v == [1.0] for v in vectors)
