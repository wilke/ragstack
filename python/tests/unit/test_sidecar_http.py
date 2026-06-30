"""Unit tests for the shared SidecarClient (mock transport)."""
import httpx
import pytest

from ragstack.sidecar_http import DEFAULT_TIMEOUT, SidecarClient


def _client(handler, base_url="http://sidecar:50053/") -> SidecarClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SidecarClient(base_url, http)


def test_base_url_is_normalised():
    c = SidecarClient("http://sidecar:50053/", http=httpx.AsyncClient())
    assert c.base_url == "http://sidecar:50053"


def test_default_timeout():
    c = SidecarClient("http://x", http=httpx.AsyncClient())
    assert c.timeout == DEFAULT_TIMEOUT == 120.0


def test_custom_timeout():
    c = SidecarClient("http://x", http=httpx.AsyncClient(), timeout=5.0)
    assert c.timeout == 5.0


@pytest.mark.asyncio
async def test_post_json_builds_url_and_returns_body():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["content"] = request.content
        return httpx.Response(200, json={"ok": True})

    # Leading slash on the path must not double up against the trimmed base.
    body = await _client(handler).post_json("/embed", {"texts": ["a"]})

    assert body == {"ok": True}
    assert seen["url"] == "http://sidecar:50053/embed"
    assert seen["method"] == "POST"
    assert b'"texts"' in seen["content"]  # JSON-encoded body sent


@pytest.mark.asyncio
async def test_post_json_path_without_leading_slash():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://sidecar:50053/v1/embeddings"
        return httpx.Response(200, json={})

    await _client(handler).post_json("v1/embeddings", {})


@pytest.mark.asyncio
async def test_post_json_forwards_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={})

    await _client(handler).post_json("x", {}, headers={"Authorization": "Bearer tok"})


@pytest.mark.asyncio
async def test_post_json_raises_for_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    with pytest.raises(httpx.HTTPStatusError) as exc:
        await _client(handler).post_json("rerank", {})
    assert exc.value.response.status_code == 503


@pytest.mark.asyncio
async def test_configured_timeout_is_used_on_request():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    await SidecarClient("http://x", http, timeout=7.5).post_json("p", {})

    # httpx exposes the effective per-request timeout via request extensions.
    assert seen["timeout"] == {
        "connect": 7.5,
        "read": 7.5,
        "write": 7.5,
        "pool": 7.5,
    }
