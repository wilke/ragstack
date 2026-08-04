"""Post-fusion shaping: per-document diversity cap + boilerplate demotion.

Both are opt-in reorderings of the fused candidate pool applied *before* the cut
to ``top_k``. The invariant that makes them safe — and that every test here
asserts in some form — is that they never remove a candidate: the output is a
permutation of the input, so the caller still gets ``top_k`` results.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.boilerplate import BOILERPLATE_KEY
from ragstack.models import Chunk, ScoredChunk
from ragstack.retrieval.retriever import HybridRetriever


def _pool(*specs: tuple[str, str]) -> list[ScoredChunk]:
    """A fused list from ``(chunk_id, doc_id)`` pairs, descending by score."""
    return [
        ScoredChunk(
            chunk=Chunk(id=cid, doc_id=doc, content=f"content of {cid}"),
            score=1.0 - i / 100,
            retrieval_method="hybrid",
        )
        for i, (cid, doc) in enumerate(specs)
    ]


def _ids(scored: list[ScoredChunk]) -> list[str]:
    return [s.chunk.id for s in scored]


def _retriever(**kwargs) -> HybridRetriever:
    return HybridRetriever(object(), object(), object(), **kwargs)


# --- per-document cap -------------------------------------------------------
def test_cap_breaks_a_single_document_monopoly() -> None:
    """The live failure: 3 of the top 5 chunks came from one document."""
    fused = _pool(
        ("a1", "A"), ("a2", "A"), ("a3", "A"), ("a4", "A"), ("a5", "A"),
        ("b1", "B"), ("c1", "C"), ("b2", "B"),
    )
    shaped = _retriever(max_per_doc=2).shape(fused)

    assert _ids(shaped)[:5] == ["a1", "a2", "b1", "c1", "b2"]
    # Nothing is discarded — the overflow keeps its fused order at the back.
    assert _ids(shaped)[5:] == ["a3", "a4", "a5"]
    assert len(shaped) == len(fused)


def test_cap_preserves_order_when_no_document_exceeds_it() -> None:
    fused = _pool(("a1", "A"), ("b1", "B"), ("a2", "A"), ("c1", "C"))
    assert _ids(_retriever(max_per_doc=2).shape(fused)) == ["a1", "b1", "a2", "c1"]


def test_cap_off_by_default_is_a_no_op() -> None:
    fused = _pool(("a1", "A"), ("a2", "A"), ("a3", "A"))
    r = _retriever()
    assert r.max_per_doc == 0
    assert _ids(r.shape(fused)) == ["a1", "a2", "a3"]


def test_cap_still_fills_top_k_when_only_one_document_matches() -> None:
    """A query whose only good matches genuinely all live in one paper must not
    come back short — the cap costs nothing in that case."""
    fused = _pool(("a1", "A"), ("a2", "A"), ("a3", "A"), ("a4", "A"))
    shaped = _retriever(max_per_doc=1).shape(fused)
    assert _ids(shaped[:3]) == ["a1", "a2", "a3"]


# --- boilerplate demotion ---------------------------------------------------
def test_demotion_uses_the_ingest_time_flag_when_present() -> None:
    fused = _pool(("boiler", "A"), ("good", "B"), ("also-good", "C"))
    fused[0].chunk.metadata[BOILERPLATE_KEY] = True
    fused[1].chunk.metadata[BOILERPLATE_KEY] = False

    shaped = _retriever(demote_boilerplate=True).shape(fused)
    assert _ids(shaped) == ["good", "also-good", "boiler"]


def test_demotion_classifies_unflagged_chunks_from_their_text() -> None:
    """The case the setting exists for: a corpus indexed before the flag existed
    still gets its licence footers and bibliographies pushed down."""
    licence = (
        "© The Author(s) 2026. This article is licensed under a Creative Commons "
        "Attribution 4.0 International License. To view a copy of this licence visit "
        "http://creativecommons.org/licenses/by/4.0/."
    )
    references = (
        "2. Aizen MA, Aguiar S, Biesmeijer JC, Garibaldi LA. Global agricultural "
        "productivity is threatened by increasing pollinator dependence. Glob Chang "
        "Biol. 2019;25(10):3516-3527. doi:10.1111/gcb.14736\n"
        "3. Klein AM, Vaissiere BE, Cane JH, Kremen C. Importance of pollinators in "
        "changing landscapes for world crops. Proc R Soc B. 2007;274:303-313.\n"
    )
    prose = (
        "Bees visited flowers of Brassica napus significantly more often than those of "
        "Trifolium pratense, and seed set increased with visitation rate up to an "
        "asymptote at roughly six visits per flower."
    )
    fused = _pool(("lic", "A"), ("refs", "A"), ("body", "B"))
    fused[0].chunk.content = licence
    fused[1].chunk.content = references
    fused[2].chunk.content = prose

    assert _ids(_retriever(demote_boilerplate=True).shape(fused)) == ["body", "lic", "refs"]


def test_demotion_off_by_default_is_a_no_op() -> None:
    fused = _pool(("boiler", "A"), ("good", "B"))
    fused[0].chunk.metadata[BOILERPLATE_KEY] = True
    assert _ids(_retriever().shape(fused)) == ["boiler", "good"]


def test_demotion_runs_before_the_cap() -> None:
    """Order matters: the cap must spend its per-document budget on real content,
    not on the boilerplate chunks that happened to rank higher."""
    fused = _pool(("a-boiler", "A"), ("a-good", "A"), ("a-good2", "A"), ("b1", "B"))
    fused[0].chunk.metadata[BOILERPLATE_KEY] = True
    for s in fused[1:]:
        s.chunk.metadata[BOILERPLATE_KEY] = False

    shaped = _retriever(max_per_doc=2, demote_boilerplate=True).shape(fused)
    assert _ids(shaped)[:3] == ["a-good", "a-good2", "b1"]
    assert _ids(shaped)[3] == "a-boiler"


# --- end-to-end through retrieve() -----------------------------------------
class _FakeStore:
    def __init__(self, chunks: list[Chunk], method: str) -> None:
        self._chunks, self._method = chunks, method

    async def search(self, _query, top_k=5, filters=None):  # noqa: ARG002
        return [
            ScoredChunk(chunk=c, score=1.0, retrieval_method=self._method)
            for c in self._chunks[:top_k]
        ]


class _FakeEmbedder:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_retrieve_applies_the_cap_and_still_returns_top_k() -> None:
    chunks = [Chunk(id=f"a{i}", doc_id="A", content=f"a{i}") for i in range(4)]
    chunks += [Chunk(id=f"b{i}", doc_id="B", content=f"b{i}") for i in range(4)]
    retriever = HybridRetriever(
        _FakeStore(chunks, "vector"),
        _FakeStore(chunks, "bm25"),
        _FakeEmbedder(),
        max_per_doc=2,
    )
    results = await retriever.retrieve("q", top_k=4)

    assert len(results) == 4
    docs = [r.chunk.doc_id for r in results]
    assert docs.count("A") == 2
    assert docs.count("B") == 2


# --- regression: the exact top-5 observed live ------------------------------
def test_reproduces_and_fixes_the_observed_bee_query_top_5() -> None:
    """The live failure, verbatim.

    "What is the role of bees?" returned an acknowledgements/BioProject
    fragment, a Creative Commons footer, a reference-list entry, a copyright
    line and a second reference line — three of them from the same document
    (``a08070be``) — all with flat ~0.016 RRF scores. With both knobs on, the
    real content that was sitting further down the pool is promoted instead.
    """
    observed = [
        ("ack", "a08070be", "Acknowledgements\nSequence data are deposited under "
                            "BioProject PRJNA987654. We thank the apiary staff."),
        ("cc", "a08070be", "If material is not included in the article's Creative "
                           "Commons licence and your intended use is not permitted by "
                           "statutory regulation or exceeds the permitted use, you will "
                           "need to obtain permission directly from the copyright holder."),
        ("ref1", "b1", "020.06.35.2.119  2. Aizen MA, Aguiar S, Biesmeijer JC, Garibaldi "
                       "LA, Roubik DW. Global agricultural productivity is threatened by "
                       "increasing pollinator dependence. Glob Chang Biol. "
                       "2019;25(10):3516-3527. doi:10.1111/gcb.14736\n"
                       "3. Klein AM, Vaissiere BE, Cane JH. Importance of pollinators in "
                       "changing landscapes. Proc R Soc B. 2007;274:303-313.\n"),
        ("copy", "a08070be", "creativecommons.org/licenses/by-nc-nd/4.0/ © The Author(s) "
                             "2026 Article https://doi.org/10.1038/s41586-026-01234-5"),
        ("ref2", "b1", "4. Potts SG, Biesmeijer JC, Kremen C, Neumann P, Schweiger O, "
                       "Kunin WE. Global pollinator declines: trends, impacts and drivers. "
                       "Trends Ecol Evol. 2010;25:345-353. doi:10.1016/j.tree.2010.01.007\n"
                       "5. Garibaldi LA, Steffan-Dewenter I, Winfree R. Wild pollinators "
                       "enhance fruit set. Science. 2013;339:1608-1611.\n"),
        # ...and the content that was sitting just below the cut.
        ("body1", "c1", "Bees are the dominant pollinators of most insect-pollinated crops, "
                        "and their role in this system is to transfer pollen between "
                        "flowers as they forage for nectar and pollen to provision brood."),
        ("body2", "a08070be", "Foraging honeybees visited flowers of Brassica napus far "
                              "more often than those of Trifolium pratense, and seed set "
                              "increased with visitation rate up to an asymptote."),
        ("body3", "d1", "The role of wild bees is complementary rather than redundant: "
                        "sites that retained a diverse wild bee community showed higher "
                        "and more stable seed set than sites dominated by managed hives."),
    ]
    fused = _pool(*[(cid, doc) for cid, doc, _ in observed])
    for scored, (_, _, text) in zip(fused, observed, strict=True):
        scored.chunk.content = text

    # Before: the top 5 is entirely boilerplate, 3 of it from one document.
    top5 = fused[:5]
    assert [s.chunk.id for s in top5] == ["ack", "cc", "ref1", "copy", "ref2"]
    assert sum(1 for s in top5 if s.chunk.doc_id == "a08070be") == 3

    shaped = _retriever(max_per_doc=2, demote_boilerplate=True).shape(fused)[:5]

    # After: real content first, and no document supplies more than two chunks.
    assert [s.chunk.id for s in shaped[:3]] == ["body1", "body2", "body3"]
    docs = [s.chunk.doc_id for s in shaped]
    assert max(docs.count(d) for d in set(docs)) <= 2
