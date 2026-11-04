"""The router helpers behind ``include_symbol_glossary`` on ``/v1/retrieve``
(#754 follow-up): ``_to_sources`` puts ``symbol_glossary`` on every source
only when asked (``""`` for none, the key absent otherwise — the ``Source``
serializer's omit-when-None rule), and ``_glossary_for_sources`` hydrates the
final list through the reranker's own machinery, degrading to no glossary
rather than raising. The HTTP-level behaviour is in
``tests/api/test_retrieve_symbol_glossary.py``.
"""
from __future__ import annotations

import pytest

from ragstack.api.routers.query import _glossary_for_sources, _to_sources
from ragstack.models import Chunk, ScoredChunk, Source
from ragstack.retrieval import glossary as glossary_mod
from ragstack.retrieval.retriever import HybridRetriever
from ragstack.stores.errors import StoreUnavailable
from ragstack.stores.memory import InMemoryTextIndex
from ragstack.tenancy import scope_filters

GLOSS = "Qb — the breathing flow rate"


@pytest.fixture(autouse=True)
def _fresh_failure_log():
    glossary_mod._reset_failure_log()
    yield
    glossary_mod._reset_failure_log()


def _chunk(cid: str, glossary: str = "", tenant: str = "alice") -> Chunk:
    return Chunk(
        id=cid, doc_id="d", content=f"content of {cid}", metadata={"tenant_id": tenant},
        symbol_glossary=glossary,
    )


def _scored(*chunks: Chunk) -> list[ScoredChunk]:
    return [ScoredChunk(chunk=c, score=1.0 - i / 10) for i, c in enumerate(chunks)]


# --------------------------------------------------------------------------- #
# Source / _to_sources
# --------------------------------------------------------------------------- #


def test_source_omits_an_unset_glossary_and_keeps_an_empty_one() -> None:
    base = {"doc_id": "d", "chunk_id": "c", "content": "x", "score": 1.0}
    assert "symbol_glossary" not in Source(**base).model_dump()
    assert Source(**base, symbol_glossary="").model_dump()["symbol_glossary"] == ""
    assert Source(**base, symbol_glossary=GLOSS).model_dump()["symbol_glossary"] == GLOSS


def test_to_sources_default_puts_no_key_on_any_source() -> None:
    out = _to_sources(_scored(_chunk("c1", GLOSS), _chunk("c2")))
    assert all(s.symbol_glossary is None for s in out)
    assert all("symbol_glossary" not in s.model_dump() for s in out)


def test_to_sources_with_the_flag_puts_the_key_on_every_source() -> None:
    out = _to_sources(
        _scored(_chunk("c1", GLOSS), _chunk("c2"), _chunk("graph-a-b")),
        include_symbol_glossary=True,
    )
    assert [s.model_dump()["symbol_glossary"] for s in out] == [GLOSS, "", ""]
    # Nothing else moves: content and metadata are what they were.
    assert [s.content for s in out] == ["content of c1", "content of c2", "content of graph-a-b"]
    assert all("symbol_glossary" not in s.metadata for s in out)


# --------------------------------------------------------------------------- #
# _glossary_for_sources
# --------------------------------------------------------------------------- #


class _Vec:
    async def search(self, vector, top_k=5, filters=None):
        return []


class _Emb:
    async def embed(self, texts):
        return [[0.0, 1.0] for _ in texts]


class _Counting(InMemoryTextIndex):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[str], list[str]]] = []

    async def get_symbol_glossaries(self, chunk_ids, tenants, *, timeout=None):
        self.calls.append((list(chunk_ids), list(tenants)))
        return await super().get_symbol_glossaries(chunk_ids, tenants)


class _Failing(_Counting):
    async def get_symbol_glossaries(self, chunk_ids, tenants, *, timeout=None):
        self.calls.append((list(chunk_ids), list(tenants)))
        raise StoreUnavailable("elasticsearch", "down", kind="connection")


async def test_hydrates_only_the_chunks_without_one_in_one_scoped_call() -> None:
    ti = _Counting()
    await ti.index([_chunk("c2", GLOSS), _chunk("c2", "bob's", tenant="bob")])
    retriever = HybridRetriever(_Vec(), ti, _Emb(), collection="phys")
    scored = _scored(_chunk("c1", "kept"), _chunk("c2"), _chunk("c3"), _chunk("graph-x"))
    filters = scope_filters({}, "alice")

    out = await _glossary_for_sources(retriever, scored, filters, "alice")

    assert ti.calls == [(["c2", "c3"], filters["tenant_id"])]
    assert "bob" not in ti.calls[0][1]
    assert [s.chunk.symbol_glossary for s in out] == ["kept", GLOSS, "", ""]
    assert [s.chunk.id for s in out] == ["c1", "c2", "c3", "graph-x"]
    assert [s.score for s in out] == [s.score for s in scored]


async def test_empty_list_and_no_text_index_are_no_ops() -> None:
    ti = _Counting()
    retriever = HybridRetriever(_Vec(), ti, _Emb())
    assert await _glossary_for_sources(retriever, [], scope_filters({}, "a"), "a") == []
    assert ti.calls == []

    class _NoIndex:
        async def retrieve(self, *a, **k):
            return []

    scored = _scored(_chunk("c1"))
    out = await _glossary_for_sources(_NoIndex(), scored, scope_filters({}, "a"), "a")
    assert out is scored and out[0].chunk.symbol_glossary == ""


async def test_a_failing_lookup_degrades_to_no_glossary_and_counts() -> None:
    ti = _Failing()
    retriever = HybridRetriever(_Vec(), ti, _Emb(), collection="phys")
    scored = _scored(_chunk("c1"))
    out = await _glossary_for_sources(retriever, scored, scope_filters({}, "alice"), "alice")
    assert out is scored
    assert len(ti.calls) == 1
    assert glossary_mod.failure_counts["phys"] == 1
    # And the response built from it says "" — never absent, never an error.
    assert _to_sources(out, include_symbol_glossary=True)[0].model_dump()["symbol_glossary"] == ""


async def test_never_reads_unscoped() -> None:
    ti = _Counting()
    await ti.index([_chunk("c1", GLOSS)])
    retriever = HybridRetriever(_Vec(), ti, _Emb())
    scored = _scored(_chunk("c1"))
    out = await _glossary_for_sources(retriever, scored, {}, None)
    assert ti.calls == [] and out is scored
