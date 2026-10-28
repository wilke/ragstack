"""``include_symbol_glossary`` on ``/v1/retrieve`` (#754 follow-up).

Pins the contract and the hydration behind it:

* the flag off (omitted or ``false``) → no source carries a ``symbol_glossary``
  key at all, and the body is byte-identical either way;
* the flag on → EVERY source carries the key: a BM25 hit keeps the glossary it
  arrived with, a vector-only hit gets it from the text index, a chunk without
  one is ``""``; ranking, scores, ``content`` and ``metadata`` are unchanged;
* the lookup is one batched, tenant-scoped request over the FINAL ``top_k``
  sources only (never the rerank pool), per collection, each source in ITS
  collection's index;
* a text index that raises or times out → ``200``, ``""`` values, one
  rate-limited warning and a counted failure; a retriever with no text index
  (and no reranker) → ``200``, ``""``;
* the served OpenAPI and the contract schemas declare the field, optional and
  not nullable, on ``/v1/retrieve`` only.

No live store: in-memory doubles throughout, the ones the conftest wires.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import jsonschema
import pytest

from ragstack.api.collections import CollectionEntry
from ragstack.api.main import app
from ragstack.config import settings
from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval import glossary as glossary_mod
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores import InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.errors import StoreUnavailable
from tests.api.conftest import _FakeEmbedder

pytestmark = pytest.mark.asyncio

_SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"

GLOSS_DEF = "Qb — the breathing flow rate"
GLOSS_VAL = "Qb — the breathing flow rate; Cv — the chamber volume"

#: The test collection's chunks: ``(id, content, glossary)``. ``c-def`` is the
#: defining sentence (a BM25 hit for "breathing flow rate"), ``c-val`` holds the
#: value and only ever matches by vector, ``c-none`` has no glossary at all.
CHUNKS = [
    ("c-def", "where Qb is the breathing flow rate of one person", GLOSS_DEF),
    ("c-val", "Qb = 0.5 m3/h and Cv = 40 m3 in the chamber", GLOSS_VAL),
    ("c-none", "plain prose about a room with no symbols", ""),
]


@pytest.fixture(autouse=True)
def _fresh_failure_log():
    glossary_mod._reset_failure_log()
    yield
    glossary_mod._reset_failure_log()


def _chunk(cid: str, content: str, glossary: str = "", tenant: str = "public") -> Chunk:
    return Chunk(
        id=cid, doc_id="paper", content=content, metadata={"tenant_id": tenant},
        symbol_glossary=glossary,
    )


class CountingTextIndex(InMemoryTextIndex):
    """The in-memory text index, recording each glossary lookup's ids and scope."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[str], list[str]]] = []

    async def get_symbol_glossaries(self, chunk_ids, tenants, *, timeout=None):
        self.calls.append((list(chunk_ids), list(tenants)))
        return await super().get_symbol_glossaries(chunk_ids, tenants)


class FailingTextIndex(CountingTextIndex):
    """Elasticsearch down: the lookup raises the store's own error."""

    async def get_symbol_glossaries(self, chunk_ids, tenants, *, timeout=None):
        self.calls.append((list(chunk_ids), list(tenants)))
        raise StoreUnavailable(
            "elasticsearch", "elasticsearch get_symbol_glossaries failed", kind="timeout"
        )


class SlowTextIndex(CountingTextIndex):
    """Elasticsearch hanging: slower than ``RERANK_GLOSSARY_TIMEOUT``."""

    async def get_symbol_glossaries(self, chunk_ids, tenants, *, timeout=None):
        self.calls.append((list(chunk_ids), list(tenants)))
        await asyncio.sleep(5.0)
        return await super().get_symbol_glossaries(chunk_ids, tenants)


async def _fill(vs: InMemoryVectorStore, ti: InMemoryTextIndex, chunks=CHUNKS, tenant="public"):
    """Seed like the real stores do: the text index holds the glossary, the
    vector store (Qdrant writes none) holds a glossary-less copy. The copy
    carries the conftest's constant fake embedding so the vector leg finds
    every chunk (cosine 1.0), whatever the query says."""
    full = [_chunk(cid, text, gloss, tenant) for cid, text, gloss in chunks]
    await ti.index(full)
    await vs.upsert([
        c.model_copy(update={"symbol_glossary": "", "embedding": [0.1, 0.2, 0.3, 0.4]})
        for c in full
    ])


def _entry(cid: str, retriever, vs, ti) -> CollectionEntry:
    return CollectionEntry(
        id=cid, label=cid, collection=f"ragstack_{cid}", model="test-model", dim=4,
        chunk_method="fixed", chunk_size=None, chunk_overlap=None, chunk_params={},
        is_shared_surface=False, retriever=retriever, vector_store=vs, text_index=ti,
        embedder=_FakeEmbedder(),
    )


@pytest.fixture
async def gloss(client, request):
    """One registered collection ``gloss`` over a text index of the class the
    test asks for (``CountingTextIndex`` by default, via indirect param)."""
    cls = getattr(request, "param", CountingTextIndex)
    vs, ti = InMemoryVectorStore(), cls()
    await _fill(vs, ti)
    app.state.collections.add(
        _entry("gloss", HybridRetriever(vs, ti, _FakeEmbedder(), collection="gloss"), vs, ti)
    )
    try:
        yield ti
    finally:
        app.state.collections.remove("gloss")


def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS / f"{name}.json").read_text())


def _validate(body: dict) -> None:
    store = {s["$id"]: s for s in (_schema(n) for n in ("source", "retrieve_response"))}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=body, schema=_schema("retrieve_response"), resolver=resolver)


async def _retrieve(client, **body):
    resp = await client.post("/v1/retrieve", json={"query": "breathing flow rate", "top_k": 5, **body})
    assert resp.status_code == 200, resp.text
    return resp


# --------------------------------------------------------------------------- #
# Flag off: no key, byte-identical
# --------------------------------------------------------------------------- #


async def test_flag_off_or_omitted_puts_no_key_on_any_source(client, gloss):
    omitted = await _retrieve(client, collection="gloss")
    explicit = await _retrieve(client, collection="gloss", include_symbol_glossary=False)
    assert omitted.text == explicit.text
    assert omitted.json()["sources"], "the seeded collection must be retrievable"
    assert all("symbol_glossary" not in s for s in omitted.json()["sources"])
    assert gloss.calls == []  # not requested: no text-index read at all
    _validate(omitted.json())


# --------------------------------------------------------------------------- #
# Flag on: every source, BM25 kept, vector-only hydrated, none → ""
# --------------------------------------------------------------------------- #


async def test_flag_on_puts_the_key_on_every_source(client, gloss):
    resp = await _retrieve(client, collection="gloss", include_symbol_glossary=True)
    body = resp.json()
    _validate(body)  # strict schema: symbol_glossary is the only new key
    by_id = {s["chunk_id"]: s for s in body["sources"]}
    assert set(by_id) == {"c-def", "c-val", "c-none"}
    assert all("symbol_glossary" in s for s in body["sources"])
    assert by_id["c-def"]["symbol_glossary"] == GLOSS_DEF  # BM25 hit: kept
    assert by_id["c-val"]["symbol_glossary"] == GLOSS_VAL  # vector-only: hydrated
    assert by_id["c-none"]["symbol_glossary"] == ""  # none: "", never absent
    # ONE batched lookup, for the chunks that arrived without one only.
    assert len(gloss.calls) == 1
    ids, _ = gloss.calls[0]
    assert sorted(ids) == ["c-none", "c-val"]
    # The glossary never leaks into content or metadata.
    for s in body["sources"]:
        assert "Qb —" not in s["content"]
        assert "symbol_glossary" not in s["metadata"]


async def test_flag_changes_nothing_but_the_added_key(client, gloss):
    plain = await _retrieve(client, collection="gloss")
    withg = await _retrieve(client, collection="gloss", include_symbol_glossary=True)
    stripped = [
        {k: v for k, v in s.items() if k != "symbol_glossary"} for s in withg.json()["sources"]
    ]
    assert stripped == plain.json()["sources"]
    assert "degraded" not in withg.json()  # a successful lookup is not a fallback


async def test_vector_mode_hydrates_every_source_from_the_text_index(client, gloss):
    resp = await _retrieve(
        client, collection="gloss", include_symbol_glossary=True, retrieval_mode="vector"
    )
    by_id = {s["chunk_id"]: s["symbol_glossary"] for s in resp.json()["sources"]}
    assert by_id == {"c-def": GLOSS_DEF, "c-val": GLOSS_VAL, "c-none": ""}
    assert len(gloss.calls) == 1 and sorted(gloss.calls[0][0]) == ["c-def", "c-none", "c-val"]


# --------------------------------------------------------------------------- #
# Only the final top_k, after the rerank
# --------------------------------------------------------------------------- #


class _RecordingReranker:
    """Ranks the pool in reverse; ``include_symbol_glossary`` is what the
    rerank-time hydration reads (``_retrieve_fused``)."""

    def __init__(self, include: bool) -> None:
        self.include_symbol_glossary = include
        self.pool_size = 0

    async def score(self, query, candidates, top_k=None):
        self.pool_size = len(candidates)
        ranked = [
            ScoredChunk(chunk=c, score=float(i), retrieval_method="reranked")
            for i, c in enumerate(candidates)
        ][::-1]
        return ranked if top_k is None else ranked[:top_k]


@pytest.mark.parametrize("rerank_reads_glossary", [False, True])
async def test_lookup_covers_the_final_top_k_only_not_the_rerank_pool(
    client, gloss, rerank_reads_glossary
):
    """Six chunks, a rerank pool of six, ``top_k=2``: the response-side lookup
    carries at most the two returned ids. With the reranker reading the
    glossary too (RERANK_INCLUDE_SYMBOL_GLOSSARY on) the pool-wide lookup
    happens first, inside the rerank — and the response still carries the
    key on every source either way."""
    vs = app.state.collections.resolve("gloss").vector_store
    extra = [(f"c-x{i}", f"filler passage number {i}", "") for i in range(3)]
    await _fill(vs, gloss, extra)
    reranker = _RecordingReranker(rerank_reads_glossary)
    app.state.reranker = reranker
    try:
        resp = await _retrieve(
            client, collection="gloss", include_symbol_glossary=True, top_k=2,
            rerank_candidates=6, retrieval_mode="vector",
        )
    finally:
        app.state.reranker = None
    assert reranker.pool_size == 6
    sources = resp.json()["sources"]
    assert len(sources) == 2 and all("symbol_glossary" in s for s in sources)
    returned = {s["chunk_id"] for s in sources}
    final_lookup = gloss.calls[-1][0]
    assert set(final_lookup) <= returned, "the response-side lookup must not see the pool"
    if rerank_reads_glossary:
        assert len(gloss.calls) == 2 and len(gloss.calls[0][0]) == 6  # the rerank's own
    else:
        assert len(gloss.calls) == 1


# --------------------------------------------------------------------------- #
# Failure modes: never a 5xx
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gloss", [FailingTextIndex], indirect=True)
async def test_text_index_failure_is_200_with_empty_values_and_one_warning(client, gloss, caplog):
    with caplog.at_level(logging.WARNING, logger="ragstack.retrieval.glossary"):
        first = await _retrieve(client, collection="gloss", include_symbol_glossary=True)
        second = await _retrieve(client, collection="gloss", include_symbol_glossary=True)
    for resp in (first, second):
        by_id = {s["chunk_id"]: s["symbol_glossary"] for s in resp.json()["sources"]}
        # The key is on every source; the lookups came back empty — but the
        # BM25 hit keeps the glossary it ARRIVED with (nothing to look up).
        assert by_id == {"c-def": GLOSS_DEF, "c-val": "", "c-none": ""}
        assert "degraded" not in resp.json()
    assert len(gloss.calls) == 2
    assert glossary_mod.failure_counts["gloss"] == 2  # every failure counted…
    warnings = [r for r in caplog.records if "symbol glossary hydration failed" in r.getMessage()]
    assert len(warnings) == 1  # …but warned once per collection per minute


@pytest.mark.parametrize("gloss", [SlowTextIndex], indirect=True)
async def test_text_index_timeout_is_200_with_empty_values(client, gloss, monkeypatch, caplog):
    monkeypatch.setattr(settings, "rerank_glossary_timeout", 0.05)
    with caplog.at_level(logging.WARNING, logger="ragstack.retrieval.glossary"):
        resp = await _retrieve(client, collection="gloss", include_symbol_glossary=True)
    by_id = {s["chunk_id"]: s["symbol_glossary"] for s in resp.json()["sources"]}
    assert by_id == {"c-def": GLOSS_DEF, "c-val": "", "c-none": ""}
    assert glossary_mod.failure_counts["gloss"] == 1
    assert any("TimeoutError" in r.getMessage() for r in caplog.records)


async def test_retriever_without_a_text_index_and_no_reranker_answers_empty_strings(client):
    """The conftest's default collection resolves through a proxy retriever
    that exposes no ``text_index`` (and ``app.state.reranker`` is None): a BM25
    hit still carries what it arrived with, everything else is "", nothing
    raises."""
    await _fill(app.state.vector_store, app.state.text_index)
    resp = await _retrieve(client, include_symbol_glossary=True)
    by_id = {s["chunk_id"]: s["symbol_glossary"] for s in resp.json()["sources"]}
    assert set(by_id) == {"c-def", "c-val", "c-none"}
    assert by_id["c-def"] == GLOSS_DEF  # the BM25 copy won the fusion
    assert by_id["c-val"] == "" and by_id["c-none"] == ""
    assert glossary_mod.failure_counts == {}


class _StubRetriever:
    """Returns a fixed list — one indexed chunk and one graph pseudo-chunk."""

    def __init__(self, text_index) -> None:
        self.text_index = text_index
        self.collection = "stub"

    async def retrieve(self, query, top_k=5, filters=None, use_graph=True, tenant_id=None, mode="hybrid"):
        return [
            ScoredChunk(chunk=_chunk("c-val", "Qb = 0.5"), score=1.0, retrieval_method="vector"),
            ScoredChunk(
                chunk=Chunk(id="graph-Qb-defines-rate", doc_id="", content="Qb defines rate",
                            metadata={"tenant_id": "public"}),
                score=0.5, retrieval_method="graph",
            ),
        ]


async def test_graph_pseudo_chunks_get_empty_string_and_are_never_looked_up(client):
    vs, ti = InMemoryVectorStore(), CountingTextIndex()
    await _fill(vs, ti)
    app.state.collections.add(_entry("stub", _StubRetriever(ti), vs, ti))
    try:
        resp = await _retrieve(client, collection="stub", include_symbol_glossary=True)
    finally:
        app.state.collections.remove("stub")
    by_id = {s["chunk_id"]: s["symbol_glossary"] for s in resp.json()["sources"]}
    assert by_id == {"c-val": GLOSS_VAL, "graph-Qb-defines-rate": ""}
    assert ti.calls == [(["c-val"], ti.calls[0][1])]


# --------------------------------------------------------------------------- #
# Scope: tenant and collection
# --------------------------------------------------------------------------- #


async def test_lookup_is_tenant_scoped(client, gloss):
    """Another tenant's glossary for the SAME chunk id is never returned, and
    the lookup carries the caller's readable tenants, not that tenant."""
    await gloss.index([_chunk("c-none", "mallory's copy", "MALLORY'S SECRET", tenant="mallory")])
    resp = await _retrieve(client, collection="gloss", include_symbol_glossary=True)
    by_id = {s["chunk_id"]: s["symbol_glossary"] for s in resp.json()["sources"]}
    assert by_id["c-none"] == ""
    assert "SECRET" not in resp.text
    [(ids, tenants)] = gloss.calls
    assert tenants and "mallory" not in tenants
    assert "public" in tenants  # the seeded chunks are public; the caller may read them


async def test_multi_collection_looks_each_source_up_in_its_own_collection(client):
    """The same chunk id in two collections with DIFFERENT glossaries: each
    stamped copy carries its own collection's, from its own index."""
    added = []
    indexes = {}
    for cid, gloss_text in (("ga", "Qb — rate, as A says"), ("gb", "Qb — rate, as B says")):
        vs, ti = InMemoryVectorStore(), CountingTextIndex()
        await _fill(vs, ti, [("c-val", "Qb = 0.5 m3/h in the chamber", gloss_text)])
        app.state.collections.add(
            _entry(cid, HybridRetriever(vs, ti, _FakeEmbedder(), collection=cid), vs, ti)
        )
        added.append(cid)
        indexes[cid] = ti
    try:
        resp = await _retrieve(
            client, collections=["ga", "gb"], include_symbol_glossary=True, retrieval_mode="vector"
        )
    finally:
        for cid in added:
            app.state.collections.remove(cid)
    body = resp.json()
    _validate(body)
    got = {s["collection"]: s["symbol_glossary"] for s in body["sources"]}
    assert got == {"ga": "Qb — rate, as A says", "gb": "Qb — rate, as B says"}
    assert indexes["ga"].calls == [(["c-val"], indexes["ga"].calls[0][1])]
    assert indexes["gb"].calls == [(["c-val"], indexes["gb"].calls[0][1])]


# --------------------------------------------------------------------------- #
# Contract: schemas and the served OpenAPI
# --------------------------------------------------------------------------- #


async def test_contract_schemas_declare_the_field_on_retrieve_only():
    req = _schema("retrieve_request")["properties"]["include_symbol_glossary"]
    assert req["type"] == "boolean" and req["default"] is False
    assert "include_symbol_glossary" not in _schema("query_request")["properties"]
    src = _schema("source")["properties"]["symbol_glossary"]
    assert src["type"] == "string"  # optional, not nullable
    assert "symbol_glossary" not in _schema("source")["required"]


async def test_served_openapi_matches_the_contract(client):
    spec = app.openapi()["components"]["schemas"]
    prop = spec["RetrieveRequest"]["properties"]["include_symbol_glossary"]
    assert prop["type"] == "boolean" and prop["default"] is False
    assert "include_symbol_glossary" not in spec["QueryRequest"]["properties"]
    src = spec["Source"]["properties"]["symbol_glossary"]
    assert src == {"type": "string", "title": "Symbol Glossary"}  # optional, not nullable


async def test_query_endpoint_does_not_offer_the_field(client):
    """Not on /v1/query: the request models ignore unknown keys (pydantic's
    default), so the field is accepted there like any stray key — and does
    nothing: no source carries a glossary."""
    await _fill(app.state.vector_store, app.state.text_index)
    resp = await client.post(
        "/v1/query", json={"query": "breathing flow rate", "include_symbol_glossary": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sources"]
    assert all("symbol_glossary" not in s for s in resp.json()["sources"])
