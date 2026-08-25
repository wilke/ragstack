"""Evidence fields + typed ids on ``Triple`` and the graph leg's confidence floor
(#347, phase 6 of #201).

What is pinned here:

* the six new fields default empty/zero and ``model_dump()`` stays
  JSON-serialisable (it is the record shape of the archive's reserved
  ``triples`` role, #353);
* both graph stores round-trip them with identical semantics, and re-ingest
  stays idempotent — the MERGE key is unchanged, the evidence props are set
  outside it (``ON CREATE SET`` / ``ON MATCH SET`` on Neo4j, an in-place
  update on the in-memory store), so the count does not grow on repeat;
* ``LLMKGExtractor`` stamps ``derived_by="llm"``, ``confidence=1`` and never
  higher — the no-launder rule is enforced at the model, so a path that tries
  to push 3 through the extractor fails instead of storing it;
* the floor fails OPEN: ``graph_min_confidence=0`` leaves retrieval results
  byte-identical to a golden captured before the fields existed, while a floor
  of 2 drops the LLM triple and keeps the tool-verified one.

The Neo4j store is exercised through the fake-driver harness of
``test_neo4j_store.py`` (no live Neo4j): assertions are on the generated Cypher
and on the rows handed to the driver.
"""
from __future__ import annotations

import json
import sys
import types

import pytest
from pydantic import ValidationError

from ragstack.config import settings
from ragstack.graph import extractor as extractor_mod
from ragstack.graph.extractor import LLMKGExtractor
from ragstack.ingestion.chunkers import RecursiveCharacterChunker
from ragstack.ingestion.pipeline import IngestionPipeline
from ragstack.models import DERIVED_BY_LLM, LLM_MAX_CONFIDENCE, Chunk, Document, Triple
from ragstack.retrieval.retriever import HybridRetriever, filter_by_confidence
from ragstack.stores import InMemoryGraphStore, InMemoryTextIndex, InMemoryVectorStore
from ragstack.stores.neo4j import Neo4jGraphStore
from tests.unit.test_neo4j_store import _FakeDriver, _FakeGraphDatabase

COL = "corpus_x"
TENANT = "public"

EVIDENCE_FIELDS = ("evidence", "chunk_id", "derived_by", "confidence", "subject_id", "object_id")


def _stamped(**overrides) -> Triple:
    """A fully provenance-stamped, tool-verified triple with typed ids."""
    base = {
        "subject": "Escherichia coli K-12", "predicate": "has_genome", "object": "U00096.3",
        "doc_id": "d1", "tenant_id": TENANT, "collection": COL,
        "evidence": "E. coli K-12 MG1655 (accession U00096.3)", "chunk_id": "d1:c7",
        "derived_by": "tool:bvbrc", "confidence": 2,
        "subject_id": "bvbrc:genome:511145.12", "object_id": "bvbrc:accession:U00096.3",
    }
    base.update(overrides)
    return Triple(**base)


def _six(t: Triple) -> dict:
    return {f: getattr(t, f) for f in EVIDENCE_FIELDS}


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

def test_fields_default_empty_and_typed_ids_absent_by_default():
    t = Triple(subject="Alice", predicate="knows", object="Bob")
    assert _six(t) == {
        "evidence": "", "chunk_id": "", "derived_by": "", "confidence": 0,
        "subject_id": "", "object_id": "",
    }


def test_model_dump_is_json_serialisable_and_round_trips():
    """The archive's ``triples`` role (#353) serialises exactly this shape."""
    t = _stamped()
    line = json.dumps(t.model_dump())          # must not raise
    assert Triple.model_validate(json.loads(line)) == t
    assert set(json.loads(line)) == {
        "subject", "predicate", "object", "doc_id", "tenant_id", "collection",
        *EVIDENCE_FIELDS,
    }


@pytest.mark.parametrize("bad", [-1, 4])
def test_confidence_is_bounded_0_to_3(bad):
    with pytest.raises(ValidationError):
        Triple(subject="a", predicate="b", object="c", confidence=bad)


def test_llm_derived_triple_cannot_self_assert_above_1():
    """No-launder rule: belief must never self-assert as evidence."""
    for level in (2, 3):
        with pytest.raises(ValidationError, match="caps confidence"):
            Triple(subject="a", predicate="b", object="c", derived_by="llm", confidence=level)
    # ...while a tool-derived triple may earn 2 and 3.
    assert Triple(subject="a", predicate="b", object="c",
                  derived_by="tool:bvbrc", confidence=3).confidence == 3
    assert LLM_MAX_CONFIDENCE == 1 and DERIVED_BY_LLM == "llm"


# --------------------------------------------------------------------------- #
# In-memory store
# --------------------------------------------------------------------------- #

async def test_memory_store_round_trips_all_six_fields():
    store = InMemoryGraphStore()
    t = _stamped()
    await store.add_triples([t])

    [back] = await store.query_neighborhood("coli", tenant_id=TENANT, collection=COL)
    assert back == t
    assert _six(back) == _six(t)
    assert (back.subject_id, back.object_id) == (t.subject_id, t.object_id)


async def test_memory_store_round_trips_at_depth_2():
    store = InMemoryGraphStore()
    a = _stamped(subject="A", object="B", predicate="p", evidence="A p B")
    b = _stamped(subject="B", object="C", predicate="q", evidence="B q C", confidence=3)
    await store.add_triples([a, b])
    got = await store.query_neighborhood("A", depth=2, tenant_id=TENANT, collection=COL)
    assert {(t.subject, t.evidence, t.confidence) for t in got} == {("A", "A p B", 2), ("B", "B q C", 3)}


async def test_memory_store_reingest_is_idempotent_and_takes_latest_evidence():
    """Key unchanged → the count does not grow; evidence follows ON MATCH SET
    (last writer wins), the same as the Cypher below."""
    store = InMemoryGraphStore()
    first = _stamped(confidence=1, derived_by="llm", evidence="first quote",
                     subject_id="", object_id="")
    await store.add_triples([first])
    await store.add_triples([first])                   # exact repeat
    assert len(store._triples) == 1

    second = _stamped(confidence=2, derived_by="tool:bvbrc", evidence="second quote")
    await store.add_triples([second])                  # same key, better evidence
    assert len(store._triples) == 1
    [back] = await store.query_neighborhood("coli", tenant_id=TENANT, collection=COL)
    assert _six(back) == _six(second)
    assert back.subject_id == "bvbrc:genome:511145.12"


async def test_memory_store_keeps_insertion_order_and_delete_by_doc():
    store = InMemoryGraphStore()
    await store.add_triples([
        _stamped(subject="A", object="B", predicate="p", doc_id="d1"),
        _stamped(subject="A", object="C", predicate="p", doc_id="d2"),
    ])
    assert [t.object for t in store._triples] == ["B", "C"]
    await store.delete_by_doc("d1", tenant_id=TENANT, collection=COL)
    assert [t.object for t in store._triples] == ["C"]


# --------------------------------------------------------------------------- #
# Neo4j store — generated Cypher over the fake driver
# --------------------------------------------------------------------------- #

@pytest.fixture
def neo4j(monkeypatch) -> Neo4jGraphStore:
    mod = types.ModuleType("neo4j")
    mod.AsyncGraphDatabase = _FakeGraphDatabase
    monkeypatch.setitem(sys.modules, "neo4j", mod)
    return Neo4jGraphStore(uri="bolt://x:7687", user="neo4j", password="ragstack")


def _drv(store: Neo4jGraphStore) -> _FakeDriver:
    return store._driver  # type: ignore[return-value]


_REL_KEY = (
    "MERGE (s)-[r:REL {predicate: row.predicate, doc_id: row.doc_id, "
    "tenant_id: row.tenant_id, collection: row.collection}]->(o)"
)
_SET = ", ".join(f"r.{p} = row.{p}" for p in EVIDENCE_FIELDS)


async def test_neo4j_merge_key_unchanged_and_evidence_set_outside_it(neo4j):
    t = _stamped()
    await neo4j.add_triples([t])
    query, params = _drv(neo4j).calls[-1]

    # The four-key MERGE is byte-identical to before #347 ...
    assert _REL_KEY in query
    # ... none of the six names leaked into the key ...
    key_props = query[query.index("[r:REL {") : query.index("}]->(o)")]
    assert not any(p in key_props for p in EVIDENCE_FIELDS)
    # ... and they are written with ON CREATE SET / ON MATCH SET after it.
    assert query.endswith(f"{_REL_KEY} ON CREATE SET {_SET} ON MATCH SET {_SET}")

    [row] = params["rows"]
    assert {k: row[k] for k in EVIDENCE_FIELDS} == _six(t)


async def test_neo4j_reingest_is_one_merge_per_row_not_a_create(neo4j):
    """Idempotence on Neo4j is the MERGE itself: two add_triples calls with the
    same triple produce the same statement with the same key, never a CREATE."""
    t = _stamped()
    await neo4j.add_triples([t])
    await neo4j.add_triples([t])
    queries = [q for q, _ in _drv(neo4j).calls]
    assert len(queries) == 2 and queries[0] == queries[1]
    assert "CREATE (" not in queries[0]


async def test_neo4j_neighborhood_returns_and_maps_the_six_props(neo4j):
    t = _stamped()
    _drv(neo4j).results.append([{
        "subject": t.subject, "predicate": t.predicate, "object": t.object,
        "doc_id": t.doc_id, "tenant_id": t.tenant_id, "collection": t.collection,
        **_six(t),
    }])
    [back] = await neo4j.query_neighborhood("coli", tenant_id=TENANT, collection=COL)
    query, _ = _drv(neo4j).calls[-1]
    for p in EVIDENCE_FIELDS:
        assert f"r.{p} AS {p}" in query
    assert back == t


async def test_neo4j_legacy_edge_without_evidence_props_reads_as_defaults(neo4j):
    """An edge written before #347 has null props: it comes back unknown
    (confidence 0), not invisible."""
    _drv(neo4j).results.append([{
        "subject": "Alice", "predicate": "knows", "object": "Bob",
        "doc_id": "d1", "tenant_id": TENANT, "collection": COL,
        "evidence": None, "chunk_id": None, "derived_by": None, "confidence": None,
        "subject_id": None, "object_id": None,
    }])
    [back] = await neo4j.query_neighborhood("alice", tenant_id=TENANT, collection=COL)
    assert _six(back) == _six(Triple(subject="Alice", predicate="knows", object="Bob"))


# --------------------------------------------------------------------------- #
# Extractor
# --------------------------------------------------------------------------- #

class _StubLLM:
    def __init__(self, response: str) -> None:
        self._response = response

    async def complete_text(self, prompt, max_tokens=512, temperature=0.0) -> str:
        return self._response


_TEXT = "Alice knows Bob. Bob works at Acme Corp in Boston."


async def test_extractor_stamps_llm_confidence_1_chunk_id_and_verbatim_evidence():
    llm = _StubLLM(json.dumps({"triples": [
        {"subject": "Alice", "predicate": "knows", "object": "Bob",
         "evidence": "Alice knows Bob."},
        {"subject": "Bob", "predicate": "works at", "object": "Acme Corp",
         "evidence": "Bob is employed by Acme"},   # not in the text → dropped
    ]}))
    triples = await LLMKGExtractor(llm).extract([Chunk(id="c9", doc_id="d1", content=_TEXT)])
    by_subject = {t.subject: t for t in triples}
    assert by_subject["Alice"].evidence == "Alice knows Bob."
    assert by_subject["Bob"].evidence == ""          # misquote is not evidence
    for t in triples:
        assert (t.derived_by, t.confidence, t.chunk_id, t.doc_id) == ("llm", 1, "c9", "d1")
        assert (t.subject_id, t.object_id) == ("", "")


async def test_extractor_ignores_model_supplied_confidence_and_typed_ids():
    """The model cannot raise its own trust level or invent identifiers."""
    llm = _StubLLM(json.dumps({"triples": [
        {"subject": "Alice", "predicate": "knows", "object": "Bob",
         "confidence": 3, "derived_by": "tool:bvbrc",
         "subject_id": "bvbrc:genome:1", "object_id": "bvbrc:genome:2"},
    ]}))
    [t] = await LLMKGExtractor(llm).extract([Chunk(id="c1", doc_id="d1", content=_TEXT)])
    assert (t.derived_by, t.confidence, t.subject_id, t.object_id) == ("llm", 1, "", "")


async def test_extractor_path_that_tries_confidence_3_fails(monkeypatch):
    """Even a miswired extractor cannot launder: the model rejects llm/3."""
    monkeypatch.setattr(extractor_mod, "_LLM_CONFIDENCE", 3)
    llm = _StubLLM(json.dumps({"triples": [
        {"subject": "Alice", "predicate": "knows", "object": "Bob"}]}))
    with pytest.raises(ValidationError, match="caps confidence"):
        await LLMKGExtractor(llm).extract([Chunk(id="c1", doc_id="d1", content=_TEXT)])


class _StampingExtractor:
    """Emits what LLMKGExtractor would: an llm/1 triple with chunk_id + evidence,
    tenant/collection left for the pipeline."""

    async def extract(self, chunks: list[Chunk]) -> list[Triple]:
        c = chunks[0]
        return [Triple(subject="Alice", predicate="knows", object="Bob", doc_id=c.doc_id,
                       chunk_id=c.id, evidence=c.content[:5], derived_by="llm", confidence=1)]


class _FixedLoader:
    def load(self, source: str) -> list[Document]:
        return [Document(id="d1", content="Alice knows Bob.", source=source)]


async def test_pipeline_stamping_keeps_the_extractor_provenance():
    """The pipeline stamps tenant/collection onto the extractor's triples; that
    must not wipe the provenance the extractor set (end-to-end into the store)."""
    graph = InMemoryGraphStore()
    pipeline = IngestionPipeline(
        loader=_FixedLoader(),
        chunker=RecursiveCharacterChunker(chunk_size=100, chunk_overlap=0),
        embedder=_FakeEmbedder(),
        vector_store=InMemoryVectorStore(),
        text_index=InMemoryTextIndex(),
        graph_store=graph,
        kg_extractor=_StampingExtractor(),
        collection=COL,
    )
    await pipeline.ingest("src", tenant_id=TENANT)
    [t] = graph._triples
    assert (t.tenant_id, t.collection) == (TENANT, COL)
    assert (t.derived_by, t.confidence, t.evidence) == ("llm", 1, "Alice")
    assert t.chunk_id != ""                     # stamped from the chunk, not wiped


def test_prompt_asks_for_evidence_and_still_formats():
    rendered = extractor_mod._PROMPT.format(text="hello")
    assert '"evidence"' in rendered and rendered.endswith("Text:\nhello")


# --------------------------------------------------------------------------- #
# Retrieval — fail-open floor
# --------------------------------------------------------------------------- #

class _FakeEmbedder:
    async def embed(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


# Captured on main before #347 (no evidence fields existed) from exactly the
# fixture built by ``_fixture`` below, with unstamped triples. Regenerate only if
# the retriever's fusion/shaping changes upstream — never to absorb a graph-leg
# change from this feature.
GOLDEN = (  # noqa: E501
    '[{"chunk": {"content": "Alice knows Bob and works at Acme.", "doc_id": "d1", "embedding": [34.0, 1.0], "end_char": 0, "id": "c1", "metadata": {"tenant_id": "public"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.032266458495966696}, {"chunk": {"content": "Bob likes coffee.", "doc_id": "d2", "embedding": [17.0, 1.0], "end_char": 0, "id": "c2", "metadata": {"tenant_id": "public"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.01639344262295082}, {"chunk": {"content": "Alice knows Bob", "doc_id": "d1", "embedding": null, "end_char": 0, "id": "graph-Alice-knows-Bob", "metadata": {"collection": "corpus_x", "tenant_id": "public"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.01639344262295082}, {"chunk": {"content": "Unrelated text about ships.", "doc_id": "d3", "embedding": [27.0, 1.0], "end_char": 0, "id": "c3", "metadata": {"tenant_id": "public"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.016129032258064516}, {"chunk": {"content": "Alice works_at Acme", "doc_id": "d1", "embedding": null, "end_char": 0, "id": "graph-Alice-works_at-Acme", "metadata": {"collection": "corpus_x", "tenant_id": "public"}, "start_char": 0}, "retrieval_method": "hybrid", "score": 0.016129032258064516}]'
)


async def _fixture(stamped: bool) -> tuple[InMemoryVectorStore, InMemoryTextIndex, InMemoryGraphStore]:
    chunks = [
        Chunk(id="c1", doc_id="d1", content="Alice knows Bob and works at Acme.",
              embedding=[34.0, 1.0], metadata={"tenant_id": TENANT}),
        Chunk(id="c2", doc_id="d2", content="Bob likes coffee.",
              embedding=[17.0, 1.0], metadata={"tenant_id": TENANT}),
        Chunk(id="c3", doc_id="d3", content="Unrelated text about ships.",
              embedding=[27.0, 1.0], metadata={"tenant_id": TENANT}),
    ]
    vs, ti, gs = InMemoryVectorStore(), InMemoryTextIndex(), InMemoryGraphStore()
    await vs.upsert(chunks)
    await ti.index(chunks)
    extra = (
        {"evidence": "Alice knows Bob", "chunk_id": "c1", "derived_by": "llm", "confidence": 1}
        if stamped else {}
    )
    await gs.add_triples([
        Triple(subject="Alice", predicate="knows", object="Bob",
               doc_id="d1", tenant_id=TENANT, collection=COL, **extra),
        Triple(subject="Alice", predicate="works_at", object="Acme",
               doc_id="d1", tenant_id=TENANT, collection=COL, **extra),
        Triple(subject="Bob", predicate="likes", object="Coffee",
               doc_id="d2", tenant_id=TENANT, collection=COL, **extra),
    ])
    return vs, ti, gs


async def _run(retriever: HybridRetriever) -> str:
    out = await retriever.retrieve("Alice", top_k=5, use_graph=True, tenant_id=TENANT)
    return json.dumps([json.loads(s.model_dump_json()) for s in out], sort_keys=True)


@pytest.mark.parametrize("stamped", [False, True])
async def test_floor_0_is_byte_identical_to_pre_347_golden(stamped):
    vs, ti, gs = await _fixture(stamped)
    r = HybridRetriever(vs, ti, _FakeEmbedder(), graph_store=gs, collection=COL,
                        graph_min_confidence=0)
    assert await _run(r) == GOLDEN


async def test_default_floor_comes_from_settings_and_is_0():
    assert settings.graph_min_confidence == 0
    vs, ti, gs = await _fixture(stamped=True)
    r = HybridRetriever(vs, ti, _FakeEmbedder(), graph_store=gs, collection=COL)
    assert r.graph_min_confidence is None
    assert await _run(r) == GOLDEN


async def test_floor_2_drops_llm_triple_and_keeps_tool_verified_one():
    gs = InMemoryGraphStore()
    await gs.add_triples([
        Triple(subject="Alice", predicate="knows", object="Bob", doc_id="d1",
               tenant_id=TENANT, collection=COL, derived_by="llm", confidence=1),
        Triple(subject="Alice", predicate="member_of", object="Legacy", doc_id="d0",
               tenant_id=TENANT, collection=COL),                       # unstamped: 0
        _stamped(subject="Alice", object="Acme", predicate="works_at",
                 derived_by="tool:hr", confidence=2),
    ])
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()

    lenient = HybridRetriever(vs, ti, _FakeEmbedder(), graph_store=gs, collection=COL,
                              graph_min_confidence=0)
    got = await lenient.retrieve("Alice", top_k=10, use_graph=True, tenant_id=TENANT)
    assert {s.chunk.content for s in got} == {
        "Alice knows Bob", "Alice member_of Legacy", "Alice works_at Acme"}

    strict = HybridRetriever(vs, ti, _FakeEmbedder(), graph_store=gs, collection=COL,
                             graph_min_confidence=2)
    got = await strict.retrieve("Alice", top_k=10, use_graph=True, tenant_id=TENANT)
    assert [s.chunk.content for s in got] == ["Alice works_at Acme"]


async def test_floor_from_settings_applies_when_not_passed(monkeypatch):
    monkeypatch.setattr(settings, "graph_min_confidence", 2)
    vs, ti, gs = await _fixture(stamped=True)      # all three are llm/1
    r = HybridRetriever(vs, ti, _FakeEmbedder(), graph_store=gs, collection=COL)
    got = await r.retrieve("Alice", top_k=5, use_graph=True, tenant_id=TENANT)
    assert not any(s.chunk.id.startswith("graph-") for s in got)


def test_filter_by_confidence_is_a_no_op_at_floor_0():
    triples = [Triple(subject="a", predicate="b", object="c")]
    assert filter_by_confidence(triples, 0) is triples
    assert filter_by_confidence(triples, 1) == []
