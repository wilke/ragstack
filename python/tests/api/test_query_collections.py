"""``collections`` on ``/v1/query`` and ``/v1/retrieve`` (issue #253):
contract shape on both endpoints, the 422s (with ``collection``, N=6,
duplicates, empty), the single-element equivalence (same bytes as the singular
form except the added ``collection`` stamp), a member the caller can't read →
404 for the whole request, and ``context_window`` over two collections
attaching each source's neighbours from ITS collection.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from ragstack.api.collections import CollectionEntry
from ragstack.api.main import app
from ragstack.ingestion.chunkers import link_neighbors_by_document
from ragstack.models import Chunk
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from tests.api.conftest import _FakeEmbedder

pytestmark = pytest.mark.asyncio

_SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"
ENDPOINTS = ["/v1/retrieve", "/v1/query"]


def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS / f"{name}.json").read_text())


def _validate(body: dict, schema_name: str) -> None:
    names = ("source", "query_response", "retrieve_response")
    store = {s["$id"]: s for s in (_schema(n) for n in names)}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=body, schema=_schema(schema_name), resolver=resolver)


def _doc(doc: str, texts: list[str]) -> list[Chunk]:
    out = [
        Chunk(id=f"{doc}-c{i}", doc_id=doc, content=t, metadata={"tenant_id": "public"})
        for i, t in enumerate(texts)
    ]
    link_neighbors_by_document(out)
    return out


# Two collections. ``shared`` is the same document (same chunk ids) ingested
# into both, with DIFFERENT neighbour text per collection so context expansion
# reveals which collection a neighbour was fetched from.
CORPUS = {
    "col_a": [
        _doc("shared", ["A before", "How does BM25 work? A copy", "A after"]),
        _doc("only-a", ["Vector databases store embeddings, in A."]),
    ],
    "col_b": [
        _doc("shared", ["B before", "How does BM25 work? B copy", "B after"]),
        _doc("only-b", ["Knowledge graphs store entities, in B."]),
    ],
}


@pytest.fixture
async def two_collections(client):
    added = []
    for cid, docs in CORPUS.items():
        vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
        chunks = [c for d in docs for c in d]
        await vs.upsert(chunks)
        await ti.index(chunks)
        app.state.collections.add(CollectionEntry(
            id=cid, label=cid, collection=f"ragstack_{cid}", model="test-model", dim=4,
            chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
            is_shared_surface=False, retriever=HybridRetriever(vs, ti, _FakeEmbedder()),
            vector_store=vs, text_index=ti, embedder=_FakeEmbedder(),
        ))
        added.append(cid)
    try:
        yield added
    finally:
        for cid in added:
            app.state.collections.remove(cid)


# --------------------------------------------------------------------------- #
# contract shape
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint,schema", [("/v1/retrieve", "retrieve_response"), ("/v1/query", "query_response")])
async def test_contract_shape_on_both_endpoints(client, two_collections, endpoint, schema):
    resp = await client.post(
        endpoint, json={"query": "How does BM25 work?", "top_k": 10, "collections": ["col_a", "col_b"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _validate(body, schema)  # strict schemas: `collection` is the only new key
    sources = body["sources"]
    assert sources and all(s["collection"] in {"col_a", "col_b"} for s in sources)
    # The shared document appears once PER collection, each copy stamped.
    copies = sorted(s["collection"] for s in sources if s["chunk_id"] == "shared-c1")
    assert copies == ["col_a", "col_b"]
    # Ranked by fused score, strictly non-increasing.
    scores = [s["score"] for s in sources]
    assert scores == sorted(scores, reverse=True)


async def test_served_openapi_declares_collections_and_the_stamp(client):
    spec = app.openapi()
    for name in ("QueryRequest", "RetrieveRequest"):
        prop = spec["components"]["schemas"][name]["properties"]["collections"]
        assert {"maxItems": 5, "minItems": 1}.items() <= {
            k: v for a in prop.get("anyOf", [prop]) for k, v in a.items()
        }.items()
    src = spec["components"]["schemas"]["Source"]["properties"]["collection"]
    assert src == {"type": "string", "title": "Collection"}  # optional, not nullable


# --------------------------------------------------------------------------- #
# 422s
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_collection_and_collections_together_is_422(client, two_collections, endpoint):
    resp = await client.post(
        endpoint, json={"query": "x", "collection": "col_a", "collections": ["col_b"]}
    )
    assert resp.status_code == 422, resp.text
    assert "mutually exclusive" in json.dumps(resp.json()["detail"])


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_six_collections_is_422(client, endpoint):
    resp = await client.post(endpoint, json={"query": "x", "collections": [f"c{i}" for i in range(6)]})
    assert resp.status_code == 422, resp.text
    assert "collections" in json.dumps(resp.json()["detail"])


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_duplicate_collections_is_422(client, two_collections, endpoint):
    resp = await client.post(endpoint, json={"query": "x", "collections": ["col_a", "col_a"]})
    assert resp.status_code == 422, resp.text
    assert "duplicates" in json.dumps(resp.json()["detail"])


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_empty_collections_is_422(client, endpoint):
    resp = await client.post(endpoint, json={"query": "x", "collections": []})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# equivalence, refusal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_single_element_equals_singular_plus_the_stamp(client, two_collections, endpoint):
    body = {"query": "How does BM25 work?", "top_k": 5, "context_window": 1}
    singular = await client.post(endpoint, json={**body, "collection": "col_a"})
    plural = await client.post(endpoint, json={**body, "collections": ["col_a"]})
    assert singular.status_code == plural.status_code == 200
    s_body, p_body = singular.json(), plural.json()
    assert all(s["collection"] == "col_a" for s in p_body["sources"])
    assert all("collection" not in s for s in s_body["sources"])
    # Same bytes once the stamp is removed — ranking, scores, context, answer.
    for s in p_body["sources"]:
        del s["collection"]
    assert json.dumps(p_body, sort_keys=True) == json.dumps(s_body, sort_keys=True)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_unknown_member_is_404_for_the_whole_request(client, two_collections, endpoint):
    resp = await client.post(endpoint, json={"query": "x", "collections": ["col_a", "nope"]})
    assert resp.status_code == 404, resp.text
    assert "nope" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# context_window over two collections
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_context_window_attaches_neighbours_from_the_right_collection(
    client, two_collections, endpoint
):
    resp = await client.post(
        endpoint,
        json={"query": "How does BM25 work?", "top_k": 2, "collections": ["col_a", "col_b"],
              "context_window": 1},
    )
    assert resp.status_code == 200, resp.text
    sources = resp.json()["sources"]
    by_collection = {s["collection"]: s for s in sources}
    assert set(by_collection) == {"col_a", "col_b"}
    assert all(s["chunk_id"] == "shared-c1" for s in sources)
    # Same chunk id in both, but each copy's neighbours come from ITS collection.
    assert [c["content"] for c in by_collection["col_a"]["context"]] == ["A before", "A after"]
    assert [c["content"] for c in by_collection["col_b"]["context"]] == ["B before", "B after"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_default_pointer_next_to_its_target_is_422(client, two_collections, endpoint):
    """``default`` is a pointer (#276): naming it beside the collection it
    points at is the same leg twice — refused after resolution, before any
    retrieval runs."""
    from tests.api.conftest import SHARED_ID

    resp = await client.post(endpoint, json={"query": "x", "collections": ["default", SHARED_ID]})
    assert resp.status_code == 422, resp.text
    assert "same collection" in resp.json()["detail"]
