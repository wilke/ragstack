"""The 503 a store failure produces, end to end (#427 W2a + W2b).

Two things are pinned here:

1. **The body is machine-readable.** ``reason`` is the three-value discriminator
   the UI branches on (#427 item D) and ``request_id`` is the id a user can read
   off a screenshot — redundant with the ``X-Request-Id`` header on purpose,
   because a header does not survive a copy-paste into a ticket.

2. **The incident replay.** A ``/v1/query`` whose *Elasticsearch* leg times out
   answers 503 with a reason. Before W2b it answered a bare 500 with a
   traceback: only the Qdrant leg had any error handling at all, so half of
   every hybrid query was uninstrumented. The #427 incident happened to hit the
   instrumented half.

   That test deliberately drives a **real** ``ElasticsearchTextIndex`` with a
   stubbed transport rather than a fake text index that raises
   ``StoreUnavailable`` directly — a fake would exercise only the exception
   handler and would pass with W2b reverted, which is precisely the vacuous
   shape this repo has shipped before.
"""
from __future__ import annotations

import pytest

from ragstack.api.main import app
from ragstack.stores.errors import (
    KIND_TIMEOUT,
    KIND_UNREACHABLE,
    STORE_FAILURE_KINDS,
    StoreUnavailable,
)

es = pytest.importorskip("elasticsearch")


class _UnavailableRetriever:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    async def retrieve(self, *args, **kwargs):
        raise StoreUnavailable(
            "qdrant",
            "qdrant search on 'sfr_tok256' at http://localhost:6333 failed — "
            "ReadTimeout: timed out; per-request timeout is 30s (QDRANT_TIMEOUT)",
            kind=self.kind,
            elapsed_s=30.4,
        )


async def _query_with_retriever(client, retriever):
    saved = app.state.retriever
    app.state.retriever = retriever
    try:
        return await client.post("/v1/query", json={"query": "what is RAG?"})
    finally:
        app.state.retriever = saved


@pytest.mark.asyncio
async def test_503_body_carries_reason_and_request_id(client):
    resp = await _query_with_retriever(client, _UnavailableRetriever(KIND_TIMEOUT))

    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "5"
    body = resp.json()

    assert body["reason"] == KIND_TIMEOUT
    assert body["reason"] in STORE_FAILURE_KINDS
    # The id in the body is the SAME id the header carries — an operator given
    # either one greps the same log lines.
    assert body["request_id"] == resp.headers["x-request-id"]
    assert body["request_id"]

    # The sentence is still there, unchanged. `reason` is added alongside it,
    # not instead of it.
    assert "ReadTimeout" in body["detail"] and "QDRANT_TIMEOUT" in body["detail"]


@pytest.mark.asyncio
async def test_reason_distinguishes_unreachable_from_timeout(client):
    resp = await _query_with_retriever(client, _UnavailableRetriever(KIND_UNREACHABLE))
    assert resp.json()["reason"] == KIND_UNREACHABLE


@pytest.mark.asyncio
async def test_the_503_log_line_carries_the_structured_fields(client, caplog):
    with caplog.at_level("WARNING", logger="ragstack.api.main"):
        await _query_with_retriever(client, _UnavailableRetriever(KIND_TIMEOUT))

    record = next(r for r in caplog.records if "unavailable" in r.getMessage())
    assert record.store == "qdrant"
    assert record.reason == KIND_TIMEOUT
    assert record.elapsed_ms == 30400
    # The sentence is still the message — this is what one greps.
    assert "QDRANT_TIMEOUT" in record.getMessage()


# --------------------------------------------------------------------------- #
# The incident replay
# --------------------------------------------------------------------------- #


class _TimingOutES:
    """A stubbed elasticsearch transport. Everything above it — the guard, the
    classifier, the message builder, the retriever, the router, the exception
    handler — is the real code path."""

    async def search(self, **_: object) -> dict:
        raise es.ConnectionTimeout("Connection timeout caused by: TimeoutError()")

    async def close(self) -> None:  # pragma: no cover - not exercised
        pass


@pytest.mark.asyncio
async def test_a_query_whose_es_leg_times_out_is_a_503_not_a_bare_500(client):
    from ragstack.retrieval.retriever import HybridRetriever
    from ragstack.stores.elasticsearch import ElasticsearchTextIndex

    text_index = ElasticsearchTextIndex(
        url="http://es.test:9200", index="ragstack_lib_open_access"
    )
    text_index._es = _TimingOutES()  # type: ignore[assignment]

    retriever = HybridRetriever(
        app.state.vector_store, text_index, app.state.embedder
    )
    resp = await _query_with_retriever(client, retriever)

    assert resp.status_code == 503, "the text leg must be a 503, exactly as the vector leg is"
    body = resp.json()
    assert body["reason"] == KIND_TIMEOUT
    assert body["request_id"] == resp.headers["x-request-id"]
    # Same shape as the Qdrant sentence: index, instance, cause, and the knob.
    assert "ragstack_lib_open_access" in body["detail"]
    assert "es.test:9200" in body["detail"]
    assert "ELASTICSEARCH_TIMEOUT" in body["detail"]
