"""``context_window`` on ``/v1/query`` and ``/v1/retrieve`` (issue #322).

Contract shape on both endpoints, the cap (4 → 422), the default-0 promise
(responses byte-identical to the pre-feature ones — a golden captured from the
unmodified router against ``contracts/fixtures/queries/test_queries.json``),
ranking unchanged with and without the window, and the generator prompt
carrying the context block when the window is on (a fake LLM captures it).
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from ragstack.api.main import app
from ragstack.ingestion.chunkers import link_neighbors_by_document
from ragstack.llm import RagGenerator
from ragstack.models import Chunk

pytestmark = pytest.mark.asyncio

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_QUERIES = _ROOT / "contracts" / "fixtures" / "queries" / "test_queries.json"
_GOLDEN = Path(__file__).with_name("golden_context_window_default.json")
_SCHEMAS = _ROOT / "contracts" / "schemas"

# Three documents, three linked chunks each, worded so each fixture query has a
# clear BM25 hit in one of them. Keep in sync with the golden: it was captured
# over exactly this corpus.
DOCS = {
    "doc-vec": [
        "A vector database stores embeddings.",
        "Vector databases index dense vectors for similarity search.",
        "Approximate nearest neighbour search scales vector lookup.",
    ],
    "doc-bm25": [
        "BM25 is a bag-of-words ranking function.",
        "How does BM25 work? Term frequency saturates and documents are length-normalised.",
        "Sparse retrieval with BM25 complements dense retrieval.",
    ],
    "doc-kg": [
        "A knowledge graph stores entities and relations.",
        "Knowledge graph entities are linked by typed predicates.",
        "Graph traversal expands a query to neighbouring entities.",
    ],
}


def _corpus() -> list[Chunk]:
    out = [
        Chunk(
            id=f"{doc}-c{i}",
            doc_id=doc,
            content=text,
            metadata={"tenant_id": "public", "source": f"{doc}.txt"},
        )
        for doc, texts in DOCS.items()
        for i, text in enumerate(texts)
    ]
    link_neighbors_by_document(out)
    return out


async def _seed() -> None:
    chunks = _corpus()
    await app.state.vector_store.upsert(chunks)
    await app.state.text_index.index(chunks)


def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS / f"{name}.json").read_text())


def _validate(body: dict, schema_name: str) -> None:
    store = {s["$id"]: s for s in (_schema(n) for n in ("source", "query_response", "retrieve_response"))}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=body, schema=_schema(schema_name), resolver=resolver)


class _CapturingLLM:
    """Stands in for OpenAILLM: records the chat messages RagGenerator builds."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None

    async def complete(self, messages, max_tokens: int = 512, temperature: float = 0.0) -> str:
        self.messages = messages
        return "captured"


# --------------------------------------------------------------------------- #
# Contract shape
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint,schema", [("/v1/retrieve", "retrieve_response"), ("/v1/query", "query_response")])
async def test_context_shape_on_both_endpoints(client, endpoint, schema):
    await _seed()
    resp = await client.post(
        endpoint, json={"query": "How does BM25 work?", "top_k": 1, "context_window": 1}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schema)  # strict schemas: context is the ONLY new key
    [src] = body["sources"]
    assert src["chunk_id"] == "doc-bm25-c1"
    assert src["context"] == [
        {"chunk_id": "doc-bm25-c0", "position": -1, "content": DOCS["doc-bm25"][0]},
        {"chunk_id": "doc-bm25-c2", "position": 1, "content": DOCS["doc-bm25"][2]},
    ]
    assert src["score"] > 0  # the source keeps its score; neighbours carry none


async def test_context_is_ordered_by_position_and_bounded_by_the_document(client):
    await _seed()
    resp = await client.post(
        "/v1/retrieve", json={"query": "knowledge graph entities", "top_k": 3, "context_window": 3}
    )
    assert resp.status_code == 200, resp.text
    by_id = {s["chunk_id"]: s for s in resp.json()["sources"]}
    # All three kg chunks are sources, so each one's neighbours are other
    # sources: nothing is attached (never duplicated), no key at all.
    assert set(by_id) == {"doc-kg-c0", "doc-kg-c1", "doc-kg-c2"}
    assert all("context" not in s for s in by_id.values())


async def test_neighbour_that_is_a_source_is_not_duplicated_but_walked_through(client):
    await _seed()
    resp = await client.post(
        "/v1/retrieve", json={"query": "What is a vector database?", "top_k": 5, "context_window": 2}
    )
    assert resp.status_code == 200, resp.text
    by_id = {s["chunk_id"]: s for s in resp.json()["sources"]}
    # doc-vec-c0 and doc-vec-c2 are both sources; c1 is too, so c0's only
    # non-source neighbour within 2 hops is... none: c1 (+1) and c2 (+2) are
    # sources. doc-bm25-c0's next two are attached in order.
    assert "context" not in by_id["doc-vec-c0"]
    assert [c["position"] for c in by_id["doc-bm25-c0"]["context"]] == [1, 2]
    assert [c["chunk_id"] for c in by_id["doc-bm25-c0"]["context"]] == ["doc-bm25-c1", "doc-bm25-c2"]


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_context_window_above_cap_is_422(client, endpoint):
    resp = await client.post(endpoint, json={"query": "x", "context_window": 4})
    assert resp.status_code == 422, resp.text
    assert "context_window" in json.dumps(resp.json()["detail"])


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_negative_context_window_is_422(client, endpoint):
    resp = await client.post(endpoint, json={"query": "x", "context_window": -1})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# Default 0: byte-identical to before the field existed
# --------------------------------------------------------------------------- #


def _cases():
    golden = json.loads(_GOLDEN.read_text())
    for fx in json.loads(_FIXTURE_QUERIES.read_text()):
        yield fx["query"], fx["expected_top_k"], golden[fx["query"]]


@pytest.mark.parametrize("explicit_zero", [False, True], ids=["omitted", "explicit-0"])
async def test_default_window_leaves_responses_byte_identical(client, explicit_zero):
    await _seed()
    for query, top_k, golden in _cases():
        body = {"query": query, "top_k": top_k}
        if explicit_zero:
            body["context_window"] = 0
        for endpoint, key in (("/v1/retrieve", "retrieve"), ("/v1/query", "query")):
            resp = await client.post(endpoint, json=body)
            assert resp.status_code == 200, resp.text
            assert resp.text == golden[key], f"{endpoint} {query!r} drifted from the golden"
            assert all("context" not in s for s in resp.json()["sources"])


# --------------------------------------------------------------------------- #
# Ranking unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint", ["/v1/retrieve", "/v1/query"])
async def test_ranking_identical_with_and_without_window(client, endpoint):
    await _seed()
    body = {"query": "What is a vector database?", "top_k": 5}
    plain = await client.post(endpoint, json=body)
    expanded = await client.post(endpoint, json={**body, "context_window": 1})
    assert plain.status_code == expanded.status_code == 200

    def ranking(resp):
        return [(s["chunk_id"], s["score"]) for s in resp.json()["sources"]]

    assert ranking(plain) == ranking(expanded)
    assert any("context" in s for s in expanded.json()["sources"])
    # The only difference between the two bodies is the added context keys.
    stripped = [{k: v for k, v in s.items() if k != "context"} for s in expanded.json()["sources"]]
    assert stripped == plain.json()["sources"]


# --------------------------------------------------------------------------- #
# Generation: the prompt carries the context block when the window is on
# --------------------------------------------------------------------------- #


async def test_generator_prompt_includes_context_block(client):
    await _seed()
    llm = _CapturingLLM()
    app.state.generator = RagGenerator(llm)  # type: ignore[arg-type]
    try:
        resp = await client.post(
            "/v1/query", json={"query": "How does BM25 work?", "top_k": 1, "context_window": 1}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["answer"] == "captured"
        assert llm.messages is not None
        prompt = llm.messages[-1]["content"]
        expected_block = (
            "[1] (context before)\n"
            f"{DOCS['doc-bm25'][0]}\n"
            "(passage)\n"
            f"{DOCS['doc-bm25'][1]}\n"
            "(context after)\n"
            f"{DOCS['doc-bm25'][2]}"
        )
        assert expected_block in prompt
        assert prompt.rstrip().endswith("Question: How does BM25 work?")
    finally:
        app.state.generator = None


async def test_generator_prompt_unchanged_without_window(client):
    await _seed()
    llm = _CapturingLLM()
    app.state.generator = RagGenerator(llm)  # type: ignore[arg-type]
    try:
        resp = await client.post("/v1/query", json={"query": "How does BM25 work?", "top_k": 1})
        assert resp.status_code == 200, resp.text
        prompt = llm.messages[-1]["content"]
        assert f"[1] {DOCS['doc-bm25'][1]}" in prompt
        assert "(context before)" not in prompt and "(context after)" not in prompt
        assert DOCS["doc-bm25"][0] not in prompt  # neighbours are NOT in the prompt
    finally:
        app.state.generator = None
