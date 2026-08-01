"""Unit tests for the RAGStack MCP server tool logic.

The HTTP layer is mocked with ``httpx.MockTransport`` (as ``test_llm.py`` does)
so no live RAGStack server is required. These exercise the backend directly —
they do not need the ``mcp`` SDK installed.
"""
from __future__ import annotations

import httpx
import pytest

from ragstack.mcp.backend import RagStackBackend, RagStackConfig


def _backend(handler, config: RagStackConfig | None = None) -> RagStackBackend:
    cfg = config or RagStackConfig(base_url="http://rag.test")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RagStackBackend(cfg, http)


def _source(doc_id: str, chunk_id: str, content: str, score: float, **meta) -> dict:
    return {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "content": content,
        "score": score,
        "metadata": meta,
    }


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_config_from_env_reads_and_strips_trailing_slash():
    cfg = RagStackConfig.from_env(
        {
            "RAGSTACK_BASE_URL": "http://localhost:8030/",
            "RAGSTACK_API_KEY": "secret",
            "RAGSTACK_COLLECTION": "papers",
        }
    )
    assert cfg.base_url == "http://localhost:8030"
    assert cfg.api_key == "secret"
    assert cfg.collection == "papers"


def test_config_from_env_defaults_are_none():
    cfg = RagStackConfig.from_env({})
    assert cfg.api_key is None
    assert cfg.collection is None


# --------------------------------------------------------------------------- #
# search  →  POST /v1/retrieve
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_formats_ranked_chunks_with_titles():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = __import__("json").loads(req.content)
        return httpx.Response(
            200,
            json={
                "sources": [
                    _source("d1", "c1", "Paris is the capital of France.", 0.91, title="Geo"),
                    _source("d2", "c2", "It has about two million people.", 0.42, filename="pop.txt"),
                ]
            },
        )

    out = await _backend(handler).search("capital of France", top_k=2)
    assert captured["path"] == "/v1/retrieve"
    assert captured["body"] == {"query": "capital of France", "top_k": 2}
    # Ranked, with title precedence (title, then filename), scores, ids, snippet.
    assert "[1] Geo  (score 0.9100)" in out
    assert "[2] pop.txt  (score 0.4200)" in out
    assert "doc_id: d1  chunk_id: c1" in out
    assert "Paris is the capital of France." in out


@pytest.mark.asyncio
async def test_search_uses_default_collection_and_argument_override():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(__import__("json").loads(req.content))
        return httpx.Response(200, json={"sources": []})

    cfg = RagStackConfig(base_url="http://rag.test", collection="default_coll")
    backend = _backend(handler, cfg)
    await backend.search("q")  # falls back to configured default
    await backend.search("q", collection="other")  # explicit override wins
    assert seen[0]["collection"] == "default_coll"
    assert seen[1]["collection"] == "other"


@pytest.mark.asyncio
async def test_search_empty_results_is_friendly():
    handler = lambda req: httpx.Response(200, json={"sources": []})  # noqa: E731
    out = await _backend(handler).search("nothing here")
    assert "No chunks matched" in out and "nothing here" in out


@pytest.mark.asyncio
async def test_search_clamps_top_k():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(__import__("json").loads(req.content))
        return httpx.Response(200, json={"sources": []})

    backend = _backend(handler)
    await backend.search("q", top_k=999)
    await backend.search("q", top_k=0)
    assert seen[0]["top_k"] == 50
    assert seen[1]["top_k"] == 1


# --------------------------------------------------------------------------- #
# answer  →  POST /v1/query
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_answer_returns_generated_answer_and_sources():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/query"
        return httpx.Response(
            200,
            json={
                "answer": "The capital of France is Paris.",
                "sources": [_source("d1", "c1", "France's capital is Paris.", 0.9, title="Geo")],
                "rewritten_queries": ["capital of France"],
            },
        )

    out = await _backend(handler).answer("What is the capital of France?")
    assert "Answer:\nThe capital of France is Paris." in out
    assert "Sources:" in out
    assert "[1] Geo  (score 0.9000)" in out


@pytest.mark.asyncio
async def test_answer_handles_llm_not_configured_placeholder():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "[LLM not configured] retrieved 1 chunks for query 'q'; top score 0.9000",
                "sources": [_source("d1", "c1", "some passage", 0.9)],
                "rewritten_queries": ["q"],
            },
        )

    out = await _backend(handler).answer("q")
    assert "no LLM is configured" in out
    # The placeholder pseudo-answer is NOT presented as a real answer.
    assert "Answer:" not in out
    # But the retrieved sources are still surfaced.
    assert "Sources:" in out and "some passage" in out


@pytest.mark.asyncio
async def test_answer_handles_generation_failed_placeholder():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "[answer generation failed] retrieved 1 chunks for query 'q'; top score 0.5000",
                "sources": [_source("d1", "c1", "passage", 0.5)],
                "rewritten_queries": ["q"],
            },
        )

    out = await _backend(handler).answer("q")
    assert "answer generation failed" in out
    assert "Answer:" not in out
    assert "passage" in out


# --------------------------------------------------------------------------- #
# list_collections  →  GET /v1/collections
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_collections_formats_entries():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.url.path == "/v1/collections"
        return httpx.Response(
            200,
            json={
                "default": "papers",
                "collections": [
                    {
                        "id": "papers",
                        "label": "Research papers",
                        "model": "bge-large",
                        "dim": 1024,
                        "default": True,
                        "count": 1200,
                        "text_count": 1200,
                    },
                    {
                        "id": "notes",
                        "label": "",
                        "model": "sfr",
                        "dim": 4096,
                        "default": False,
                        "count": 30,
                        "text_count": None,
                    },
                ],
            },
        )

    out = await _backend(handler).list_collections()
    assert "Available collections (default: papers):" in out
    assert "- papers [default]" in out and '"Research papers"' in out
    assert "1200 vector chunks" in out
    assert "- notes" in out
    assert "text count n/a" in out


@pytest.mark.asyncio
async def test_list_collections_empty():
    handler = lambda req: httpx.Response(200, json={"collections": [], "default": None})  # noqa: E731
    out = await _backend(handler).list_collections()
    assert "no collections registered" in out


# --------------------------------------------------------------------------- #
# Error handling: never a stack trace
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_connection_refused_is_clear_message():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    out = await _backend(handler).search("q")
    assert "Cannot reach RAGStack" in out
    assert "http://rag.test" in out


@pytest.mark.asyncio
async def test_http_500_surfaces_detail():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    out = await _backend(handler).answer("q")
    assert "HTTP 500" in out and "boom" in out


@pytest.mark.asyncio
async def test_http_401_points_at_api_key():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    out = await _backend(handler).list_collections()
    assert "401" in out and "RAGSTACK_API_KEY" in out


@pytest.mark.asyncio
async def test_503_llm_unavailable_is_graceful():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "llm backend down"})

    out = await _backend(handler).answer("q")
    assert "503" in out and "unavailable" in out


@pytest.mark.asyncio
async def test_api_key_sent_as_header_when_configured():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("X-API-Key")
        return httpx.Response(200, json={"sources": []})

    cfg = RagStackConfig(base_url="http://rag.test", api_key="s3cr3t")
    await _backend(handler, cfg).search("q")
    assert seen["key"] == "s3cr3t"


@pytest.mark.asyncio
async def test_no_api_key_header_when_absent():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("X-API-Key")
        return httpx.Response(200, json={"sources": []})

    await _backend(handler).search("q")
    assert seen["key"] is None


@pytest.mark.asyncio
async def test_empty_query_short_circuits_without_call():
    called = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"sources": []})

    backend = _backend(handler)
    assert "No query" in await backend.search("   ")
    assert "No query" in await backend.answer("")
    assert called["n"] == 0


# --------------------------------------------------------------------------- #
# Server wiring (requires the mcp SDK; skipped if not installed)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_build_server_registers_three_tools():
    pytest.importorskip("mcp")
    from ragstack.mcp.server import build_server

    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    server = build_server(RagStackConfig(base_url="http://rag.test"), http=http)
    names = {t.name for t in await server.list_tools()}
    assert names == {"search", "answer", "list_collections"}
    await http.aclose()
