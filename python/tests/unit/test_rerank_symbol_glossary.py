"""The cross-encoder reads the chunk's symbol glossary (#754, owner decision
2026-10-22: RERANK_INCLUDE_SYMBOL_GLOSSARY, default on).

Pins four things:

* the passage text — ``content + "\\n" + symbol_glossary`` when on and the
  chunk has one, exactly ``content`` otherwise — and that both scorers use it;
* hydration: a candidate only the vector leg found (Qdrant stores no glossary)
  gets its glossary from its own collection's text index before the rerank, in
  ONE batched, tenant-scoped call, and a failed call degrades to no glossary;
* the response is unchanged apart from order: a source's ``content`` never
  carries the glossary;
* the setting defaults to True and reaches the API's reranker.

No live store: every text index here is in-memory or a recording fake.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from ragstack.api.routers.query import _retrieve_fused, _to_sources
from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval.glossary import hydrate_symbol_glossary
from ragstack.retrieval.retriever import CollectionLeg, HybridRetriever, MultiCollectionRetriever
from ragstack.scoring.scorers import CrossEncoderScorer, SidecarReranker, rerank_passage
from ragstack.stores.errors import StoreUnavailable
from ragstack.stores.memory import InMemoryTextIndex
from ragstack.tenancy import scope_filters

GLOSS = "Qb — the breathing flow rate"


def _chunk(cid: str, content: str, glossary: str = "", tenant: str = "alice") -> Chunk:
    return Chunk(
        id=cid, doc_id="d1", content=content, metadata={"tenant_id": tenant},
        symbol_glossary=glossary,
    )


# --------------------------------------------------------------------------- #
# The passage helper
# --------------------------------------------------------------------------- #


def test_passage_is_content_plus_glossary_when_on() -> None:
    c = _chunk("c1", "Qb = 0.5 m3/h", GLOSS)
    assert rerank_passage(c, True) == f"Qb = 0.5 m3/h\n{GLOSS}"


def test_passage_is_exactly_content_when_off() -> None:
    c = _chunk("c1", "Qb = 0.5 m3/h", GLOSS)
    assert rerank_passage(c, False) == "Qb = 0.5 m3/h"


def test_passage_is_exactly_content_without_a_glossary() -> None:
    c = _chunk("c1", "Qb = 0.5 m3/h ")
    # Byte-identical: no trailing newline, no stripping.
    assert rerank_passage(c, True) == "Qb = 0.5 m3/h "


# --------------------------------------------------------------------------- #
# Both scorers
# --------------------------------------------------------------------------- #


async def _sidecar_documents(include: bool | None, chunks: list[Chunk]) -> tuple[list[str], list]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body)
        n = len(body["documents"])
        return httpx.Response(
            200, json={"scores": [float(n - i) for i in range(n)], "indices": list(range(n))}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        kwargs = {} if include is None else {"include_symbol_glossary": include}
        rr = SidecarReranker("http://crossencoder:50052", http, **kwargs)
        out = await rr.score("q", chunks)
    return captured["documents"], out


async def test_sidecar_sends_the_glossary_when_on() -> None:
    chunks = [_chunk("c1", "Qb = 0.5", GLOSS), _chunk("c2", "no symbols")]
    docs, out = await _sidecar_documents(True, chunks)
    assert docs == [f"Qb = 0.5\n{GLOSS}", "no symbols"]
    # The returned chunks are the ORIGINAL ones: content unchanged.
    assert [s.chunk.content for s in out] == ["Qb = 0.5", "no symbols"]
    assert out[0].chunk is chunks[0]


async def test_sidecar_sends_exactly_content_when_off() -> None:
    chunks = [_chunk("c1", "Qb = 0.5", GLOSS), _chunk("c2", "no symbols")]
    docs, _ = await _sidecar_documents(False, chunks)
    assert docs == ["Qb = 0.5", "no symbols"]


async def test_sidecar_constructor_default_is_off_for_library_callers() -> None:
    """The eval scripts build their own SidecarReranker; the API passes the
    setting explicitly (see the deps test below)."""
    docs, _ = await _sidecar_documents(None, [_chunk("c1", "Qb = 0.5", GLOSS)])
    assert docs == ["Qb = 0.5"]


class _FakeModel:
    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs):
        self.pairs = list(pairs)

        class _Arr(list):
            def tolist(self):
                return list(self)

        return _Arr(float(len(p[1])) for p in pairs)


@pytest.mark.parametrize(
    ("include", "want"),
    [(True, [f"Qb = 0.5\n{GLOSS}", "plain"]), (False, ["Qb = 0.5", "plain"])],
)
async def test_local_cross_encoder_uses_the_same_passage(include: bool, want: list[str]) -> None:
    scorer = CrossEncoderScorer(model_name="unused", include_symbol_glossary=include)
    model = _FakeModel()
    scorer._model = model  # type: ignore[assignment]
    chunks = [_chunk("c1", "Qb = 0.5", GLOSS), _chunk("c2", "plain")]
    out = await scorer.score("q", chunks)
    assert [p[1] for p in model.pairs] == want
    assert {s.chunk.content for s in out} == {"Qb = 0.5", "plain"}


# --------------------------------------------------------------------------- #
# Setting
# --------------------------------------------------------------------------- #


def test_setting_defaults_to_true(monkeypatch) -> None:
    from ragstack.config import Settings

    monkeypatch.delenv("RERANK_INCLUDE_SYMBOL_GLOSSARY", raising=False)
    assert Settings(_env_file=None).rerank_include_symbol_glossary is True
    monkeypatch.setenv("RERANK_INCLUDE_SYMBOL_GLOSSARY", "false")
    assert Settings(_env_file=None).rerank_include_symbol_glossary is False


@pytest.mark.parametrize("flag", [True, False])
async def test_the_api_reranker_carries_the_setting(monkeypatch, flag: bool) -> None:
    from types import SimpleNamespace

    from ragstack.api import deps

    monkeypatch.setattr(deps.settings, "rerank_enabled", True)
    monkeypatch.setattr(deps.settings, "crossencoder_sidecar_url", "http://127.0.0.1:1")
    monkeypatch.setattr(deps.settings, "rerank_include_symbol_glossary", flag)
    async with httpx.AsyncClient() as http:
        built = deps._build_reranker(http)
        assert built is not None and built.include_symbol_glossary is flag
        # A registered reranker (hot swap / per-request override) follows it too.
        entry = SimpleNamespace(base_urls=["http://127.0.0.1:1"])
        assert deps._reranker_from_entry(entry, http).include_symbol_glossary is flag


# --------------------------------------------------------------------------- #
# Hydration
# --------------------------------------------------------------------------- #


class CountingTextIndex(InMemoryTextIndex):
    """The in-memory text index, counting glossary lookups and their scope."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[str], list[str]]] = []

    async def get_symbol_glossaries(self, chunk_ids, tenants):
        self.calls.append((list(chunk_ids), list(tenants)))
        return await super().get_symbol_glossaries(chunk_ids, tenants)


class FailingTextIndex(InMemoryTextIndex):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get_symbol_glossaries(self, chunk_ids, tenants):
        self.calls += 1
        raise StoreUnavailable(
            "elasticsearch", "elasticsearch get_symbol_glossaries failed", kind="timeout"
        )


class VectorOnlyStore:
    """A vector leg that returns its chunks, glossary-less like Qdrant's."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks

    async def search(self, vector, top_k: int = 5, filters=None):
        return [
            ScoredChunk(chunk=c, score=1.0 - i / 100, retrieval_method="vector")
            for i, c in enumerate(self.chunks[:top_k])
        ]


class _Embedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 1.0] for _ in texts]


class RecordingReranker:
    """Records the chunks it is handed; ranks them in reverse order."""

    def __init__(self, include: bool = True) -> None:
        self.include_symbol_glossary = include
        self.seen: list[Chunk] = []

    async def score(self, query, candidates, top_k=None):
        self.seen = list(candidates)
        ranked = [
            ScoredChunk(chunk=c, score=float(i), retrieval_method="reranked")
            for i, c in enumerate(candidates)
        ][::-1]
        return ranked if top_k is None else ranked[:top_k]


async def _fixture(text_index: InMemoryTextIndex | None = None):
    """Two chunks in the text index with glossaries (one of alice's, one of
    bob's with the SAME chunk id), and a vector leg that finds alice's two
    chunks glossary-less. The query matches nothing lexically, so only the
    vector leg returns anything."""
    ti = text_index if text_index is not None else CountingTextIndex()
    await ti.index([
        _chunk("c1", "Qb = 0.5 m3/h", GLOSS, tenant="alice"),
        _chunk("c2", "plain text"),
        _chunk("c1", "Qb = 0.5 m3/h", "bob's private glossary", tenant="bob"),
    ])
    vec = VectorOnlyStore([_chunk("c1", "Qb = 0.5 m3/h"), _chunk("c2", "plain text")])
    retriever = HybridRetriever(vec, ti, _Embedder(), collection="phys")
    return retriever, ti


async def test_hydration_fills_vector_only_candidates_in_one_tenant_scoped_call() -> None:
    retriever, ti = await _fixture()
    filters = scope_filters({}, "alice")
    scored = await retriever.retrieve("zzz", top_k=5, filters=filters, use_graph=False)
    assert all(not s.chunk.symbol_glossary for s in scored)  # the vector leg carries none

    out = await hydrate_symbol_glossary(retriever, scored, filters, "alice")

    assert len(ti.calls) == 1  # ONE batched lookup
    ids, tenants = ti.calls[0]
    assert sorted(ids) == ["c1", "c2"]
    assert tenants == filters["tenant_id"] and "bob" not in tenants
    by_id = {s.chunk.id: s.chunk for s in out}
    assert by_id["c1"].symbol_glossary == GLOSS  # alice's, never bob's
    assert by_id["c2"].symbol_glossary == ""
    # Everything else is untouched: order, scores, content, metadata.
    assert [s.chunk.id for s in out] == [s.chunk.id for s in scored]
    assert [s.score for s in out] == [s.score for s in scored]
    assert [s.chunk.content for s in out] == [s.chunk.content for s in scored]
    assert [s.chunk.metadata for s in out] == [s.chunk.metadata for s in scored]


async def test_hydration_skips_candidates_that_already_have_one_and_graph_chunks() -> None:
    retriever, ti = await _fixture()
    scored = [
        ScoredChunk(chunk=_chunk("c1", "x", "already"), score=1.0),
        ScoredChunk(chunk=_chunk("graph-a-b-c", "a b c"), score=0.5, retrieval_method="graph"),
    ]
    out = await hydrate_symbol_glossary(retriever, scored, scope_filters({}, "alice"), "alice")
    assert ti.calls == []
    assert out is scored


async def test_hydration_never_reads_unscoped() -> None:
    retriever, ti = await _fixture()
    scored = [ScoredChunk(chunk=_chunk("c1", "x"), score=1.0)]
    out = await hydrate_symbol_glossary(retriever, scored, {}, None)
    assert ti.calls == [] and out is scored


async def test_hydration_degrades_to_no_glossary_on_error(caplog) -> None:
    retriever, ti = await _fixture(FailingTextIndex())
    scored = [ScoredChunk(chunk=_chunk("c1", "x"), score=1.0)]
    with caplog.at_level(logging.WARNING, logger="ragstack.retrieval.glossary"):
        out = await hydrate_symbol_glossary(
            retriever, scored, scope_filters({}, "alice"), "alice"
        )
    assert ti.calls == 1
    assert out is scored
    assert any("rerank glossary hydration failed" in r.getMessage() for r in caplog.records)


async def test_multi_collection_hydrates_each_candidate_from_its_own_collection() -> None:
    ti_a, ti_b = CountingTextIndex(), CountingTextIndex()
    await ti_a.index([_chunk("c1", "Qb", "gloss from A")])
    await ti_b.index([_chunk("c1", "Qb", "gloss from B", tenant="carol")])
    leg_a = CollectionLeg(
        id="A", retriever=HybridRetriever(VectorOnlyStore([]), ti_a, _Embedder()), physical="pa"
    )
    leg_b = CollectionLeg(
        id="B", retriever=HybridRetriever(VectorOnlyStore([]), ti_b, _Embedder()), physical="pb",
        extra_tenants=["carol"],  # alice reads carol's chunks in B only (a share)
    )
    multi = MultiCollectionRetriever([leg_a, leg_b])
    scored = [
        ScoredChunk(chunk=_chunk("c1", "Qb"), score=1.0, collection="A"),
        ScoredChunk(chunk=_chunk("c1", "Qb", tenant="carol"), score=0.9, collection="B"),
    ]
    out = await hydrate_symbol_glossary(multi, scored, {}, "alice")
    assert [s.chunk.symbol_glossary for s in out] == ["gloss from A", "gloss from B"]
    assert len(ti_a.calls) == 1 and len(ti_b.calls) == 1
    assert "carol" not in ti_a.calls[0][1]  # each leg keeps its own scope
    assert "carol" in ti_b.calls[0][1]
    assert [s.collection for s in out] == ["A", "B"]


# --------------------------------------------------------------------------- #
# Through the router's fusion + rerank
# --------------------------------------------------------------------------- #


async def test_retrieve_fused_reranks_vector_hits_with_their_glossary() -> None:
    retriever, ti = await _fixture()
    filters = scope_filters({}, "alice")
    reranker = RecordingReranker(include=True)
    scored = await _retrieve_fused(
        retriever, reranker, "zzz", ["zzz"], 2, filters, False, tenant_id="alice",
    )
    assert len(ti.calls) == 1
    seen = {c.id: c.symbol_glossary for c in reranker.seen}
    assert seen == {"c1": GLOSS, "c2": ""}
    # The response's sources are what they would have been: content without
    # the glossary, metadata without it.
    sources = _to_sources(scored)
    assert {s.content for s in sources} == {"Qb = 0.5 m3/h", "plain text"}
    for s in sources:
        assert GLOSS not in s.content
        assert "symbol_glossary" not in s.metadata


async def test_multi_query_fusion_keeps_the_glossary_through_hydration() -> None:
    """HyDE/multi-query re-fuse the per-variant lists and the LAST variant's
    copy of a chunk survives. 'Qb' hits c1 by BM25 (glossary), 'zzz' only by
    vector (none) — so the fused copy has none until hydration restores it."""
    retriever, ti = await _fixture()
    filters = scope_filters({}, "alice")
    reranker = RecordingReranker(include=True)
    await _retrieve_fused(
        retriever, reranker, "Qb", ["Qb", "zzz"], 2, filters, False, tenant_id="alice",
    )
    assert {c.id: c.symbol_glossary for c in reranker.seen}["c1"] == GLOSS
    assert len(ti.calls) == 1


@pytest.mark.parametrize(
    ("reranker", "rerank"),
    [(RecordingReranker(include=False), None), (RecordingReranker(include=True), False),
     (None, None)],
)
async def test_no_lookup_when_the_setting_is_off_or_no_rerank_runs(reranker, rerank) -> None:
    retriever, ti = await _fixture()
    await _retrieve_fused(
        retriever, reranker, "zzz", ["zzz"], 2, scope_filters({}, "alice"), False,
        rerank=rerank, tenant_id="alice",
    )
    assert ti.calls == []
    if reranker is not None and rerank is None:
        assert all(not c.symbol_glossary for c in reranker.seen)


async def test_a_failed_lookup_still_reranks() -> None:
    retriever, ti = await _fixture(FailingTextIndex())
    reranker = RecordingReranker(include=True)
    scored = await _retrieve_fused(
        retriever, reranker, "zzz", ["zzz"], 2, scope_filters({}, "alice"), False,
        tenant_id="alice",
    )
    assert ti.calls == 1
    assert [c.id for c in reranker.seen] == ["c1", "c2"]
    assert all(s.retrieval_method == "reranked" for s in scored)
