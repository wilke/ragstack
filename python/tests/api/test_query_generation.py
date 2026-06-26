"""/v1/query answer generation: real answer when an LLM is wired, placeholder otherwise."""
import pytest

from ragstack.api.main import app


class _FakeGenerator:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.seen_query: str | None = None

    async def generate(self, query: str, sources) -> str:
        self.seen_query = query
        return self.answer


@pytest.mark.asyncio
async def test_query_uses_generator_when_present(client):
    fake = _FakeGenerator("Generated grounded answer.")
    app.state.generator = fake
    try:
        resp = await client.post("/v1/query", json={"query": "what is RAG?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "Generated grounded answer."
        assert fake.seen_query == "what is RAG?"
    finally:
        app.state.generator = None


@pytest.mark.asyncio
async def test_query_placeholder_without_generator(client):
    app.state.generator = None
    resp = await client.post("/v1/query", json={"query": "what is RAG?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body and "sources" in body
    assert "[LLM not configured]" in body["answer"]


class _BoomGenerator:
    async def generate(self, query, sources) -> str:
        raise RuntimeError("LLM endpoint is down")


@pytest.mark.asyncio
async def test_query_degrades_when_generation_fails(client):
    # Retrieval succeeded; an LLM outage must degrade to sources + a note, not 500.
    app.state.generator = _BoomGenerator()
    try:
        resp = await client.post("/v1/query", json={"query": "what is RAG?"})
        assert resp.status_code == 200
        body = resp.json()
        assert "[answer generation failed]" in body["answer"]
        assert "sources" in body
    finally:
        app.state.generator = None
