"""Phase 2: per-request llm / reranker overrides on /query + /retrieve, and the
tenant-gated GET /v1/models/available picker.

The builder unit tests assert resolution (a ref → the right client). The endpoint
tests assert the override is wired (llm override → the generator is *attempted*,
so the fallback prefix flips from "not configured" to "generation failed"; the
fake URL isn't reachable, which is fine) and the guardrails (unknown 404, wrong
task 400).
"""
import httpx
import pytest

from ragstack.api import security
from ragstack.api.deps import build_generator_for, build_reranker_for
from ragstack.api.model_registry import ModelEntry, ModelRegistry

pytestmark = pytest.mark.asyncio

LLM = {
    "id": "llm-a",
    "task": "llm",
    "provider": "vllm",
    "base_urls": ["http://localhost:9999"],
    "model": "test/model-a",
}
RR = {"id": "rr-a", "task": "reranker", "provider": "sidecar", "base_urls": ["http://localhost:9998"]}


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    # keyless dev → admin, so we can register models via the admin surface
    monkeypatch.setattr(security.settings, "api_keys", [])
    monkeypatch.setattr(security.settings, "default_role", security.ROLE_ADMIN)


# --- builders (unit) -------------------------------------------------------- #

async def test_build_generator_for_resolves():
    reg = ModelRegistry([ModelEntry(**LLM)], allowlist=["http://localhost"])
    async with httpx.AsyncClient() as http:
        gen = build_generator_for(reg, http, "llm-a")
        assert gen.llm.base_url == "http://localhost:9999"
        assert gen.llm.model == "test/model-a"


async def test_build_reranker_for_resolves():
    reg = ModelRegistry([ModelEntry(**RR)], allowlist=["http://localhost"])
    async with httpx.AsyncClient() as http:
        rr = build_reranker_for(reg, http, "rr-a")
        assert rr.base_url == "http://localhost:9998"


async def test_builders_reject_unknown_and_wrong_task():
    reg = ModelRegistry([ModelEntry(**LLM), ModelEntry(**RR)], allowlist=["http://localhost"])
    async with httpx.AsyncClient() as http:
        with pytest.raises(KeyError):
            build_generator_for(reg, http, "ghost")
        with pytest.raises(ValueError):
            build_generator_for(reg, http, "rr-a")  # reranker, not llm
        with pytest.raises(ValueError):
            build_reranker_for(reg, http, "llm-a")  # llm, not reranker


# --- endpoints -------------------------------------------------------------- #

async def _register(client, entry):
    r = await client.post("/v1/admin/models/registry", json=entry)
    assert r.status_code == 201, r.text


async def test_query_llm_override_is_used(client):
    # No default generator → the plain query is a "not configured" placeholder.
    base = await client.post("/v1/query", json={"query": "q", "retrieval_mode": "bm25"})
    assert base.json()["answer"].startswith("[LLM not configured]")
    # With an llm override the generator IS resolved and attempted; the fake URL
    # is unreachable, so it degrades to the "generation failed" placeholder —
    # proving the override took effect (a different code path than the default).
    await _register(client, LLM)
    over = await client.post(
        "/v1/query", json={"query": "q", "retrieval_mode": "bm25", "llm": "llm-a"}
    )
    assert over.status_code == 200
    assert over.json()["answer"].startswith("[answer generation failed]")


async def test_query_unknown_llm_is_404(client):
    resp = await client.post("/v1/query", json={"query": "q", "llm": "ghost"})
    assert resp.status_code == 404


async def test_query_wrong_task_llm_is_400(client):
    await _register(client, RR)
    resp = await client.post("/v1/query", json={"query": "q", "llm": "rr-a"})
    assert resp.status_code == 400


async def test_retrieve_reranker_override_accepted(client):
    await _register(client, RR)
    resp = await client.post(
        "/v1/retrieve", json={"query": "q", "retrieval_mode": "bm25", "reranker": "rr-a"}
    )
    # The fake reranker URL is unreachable → graceful fallback to fused order, never a 500.
    assert resp.status_code == 200
    assert "sources" in resp.json()


async def test_retrieve_unknown_reranker_is_404(client):
    resp = await client.post("/v1/retrieve", json={"query": "q", "reranker": "ghost"})
    assert resp.status_code == 404


async def test_retrieve_wrong_task_reranker_is_400(client):
    await _register(client, LLM)
    resp = await client.post("/v1/retrieve", json={"query": "q", "reranker": "llm-a"})
    assert resp.status_code == 400


async def test_available_models_lists_hot_swappable_without_urls(client):
    await _register(client, LLM)
    await _register(client, RR)
    # register a build-time model too — it must NOT appear in /available
    await _register(
        client,
        {"id": "emb", "task": "embedding", "base_urls": ["http://localhost:1"], "model": "e", "dim": 8},
    )
    body = (await client.get("/v1/models/available")).json()
    ids = {m["id"] for m in body["models"]}
    assert ids == {"llm-a", "rr-a"}  # embedding excluded
    for m in body["models"]:
        assert "base_urls" not in m  # URLs are not exposed to non-admin callers
        assert m["task"] in {"llm", "reranker"}
