"""A store that cannot answer is the deployment's problem, not the caller's:
/v1/query must say 503 with the reason, never a bare 500."""
import pytest

from ragstack.api.main import app
from ragstack.stores.errors import StoreUnavailable


class _UnavailableRetriever:
    async def retrieve(self, *args, **kwargs):
        raise StoreUnavailable(
            "qdrant",
            "qdrant search on 'sfr_tok256' at http://localhost:6333 failed — "
            "ReadTimeout: timed out; per-request timeout is 30s (QDRANT_TIMEOUT)",
        )


@pytest.mark.asyncio
async def test_query_reports_503_when_the_vector_store_times_out(client):
    saved = app.state.retriever
    app.state.retriever = _UnavailableRetriever()
    try:
        resp = await client.post("/v1/query", json={"query": "what is RAG?"})
    finally:
        app.state.retriever = saved
    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "5"
    detail = resp.json()["detail"]
    assert detail.startswith("qdrant unavailable: ")
    assert "ReadTimeout" in detail and "QDRANT_TIMEOUT" in detail
