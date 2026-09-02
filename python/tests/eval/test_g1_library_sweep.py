"""Unit tests for the G1 retrieval-parameter sweep harness.

Everything here is offline: sampling determinism and nesting, the production
depth arithmetic and its inverse, grid expansion, the chunk-level metric
expansion, per-leg accounting and full ``evaluate_cell`` parameter threading
(against fake stores), teardown, the scale banner, the two-stage statistics, and
the manifest shape. Only ``build_library_index``'s happy path needs the live
embedding fleet + Qdrant/ES; its *failure* path is tested here because that is
where stores leak.

The eval scripts live under ``python/scripts/eval`` and import each other as
siblings, so the directory goes on ``sys.path`` the same way the harnesses do.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import _stats  # noqa: E402
import chunking_compare_7way as c7  # noqa: E402
import g1_library_sweep as g1  # noqa: E402

from ragstack.models import Chunk, ScoredChunk  # noqa: E402
from ragstack.retrieval.retriever import HybridRetriever  # noqa: E402
from ragstack.scoring.scorers import RRFScorer  # noqa: E402

#: ``--qdrant-url``/``--es-url`` are REQUIRED (#476) — they used to default to the
#: ``chunking_compare_7way`` constants, which were the production addresses by
#: another name. Every ``parse_args`` call below supplies them, pointed at a dead
#: port so a test that somehow reaches a store fails loudly. Passed to the
#: negative cases too, so those keep failing for the reason they assert rather
#: than for a missing required argument.
DEAD_STORE = "http://127.0.0.1:1"
STORE_FLAGS = ("--qdrant-url", DEAD_STORE, "--es-url", DEAD_STORE)


# --------------------------------------------------------------------------- #
# Fixtures: a tiny labelled corpus
# --------------------------------------------------------------------------- #
def _corpus(n: int = 400) -> list[str]:
    return [f"d{i:04d}" for i in range(n)]


def _qrels(n_queries: int = 60, corpus: list[str] | None = None) -> dict[str, dict[str, int]]:
    corpus = corpus or _corpus()
    out: dict[str, dict[str, int]] = {}
    for q in range(n_queries):
        # 1-2 relevant docs per query, spread over the corpus, no overlap.
        rel = {corpus[(q * 3) % len(corpus)]: 1}
        if q % 4 == 0:
            rel[corpus[(q * 3 + 1) % len(corpus)]] = 2
        out[str(1000 + q)] = rel
    return out


# --------------------------------------------------------------------------- #
# Safety rails
# --------------------------------------------------------------------------- #
def test_guard_scratch_rejects_non_g1_names():
    assert g1.guard_scratch("g1_lib_50docs_abc") == "g1_lib_50docs_abc"
    for name in ("ragstack_sfr_tok512", "scifact_m7_x", "g2bench_a", "lib_g1_x"):
        with pytest.raises(SystemExit):
            g1.guard_scratch(name)


def test_library_collection_name_is_guarded_and_shaped():
    assert (
        g1.library_collection_name(200, "deadbeef", "0123456789abcdef")
        == "g1_lib_200docs_deadbeef_0123456789ab"
    )


def test_collection_name_separates_runs_at_different_seeds():
    """Two runs at different seeds must not resolve to the same scratch store.

    `ensure_collection` reuses an existing collection, so a shared name would
    silently merge two different corpora and every metric would describe the
    union of them."""
    corpus, qrels = _corpus(), _qrels()
    a = g1.sample_library(corpus, qrels, 100, seed=0)
    b = g1.sample_library(corpus, qrels, 100, seed=1)
    na = g1.library_collection_name(a.n_docs, "spec1234", a.digest)
    nb = g1.library_collection_name(b.n_docs, "spec1234", b.digest)
    assert na != nb
    assert na.startswith("g1_") and nb.startswith("g1_")


# --------------------------------------------------------------------------- #
# Sampling determinism and nesting
# --------------------------------------------------------------------------- #
def test_sample_library_is_deterministic():
    corpus, qrels = _corpus(), _qrels()
    a = g1.sample_library(corpus, qrels, 100, seed=0)
    b = g1.sample_library(list(reversed(corpus)), qrels, 100, seed=0)
    assert a == b  # input order must not matter — the sampler sorts first
    assert a.digest == b.digest


def test_sample_library_seed_changes_the_sample():
    corpus, qrels = _corpus(), _qrels()
    a = g1.sample_library(corpus, qrels, 100, seed=0)
    b = g1.sample_library(corpus, qrels, 100, seed=1)
    assert a.digest != b.digest


def test_sample_library_hits_the_requested_size():
    corpus, qrels = _corpus(), _qrels()
    for n in (50, 100, 200):
        s = g1.sample_library(corpus, qrels, n, seed=0)
        assert s.n_docs == n
        assert len(s.doc_ids) == n
        assert len(set(s.doc_ids)) == n
        assert set(s.judged_doc_ids).isdisjoint(s.distractor_doc_ids)


def test_every_retained_query_has_its_full_judged_set_present():
    """The drop-not-keep policy: a retained query is always satisfiable."""
    corpus, qrels = _corpus(), _qrels()
    s = g1.sample_library(corpus, qrels, 100, seed=0)
    present = set(s.doc_ids)
    for qid in s.query_ids:
        rel = {d for d, g in qrels[qid].items() if g > 0}
        assert rel and rel <= present, qid
    assert s.n_queries_dropped == s.n_queries_available - len(s.query_ids)


def test_query_and_distractor_sets_nest_across_sizes():
    """Cross-rung pairing is the ladder's whole justification, so assert it.

    The earlier sampler re-shuffled ``corpus - judged_set`` per rung, producing
    two unrelated permutations: 1/25 measured distractor overlap between n50 and
    n100 while the docstring claimed they nested. The library must nest — and
    the distractors must too, modulo those promoted to *judged* at the larger
    rung (still present, just relabelled)."""
    corpus, qrels = _corpus(), _qrels()
    s50 = g1.sample_library(corpus, qrels, 50, seed=0)
    s100 = g1.sample_library(corpus, qrels, 100, seed=0)
    s200 = g1.sample_library(corpus, qrels, 200, seed=0)

    # Queries: a strict prefix relationship.
    assert s50.query_ids == s100.query_ids[: len(s50.query_ids)]
    assert s100.query_ids == s200.query_ids[: len(s100.query_ids)]
    # Judged documents.
    assert set(s50.judged_doc_ids) <= set(s100.judged_doc_ids) <= set(s200.judged_doc_ids)
    # THE LIBRARY ITSELF nests — this is the property the pairing needs.
    assert set(s50.doc_ids) <= set(s100.doc_ids) <= set(s200.doc_ids)
    assert s50.nests_within(s100) and s100.nests_within(s200)
    # Distractors nest into the larger library (some become judged there).
    assert set(s50.distractor_doc_ids) <= set(s100.doc_ids)
    assert set(s100.distractor_doc_ids) <= set(s200.doc_ids)
    # And the great majority stay distractors — not one unrelated shuffle.
    kept = set(s50.distractor_doc_ids) & set(s100.distractor_doc_ids)
    assert len(kept) >= 0.8 * len(s50.distractor_doc_ids)


def test_nests_within_rejects_an_unrelated_sample():
    corpus, qrels = _corpus(), _qrels()
    s50 = g1.sample_library(corpus, qrels, 50, seed=0)
    other = g1.sample_library(corpus, qrels, 100, seed=7)
    assert not s50.nests_within(other)


def test_judged_fraction_is_respected():
    corpus, qrels = _corpus(), _qrels()
    s = g1.sample_library(corpus, qrels, 100, seed=0, judged_fraction=0.25)
    assert len(s.judged_doc_ids) <= 25 + 2  # the accepting query may straddle


def test_sample_library_rejects_nonsense_size():
    with pytest.raises(ValueError):
        g1.sample_library(_corpus(), _qrels(), 0)


# --------------------------------------------------------------------------- #
# Depth arithmetic — the production composition, and its inverse
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("top_k", "mult", "rr", "cand", "expected"),
    [
        (5, 2, False, 50, 10),    # shipping defaults, rerank off
        (5, 2, True, 50, 100),    # shipping defaults, rerank ON -> 10x breadth
        (5, 20, False, 50, 100),  # the designated alternative, rerank off
        (10, 1, False, 0, 10),
        (5, 2, True, 3, 10),      # rerank_candidates below top_k is a no-op
    ],
)
def test_leg_depth_matches_production_composition(top_k, mult, rr, cand, expected):
    assert g1.leg_depth_for(top_k, mult, rr, cand) == expected


def test_shippable_triples_invert_the_depth_composition():
    """Protocol §6.2: the deliverable states the triple, not D."""
    for t in g1.shippable_triples(100, False, None):
        assert g1.leg_depth_for(
            t["top_k"], t["candidate_multiplier"], False, 0
        ) == 100
    tk_mult = {
        (t["top_k"], t["candidate_multiplier"])
        for t in g1.shippable_triples(100, False, None)
    }
    assert (5, 20) in tk_mult      # §6.2's own worked example
    assert (10, 10) in tk_mult
    # The shipping default is a realization of D=10.
    assert (g1.DEFAULT_TOP_K, g1.DEFAULT_MULTIPLIER) in {
        (t["top_k"], t["candidate_multiplier"])
        for t in g1.shippable_triples(g1.DEFAULT_DEPTH, False, None)
    }


def test_shippable_triples_respect_the_rerank_coupling():
    """With rerank on, production's base is max(top_k, C) — so C drives D."""
    triples = g1.shippable_triples(100, True, 50)
    assert triples
    for t in triples:
        assert t["rerank_candidates"] == 50
        assert g1.leg_depth_for(
            t["top_k"], t["candidate_multiplier"], True, 50
        ) == 100


def test_shippable_triples_can_be_empty_for_an_unrealizable_depth():
    """A prime depth below every report cutoff has no integer multiplier."""
    assert g1.shippable_triples(7, False, None) == [
        {"top_k": 1, "candidate_multiplier": 7, "rerank_candidates": None}
    ]
    assert g1._fmt_triples([]) == "none"


# --------------------------------------------------------------------------- #
# Grid expansion
# --------------------------------------------------------------------------- #
def test_grid_sweeps_depth_not_the_aliased_product():
    """The regression this whole redesign exists for.

    ``top_k`` and ``candidate_multiplier`` reach the retriever only as a
    product, so a grid keyed on both emits byte-identical duplicate cells that
    enter the multiplicity family as guaranteed nulls."""
    grid = g1.build_grid([50], ["hybrid"], [60], [10, 20, 50], [False], [50])
    depths = [c.depth for c in grid]
    assert len(depths) == len(set(depths))
    assert set(depths) >= {10, 20, 50, g1.PRIMARY_DEPTH}
    assert all(hasattr(c, "depth") for c in grid)
    assert not any(hasattr(c, "multiplier") for c in grid)


def test_grid_collapses_rrf_k_outside_hybrid():
    grid = g1.build_grid([50], ["vector", "bm25"], [1, 60, 240], [10], [False], [50])
    assert {c.rrf_k for c in grid if c.mode in ("vector", "bm25")} == {None}
    # 2 modes x 1 depth x 1 rrf(None) + the 2 forced primary hybrid cells
    assert len([c for c in grid if c.mode != "hybrid"]) == 2


def test_grid_always_contains_the_designated_primary_pair():
    """Even when the CLI grid excludes them, the primary comparison must exist."""
    grid = g1.build_grid([50, 200], ["bm25"], [60], [37], [False], [50])
    for n in (50, 200):
        cells = [c for c in grid if c.n_docs == n]
        assert any(c.is_default for c in cells)
        assert any(c.is_primary_alt for c in cells)


def test_grid_cell_ids_are_unique_and_stable():
    grid = g1.build_grid([50], ["hybrid"], [10, 60], [10, 100], [False, True], [50])
    ids = [c.cell_id for c in grid]
    assert len(ids) == len(set(ids))
    assert "n50_hybrid_rrf60_d10_rr0" in ids
    assert "n50_hybrid_rrf60_d100_rr0" in ids
    assert "n50_hybrid_rrf60_d100_rr50" in ids


def test_grid_drops_rerank_candidates_deeper_than_the_pool():
    """Protocol §6.3b's offline C-derivation is faithful only while C <= D."""
    grid = g1.build_grid([50], ["hybrid"], [60], [10], [True], [10, 50, 100])
    for c in grid:
        if c.rerank_enabled:
            assert c.rerank_candidates is not None
            assert c.rerank_candidates <= c.depth


def test_grid_does_not_emit_rerank_candidates_when_rerank_is_off():
    grid = g1.build_grid([50], ["hybrid"], [60], [10], [False], [10, 50, 100])
    assert {c.rerank_candidates for c in grid} == {None}


def test_cell_params_carry_the_shippable_triples():
    cell = g1.Cell("n200", 200, "hybrid", 60, 100, False, None)
    p = cell.as_params()
    assert p["leg_depth"] == 100
    assert p["is_shipping_default"] is False
    assert {"top_k": 5, "candidate_multiplier": 20, "rerank_candidates": None} in (
        p["shippable_triples"]
    )
    assert "top_k" not in p and "candidate_multiplier" not in p


# --------------------------------------------------------------------------- #
# Metrics — chunk-level expansion
# --------------------------------------------------------------------------- #
def test_chunk_level_ndcg_reaches_1_on_a_perfect_ranking():
    """A relevant doc with several chunks must be able to fill the top ranks.

    Building the ideal DCG from the doc-level qrel row alone would cap a perfect
    chunk ranking below 1.0; the chunk-level expansion is what fixes it.
    """
    doc_rels = {"dA": 1}
    chunk_rels = {f"dA#{i}": 1 for i in range(6)}
    ranked_chunks = [f"dA#{i}" for i in range(6)]
    ranked_docs = ["dA"] * 6
    m = g1.query_metrics(ranked_chunks, ranked_docs, doc_rels, chunk_rels)
    assert m[g1.CO_PRIMARY_METRIC] == pytest.approx(1.0)


def test_doc_collapse_and_context_precision():
    doc_rels = {"dA": 1, "dB": 1}
    chunk_rels = {"dA#0": 1, "dA#1": 1, "dB#0": 1}
    ranked_chunks = ["dA#0", "dA#1", "dB#0", "dC#0", "dD#0"]
    ranked_docs = ["dA", "dA", "dB", "dC", "dD"]
    m = g1.query_metrics(ranked_chunks, ranked_docs, doc_rels, chunk_rels)
    assert m["unique_docs@5"] == 4.0        # 5 chunks collapse to 4 docs
    assert m["ctxprec@3"] == pytest.approx(1.0)
    assert m["ctxprec@5"] == pytest.approx(3 / 5)
    assert m["recall@10"] == pytest.approx(1.0)
    assert m["mrr@10"] == pytest.approx(1.0)
    assert m[g1.PRIMARY_METRIC] == pytest.approx(1.0)


def test_metric_names_cover_everything_query_metrics_emits():
    m = g1.query_metrics(["c1"], ["dA"], {"dA": 1}, {"c1": 1})
    assert set(m) == set(g1.METRIC_NAMES)


# --------------------------------------------------------------------------- #
# Per-leg instrumentation
# --------------------------------------------------------------------------- #
def _sc(cid: str, doc: str, score: float, method: str) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(id=cid, doc_id=doc, content=cid), score=score,
        retrieval_method=method,
    )


class _FakeVectorStore:
    def __init__(self, hits: list[ScoredChunk]) -> None:
        self.hits = hits
        self.last_top_k: int | None = None

    async def search(self, query_vector, top_k=5, filters=None):  # noqa: ARG002
        self.last_top_k = top_k
        return self.hits[:top_k]


class _FakeTextIndex:
    def __init__(self, hits: list[ScoredChunk]) -> None:
        self.hits = hits
        self.last_top_k: int | None = None

    async def search(self, query, top_k=5, filters=None):  # noqa: ARG002
        self.last_top_k = top_k
        return self.hits[:top_k]


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[0.1, 0.2, 0.3] for _ in texts]


def _legs():
    dense = [_sc(f"c{i}", f"d{i}", 1.0 - i / 100, "vector") for i in range(20)]
    # BM25 overlaps on c0..c4 and brings c100.. of its own.
    bm25 = [_sc(f"c{i}", f"d{i}", 5.0 - i, "bm25") for i in range(5)] + [
        _sc(f"c{100+i}", f"d{100+i}", 3.0 - i, "bm25") for i in range(15)
    ]
    return dense, bm25


@pytest.mark.asyncio
async def test_instrumented_matches_production_retrieve_exactly():
    """The eval subclass must not fork retrieval behaviour."""
    dense, bm25 = _legs()
    for mode in ("hybrid", "vector", "bm25"):
        prod = HybridRetriever(
            _FakeVectorStore(dense), _FakeTextIndex(bm25), _FakeEmbedder(),
            rrf_scorer=RRFScorer(k=17), candidate_multiplier=3,
        )
        inst = g1.InstrumentedHybridRetriever(
            _FakeVectorStore(dense), _FakeTextIndex(bm25), _FakeEmbedder(),
            rrf_scorer=RRFScorer(k=17), candidate_multiplier=3,
        )
        expected = await prod.retrieve("q", top_k=5, use_graph=False, mode=mode)
        fused, _ = await inst.retrieve_instrumented("q", top_k=5, mode=mode)
        got = fused[:5]
        assert [s.chunk.id for s in got] == [s.chunk.id for s in expected], mode
        assert [s.score for s in got] == [s.score for s in expected], mode


@pytest.mark.asyncio
async def test_leg_stats_account_for_both_legs():
    dense, bm25 = _legs()
    vs, ti = _FakeVectorStore(dense), _FakeTextIndex(bm25)
    inst = g1.InstrumentedHybridRetriever(vs, ti, _FakeEmbedder(),
                                          candidate_multiplier=2)
    fused, legs = await inst.retrieve_instrumented("q", top_k=5, mode="hybrid")
    assert legs.requested_depth == 10
    assert vs.last_top_k == 10 and ti.last_top_k == 10
    assert legs.dense_hits == 10 and legs.bm25_hits == 10
    assert legs.dense_deficit == 0 and legs.bm25_deficit == 0
    assert legs.overlap == 5              # c0..c4 in both legs
    assert legs.union_depth == 15         # 10 + 10 - 5
    assert legs.fused_depth == len(fused) == 15


@pytest.mark.asyncio
async def test_leg_stats_report_a_short_leg_as_a_deficit():
    """A short leg degrades the fusion toward single-leg behaviour with no signal
    at any API layer — this counter is the only place it becomes visible."""
    dense, bm25 = _legs()
    inst = g1.InstrumentedHybridRetriever(
        _FakeVectorStore(dense[:3]), _FakeTextIndex(bm25), _FakeEmbedder(),
        candidate_multiplier=2,
    )
    _, legs = await inst.retrieve_instrumented("q", top_k=5, mode="hybrid")
    assert legs.dense_hits == 3
    assert legs.dense_deficit == 7
    assert legs.bm25_deficit == 0


@pytest.mark.asyncio
async def test_single_leg_modes_zero_the_other_legs_deficit():
    dense, bm25 = _legs()
    inst = g1.InstrumentedHybridRetriever(
        _FakeVectorStore(dense), _FakeTextIndex(bm25), _FakeEmbedder(),
        candidate_multiplier=2,
    )
    _, v = await inst.retrieve_instrumented("q", top_k=5, mode="vector")
    assert v.bm25_hits == 0 and v.bm25_deficit == 0 and v.overlap == 0
    _, b = await inst.retrieve_instrumented("q", top_k=5, mode="bm25")
    assert b.dense_hits == 0 and b.dense_deficit == 0


@pytest.mark.asyncio
async def test_cached_query_vector_skips_the_embedding_fleet():
    dense, bm25 = _legs()
    emb = _FakeEmbedder()
    inst = g1.InstrumentedHybridRetriever(_FakeVectorStore(dense),
                                          _FakeTextIndex(bm25), emb)
    await inst.retrieve_instrumented("q", top_k=5, mode="hybrid",
                                     query_vector=[0.0, 1.0, 0.0])
    assert emb.calls == 0
    await inst.retrieve_instrumented("q", top_k=5, mode="hybrid")
    assert emb.calls == 1


@pytest.mark.asyncio
async def test_cached_query_embedder_raises_on_a_miss():
    emb = g1.CachedQueryEmbedder({"hello": [1.0]})
    assert await emb.embed(["hello"]) == [[1.0]]
    with pytest.raises(KeyError):
        await emb.embed(["nope"])


# --------------------------------------------------------------------------- #
# evaluate_cell — the one place every swept parameter is threaded
# --------------------------------------------------------------------------- #
def _fake_library_index(rung: str = "n50", n_docs: int = 50, **kw) -> g1.LibraryIndex:
    from ragstack import provenance

    sample = g1.LibrarySample(
        n_docs=n_docs, seed=0, judged_fraction=0.5,
        doc_ids=tuple(f"d{i}" for i in range(n_docs)),
        judged_doc_ids=("d0", "d1"),
        distractor_doc_ids=tuple(f"d{i}" for i in range(2, n_docs)),
        query_ids=("q1", "q2"),
        n_queries_available=2, n_queries_dropped=0, digest="deadbeef" * 8,
    )
    manifest = provenance.make_ingest_manifest(
        collection="g1_lib_test", model="m", dim=8, embedding_api="openai",
        embedding_endpoints=[], chunk_method="fixed_tok512", chunk_size=512,
        chunk_overlap=64, chunk_params=None, corpus="test", chunk_count=1,
        ragstack_version="0", source="ingest",
    )
    defaults = {
        "sample": sample, "collection": "g1_lib_test", "es_index": "g1_lib_test",
        "spec_hash": "abcd1234", "n_chunks": 500, "chunks_per_doc": 10.0,
        "doc_to_chunk_ids": {f"d{i}": [f"c{i}"] for i in range(n_docs)},
        "manifest": manifest, "build_s": 0.1,
        "dense_matchable": 500,
        "bm25_matchable": {"q1": 500, "q2": 500},
        "hnsw": {"hnsw_built": True, "indexed_vectors": 500,
                 "indexing_threshold": 100},
    }
    defaults.update(kw)
    idx = g1.LibraryIndex(**defaults)  # type: ignore[arg-type]
    idx.sample = g1.LibrarySample(**{**sample.__dict__, "n_docs": n_docs})
    return idx


class _FakeReranker:
    """Ranks by chunk id descending — enough to prove the wiring is live."""

    def __init__(self) -> None:
        self.calls = 0

    async def score(self, query, candidates, top_k=None):  # noqa: ARG002
        self.calls += 1
        return [
            ScoredChunk(chunk=c, score=float(len(candidates) - i),
                        retrieval_method="reranked")
            for i, c in enumerate(reversed(candidates))
        ]


def _rrf_sensitive_legs():
    """Two legs whose fused order genuinely depends on rrf_k.

    ``P`` sits at dense rank 0 only: RRF score ``1/(k+1)``.
    ``Q`` sits at rank 3 in *both* legs: ``2/(k+4)``.
    At k=1 that is 0.500 vs 0.400 (P first); at k=60 it is 0.0164 vs 0.0313
    (Q first). So a cell that fails to thread ``rrf_k`` cannot pass this."""
    dense = [
        _sc("P", "dP", 0.9, "vector"),
        _sc("A", "dA", 0.8, "vector"),
        _sc("B", "dB", 0.7, "vector"),
        _sc("Q", "dQ", 0.6, "vector"),
    ]
    bm25 = [
        _sc("X", "dX", 9.0, "bm25"),
        _sc("Y", "dY", 8.0, "bm25"),
        _sc("Z", "dZ", 7.0, "bm25"),
        _sc("Q", "dQ", 6.0, "bm25"),
    ]
    return dense, bm25


async def _run_cell(cell, index=None, reranker=None, dense=None, bm25=None):
    dense = dense if dense is not None else _rrf_sensitive_legs()[0]
    bm25 = bm25 if bm25 is not None else _rrf_sensitive_legs()[1]
    stores: dict[str, object] = {}

    def factory(_index):
        vs, ti = _FakeVectorStore(dense), _FakeTextIndex(bm25)
        stores["vs"], stores["ti"] = vs, ti
        return vs, ti

    index = index or _fake_library_index()
    qrels = {"q1": {"dQ": 1}, "q2": {"dP": 1}}
    queries = {"q1": "query one", "q2": "query two"}
    qvectors = {"query one": [0.1] * 8, "query two": [0.2] * 8}
    res = await g1.evaluate_cell(
        cell, index, queries, qrels, qvectors,
        reranker or _FakeReranker(), {}, concurrency=2, store_factory=factory,
    )
    return res, stores


@pytest.mark.asyncio
async def test_evaluate_cell_threads_rrf_k_into_the_ranking():
    """Two cells differing ONLY in rrf_k must produce different rankings.

    Deleting the ``rrf_scorer=RRFScorer(k=cell.rrf_k)`` wiring in
    ``evaluate_cell`` makes both cells fall back to ``RRFScorer()``'s k=60 and
    this test fails — which is the point: nothing else in the suite noticed."""
    low = g1.Cell("n50", 50, "hybrid", 1, 10, False, None)
    high = g1.Cell("n50", 50, "hybrid", 60, 10, False, None)
    res_low, _ = await _run_cell(low)
    res_high, _ = await _run_cell(high)
    assert res_low["rankings"]["q1"] != res_high["rankings"]["q1"]
    # ... and specifically: P outranks Q at k=1, Q outranks P at k=60.
    lo, hi = res_low["rankings"]["q1"], res_high["rankings"]["q1"]
    assert lo.index("P") < lo.index("Q")
    assert hi.index("Q") < hi.index("P")


@pytest.mark.asyncio
async def test_evaluate_cell_threads_depth_into_both_legs():
    """Two cells differing only in D must request different per-leg depths."""
    seen = []
    for depth in (10, 100):
        cell = g1.Cell("n50", 50, "hybrid", 60, depth, False, None)
        res, stores = await _run_cell(cell)
        seen.append((stores["vs"].last_top_k, stores["ti"].last_top_k))
        assert res["params"]["leg_depth"] == depth
        assert all(c["requested_depth"] == depth for c in res["counters"])
    assert seen == [(10, 10), (100, 100)]


@pytest.mark.asyncio
async def test_evaluate_cell_threads_mode_into_the_legs():
    for mode, dense_expected, bm25_expected in (
        ("hybrid", True, True), ("vector", True, False), ("bm25", False, True)
    ):
        cell = g1.Cell("n50", 50, mode, 60 if mode == "hybrid" else None,
                       10, False, None)
        res, stores = await _run_cell(cell)
        assert (stores["vs"].last_top_k is not None) is dense_expected, mode
        assert (stores["ti"].last_top_k is not None) is bm25_expected, mode


@pytest.mark.asyncio
async def test_evaluate_cell_threads_the_reranker():
    off = g1.Cell("n50", 50, "hybrid", 60, 10, False, None)
    on = g1.Cell("n50", 50, "hybrid", 60, 10, True, 4)
    rr_off, rr_on = _FakeReranker(), _FakeReranker()
    res_off, _ = await _run_cell(off, reranker=rr_off)
    res_on, _ = await _run_cell(on, reranker=rr_on)
    assert rr_off.calls == 0
    assert rr_on.calls > 0
    assert res_off["rankings"]["q1"] != res_on["rankings"]["q1"]
    assert all(
        c["rerank_pool_occupancy"] is not None for c in res_on["counters"]
    )


@pytest.mark.asyncio
async def test_evaluate_cell_records_the_measurement_regime():
    """Every cell record carries the regime, machine-readable (finding 4)."""
    cell = g1.Cell("n50", 50, "hybrid", 60, 10, False, None)
    res, _ = await _run_cell(cell)
    assert res["regime"]["scale_regime"] == g1.REGIME_HNSW
    assert res["regime"]["hnsw_built"] is True
    assert res["regime"]["chunks_per_doc"] == 10.0

    unbuilt = _fake_library_index(hnsw={"hnsw_built": False, "indexed_vectors": 0})
    res2, _ = await _run_cell(cell, index=unbuilt)
    assert res2["regime"]["scale_regime"] == g1.REGIME_BRUTE_FORCE
    assert res2["regime"]["hnsw_built"] is False

    errored = _fake_library_index(hnsw={"error": "ConnectError: refused"})
    res3, _ = await _run_cell(cell, index=errored)
    assert res3["regime"]["scale_regime"] == g1.REGIME_UNKNOWN
    assert res3["regime"]["hnsw_built"] is None


# --------------------------------------------------------------------------- #
# The §5.4 sanity assertion
# --------------------------------------------------------------------------- #
def _counter(dense_hits, bm25_hits, d, dense_matchable, bm25_matchable):
    return {
        "dense_hits": dense_hits,
        "bm25_hits": bm25_hits,
        "dense_deficit": max(0, d - dense_hits),
        "bm25_deficit": max(0, d - bm25_hits),
        "dense_unreturned": max(0, min(d, dense_matchable) - dense_hits),
        "bm25_unreturned": max(0, min(d, bm25_matchable) - bm25_hits),
    }


def test_full_legs_pass():
    c = [_counter(10, 10, 10, 5000, 900) for _ in range(50)]
    s = g1.sanity_verdict(c, 10, 5000)
    assert s["verdict"] == "PASS"
    assert s["dense_starved_rate"] == 0.0 and s["bm25_starved_rate"] == 0.0


def test_a_bm25_leg_short_because_the_index_has_no_more_matches_is_not_a_failure():
    """H1b: 'BM25 returned 4' is a measurement, not a truncation bug."""
    c = [_counter(10, 4, 10, 5000, 4) for _ in range(50)]
    s = g1.sanity_verdict(c, 10, 5000)
    assert s["verdict"] == "PASS"          # 4 == min(10, matchable=4)
    assert s["bm25_starved_rate"] == 1.0   # but every query IS depth-starved
    assert s["bm25_deficit_rate"] == 0.0


def test_a_dense_leg_short_of_its_ceiling_voids_the_cell():
    """The registered prediction: the DENSE leg is the one that can truncate."""
    c = [_counter(7, 10, 10, 5000, 900) for _ in range(50)]
    s = g1.sanity_verdict(c, 10, 5000)
    assert s["verdict"] == "INVALID (hit deficit)"
    assert s["dense_deficit_rate"] == 1.0


def test_a_deficit_below_the_threshold_still_passes():
    c = [_counter(10, 10, 10, 5000, 900) for _ in range(200)]
    c[0] = _counter(9, 10, 10, 5000, 900)     # 0.5% < MAX_DEFICIT_RATE
    assert g1.sanity_verdict(c, 10, 5000)["verdict"] == "PASS"
    c[1] = _counter(9, 10, 10, 5000, 900)     # 1.0% == threshold, still PASS
    assert g1.sanity_verdict(c, 10, 5000)["verdict"] == "PASS"
    c[2] = _counter(9, 10, 10, 5000, 900)     # 1.5% > threshold
    assert g1.sanity_verdict(c, 10, 5000)["verdict"] == "INVALID (hit deficit)"


def test_a_leg_capped_by_a_tiny_index_is_not_a_deficit():
    """A 50-doc library has fewer chunks than a depth-100 request asks for."""
    c = [_counter(61, 40, 100, 61, 40) for _ in range(20)]
    s = g1.sanity_verdict(c, 100, 61)
    assert s["verdict"] == "PASS"
    assert s["dense_starved_rate"] == 1.0


# --------------------------------------------------------------------------- #
# Build-failure path — stores must not outlive a crash
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_build_failure_still_registers_the_store_for_teardown(monkeypatch):
    """The collection is created before the upsert loop runs; if anything after
    the name is known raises, the name must already be in the teardown ledger."""
    created: list[str] = []
    sample = _fake_library_index().sample

    async def _boom(collection, es_index):  # noqa: ARG001
        raise RuntimeError("qdrant is on fire")

    monkeypatch.setattr(g1, "assert_store_absent_or_empty", _boom)
    with pytest.raises(RuntimeError):
        await g1.build_library_index(sample, {}, None, created=created)
    assert len(created) == 1
    assert created[0].startswith("g1_lib_")
    assert sample.digest[:12] in created[0]


@pytest.mark.asyncio
async def test_a_chunking_failure_also_leaves_the_store_registered(monkeypatch):
    created: list[str] = []
    sample = _fake_library_index().sample

    async def _ok(collection, es_index):  # noqa: ARG001
        return None

    def _boom(cfg, docs):  # noqa: ARG001
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(g1, "assert_store_absent_or_empty", _ok)
    monkeypatch.setattr(g1.c7, "chunk_docs_for_config", _boom)
    with pytest.raises(RuntimeError):
        await g1.build_library_index(
            sample, {d: object() for d in sample.doc_ids}, None, created=created
        )
    assert len(created) == 1


# --------------------------------------------------------------------------- #
# Teardown
# --------------------------------------------------------------------------- #
class _FakeQdrantClient:
    def __init__(self, *a, **kw) -> None:  # noqa: ARG002
        self.dropped: list[str] = []
        self.remaining: list[str] = []

    async def delete_collection(self, collection_name):
        self.dropped.append(collection_name)

    async def get_collections(self):
        class _C:
            def __init__(self, name): self.name = name

        class _R:
            def __init__(self, names): self.collections = [_C(n) for n in names]

        return _R(self.remaining)

    async def close(self):
        return None


class _FakeHttp:
    def __init__(self, cat_text: str = "") -> None:
        self.deleted: list[str] = []
        self.cat_text = cat_text

    async def delete(self, url, timeout=None):  # noqa: ARG002
        self.deleted.append(url)

        class _R:
            status_code = 200

        return _R()

    async def get(self, url, timeout=None):  # noqa: ARG002
        class _R:
            pass

        r = _R()
        r.text = self.cat_text
        return r


@pytest.fixture()
def _fake_qdrant(monkeypatch):
    import qdrant_client

    # The store target ``main`` would have set from --qdrant-url/--es-url. Without
    # it ``c7.store_urls()`` refuses (#476) — and since teardown resolves the URL
    # before it checks the name, every test here would otherwise pass on the wrong
    # SystemExit, including the one asserting the g1_ name guard.
    monkeypatch.setattr(c7, "QDRANT_URL", DEAD_STORE, raising=False)
    monkeypatch.setattr(c7, "ES_URL", DEAD_STORE, raising=False)

    holder: dict[str, _FakeQdrantClient] = {}

    def _factory(*a, **kw):
        c = _FakeQdrantClient(*a, **kw)
        holder["client"] = c
        return c

    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", _factory)
    return holder


@pytest.mark.asyncio
async def test_teardown_drops_both_stores_and_verifies(_fake_qdrant):
    http = _FakeHttp(cat_text="")
    gone = await g1.teardown(http, ["g1_lib_a", "g1_lib_b"])
    assert gone is True
    assert _fake_qdrant["client"].dropped == ["g1_lib_a", "g1_lib_b"]
    assert [u.rsplit("/", 1)[-1] for u in http.deleted] == ["g1_lib_a", "g1_lib_b"]


@pytest.mark.asyncio
async def test_teardown_reports_leftovers_rather_than_claiming_success(_fake_qdrant):
    http = _FakeHttp(cat_text="g1_lib_a\n")

    def _factory(*a, **kw):
        c = _FakeQdrantClient(*a, **kw)
        c.remaining = ["g1_lib_a"]
        _fake_qdrant["client"] = c
        return c

    import qdrant_client
    qdrant_client.AsyncQdrantClient = _factory  # type: ignore[assignment]
    assert await g1.teardown(http, ["g1_lib_a"]) is False


@pytest.mark.asyncio
async def test_teardown_refuses_a_non_g1_name(_fake_qdrant):
    """Production Qdrant is on the same host — the guard must hold at teardown."""
    with pytest.raises(SystemExit):
        await g1.teardown(_FakeHttp(), ["ragstack_sfr_tok512"])


@pytest.mark.asyncio
async def test_teardown_is_a_noop_with_nothing_to_drop():
    assert await g1.teardown(_FakeHttp(), []) is True


# --------------------------------------------------------------------------- #
# The scale banner — three states, never two
# --------------------------------------------------------------------------- #
def _lib(rung_n_docs: int, hnsw: dict | None) -> dict:
    return {
        "n_docs": rung_n_docs, "n_chunks": rung_n_docs * 2,
        "chunks_per_doc": 1.2, "hnsw": hnsw,
    }


def test_scale_banner_reports_unbuilt_hnsw():
    libs = {"n50": _lib(50, {"hnsw_built": False}),
            "n200": _lib(200, {"hnsw_built": False})}
    b = g1._scale_banner(libs)
    assert "HNSW was never built" in b
    assert "n50" in b and "n200" in b
    assert "HNSW was built at every rung" not in b


def test_scale_banner_refuses_to_claim_hnsw_when_telemetry_errored():
    """The bug this test exists for: `qdrant_index_info` returns
    ``{"error": ...}`` with no ``hnsw_built`` key, the rung was skipped, and the
    else-branch printed 'HNSW was built at every rung.' — an affirmative lie."""
    libs = {"n50": _lib(50, {"error": "ConnectError: connection refused"})}
    b = g1._scale_banner(libs)
    assert "HNSW status UNKNOWN — do not quote" in b
    assert "HNSW was built at every rung" not in b
    assert "HNSW was never built" not in b


def test_scale_banner_handles_a_missing_hnsw_key():
    assert "UNKNOWN" in g1._scale_banner({"n50": _lib(50, None)})
    assert "UNKNOWN" in g1._scale_banner({"n50": _lib(50, {})})


def test_scale_banner_says_built_only_when_every_rung_is_built():
    libs = {"n50": _lib(50, {"hnsw_built": True})}
    assert "HNSW was built at every rung" in g1._scale_banner(libs)
    mixed = {"n50": _lib(50, {"hnsw_built": True}),
             "n200": _lib(200, {"error": "boom"})}
    b = g1._scale_banner(mixed)
    assert "HNSW was built at every rung" not in b
    assert "UNKNOWN" in b
    assert "built at rung(s) n50" in b


def test_scale_regime_helpers_are_three_valued():
    assert g1.scale_regime_for({"hnsw_built": True}) == g1.REGIME_HNSW
    assert g1.scale_regime_for({"hnsw_built": False}) == g1.REGIME_BRUTE_FORCE
    assert g1.scale_regime_for({"error": "x"}) == g1.REGIME_UNKNOWN
    assert g1.scale_regime_for(None) == g1.REGIME_UNKNOWN
    assert g1.hnsw_state({"error": "x"}) is None


# --------------------------------------------------------------------------- #
# Manifest shape
# --------------------------------------------------------------------------- #
def _fake_docs():
    class _D:
        def __init__(self, i):
            self.id = f"d{i}"
            self.content = f"content {i}"

    return [_D(i) for i in range(5)]


def test_dataset_provenance_digests_content_not_paths():
    docs = _fake_docs()
    queries = {"1": "a claim"}
    qrels = {"1": {"d0": 1}}
    a = g1.dataset_provenance(docs, queries, qrels, "hf:BeIR/scifact")
    b = g1.dataset_provenance(list(reversed(docs)), queries, qrels, "hf:BeIR/scifact")
    assert a == b  # order-independent
    docs[0].content = "tampered"
    c = g1.dataset_provenance(docs, queries, qrels, "hf:BeIR/scifact")
    assert c["corpus_sha256"] != a["corpus_sha256"]
    assert a["n_docs"] == 5 and a["n_queries"] == 1 and a["n_judgments"] == 1


def _manifest(**kw):
    args = g1.parse_args([*STORE_FLAGS, "--doc-counts", "50", "--smoke"])
    grid = g1.build_grid([50], ["hybrid"], [60], [10], [False], [50])
    defaults = {
        "run_id": "g1-test",
        "argv": ["g1_library_sweep.py", "--smoke"],
        "args": args,
        "dataset": g1.dataset_provenance(
            _fake_docs(), {"1": "q"}, {"1": {"d0": 1}}, "src"
        ),
        "indexes": {},
        "grid": grid,
        "started_at": "2026-01-01T00:00:00+00:00",
        "qvec_cache": {"path": "x", "sha256": "sha256:abc", "hit": False},
        "reranker": {"url": "http://x", "model": "BAAI/bge-reranker-v2-m3",
                     "revision": "abc123"},
    }
    defaults.update(kw)
    return g1.build_run_manifest(**defaults)


def test_run_manifest_carries_every_required_provenance_section():
    m = _manifest()
    assert m["schema_version"] == "ragstack.eval_run/v1"
    for key in ("run_id", "protocol_version", "git", "ragstack_version", "dataset",
                "build_spec", "libraries", "grid", "runtime", "seeds", "argv", "cwd",
                "reranker", "query_vector_cache"):
        assert key in m, key
    assert m["build_spec"]["spec_hash"] and len(m["build_spec"]["spec_hash"]) == 8
    assert m["build_spec"]["chunk_config"] == g1.CHUNK_CONFIG_KEY
    assert m["grid"]["primary_comparison"]["metric"] == g1.PRIMARY_METRIC
    assert m["seeds"]["bootstrap"] == 0
    assert m["seeds"]["query_split"] == g1.SPLIT_SEED
    assert set(m["runtime"]["packages"]) >= {"qdrant-client", "elasticsearch", "numpy"}
    assert m["argv"] == ["g1_library_sweep.py", "--smoke"]


def test_manifest_does_not_claim_the_primary_is_preregistered():
    """It was committed with the harness, after the protocol was hashed."""
    pc = _manifest()["grid"]["primary_comparison"]
    assert pc["preregistered"] is False
    assert "designated" in pc["designation"].lower()
    assert "A4" in pc["designation"]


def test_manifest_records_the_reranker_model_and_revision():
    r = _manifest()["reranker"]
    assert r["model"] == "BAAI/bge-reranker-v2-m3"
    assert r["revision"] == "abc123"


def test_manifest_records_the_query_vector_cache_digest():
    c = _manifest()["query_vector_cache"]
    assert c["sha256"].startswith("sha256:")
    assert c["hit"] is False


def test_git_info_identifies_a_dirty_tree_rather_than_flagging_it():
    g = g1._git_info()
    assert set(g) >= {"commit", "branch", "dirty", "dirty_digest", "dirty_files"}
    if g["dirty"]:
        assert g["dirty_digest"].startswith("sha256:")
        assert isinstance(g["dirty_files"], list)
    else:
        assert g["dirty_digest"] is None


def test_manifest_records_the_swept_factor_and_the_shippable_mapping():
    m = _manifest()
    assert "depth" in m["grid"]["swept_factor"].lower()
    mapping = m["grid"]["shippable_triples_by_depth"]
    assert set(mapping) >= {"10", "100"}
    assert any(t["top_k"] == 5 and t["candidate_multiplier"] == 20
               for t in mapping["100"])


def test_build_spec_matches_the_collection_name_hash():
    desc, spec_hash, _ = g1.build_spec()
    assert desc.startswith("fixed_tok512/512/64")
    assert spec_hash in g1.library_collection_name(200, spec_hash, "abcdef123456")


# --------------------------------------------------------------------------- #
# Statistics wiring
# --------------------------------------------------------------------------- #
def _cell_result(
    cell: g1.Cell,
    values: list[float],
    verdict: str = "PASS",
    query_ids: list[str] | None = None,
    co_values: list[float] | None = None,
    p95: float = 2.0,
) -> dict:
    n = len(values)
    per_query = {m: list(values) for m in g1.METRIC_NAMES}
    if co_values is not None:
        per_query[g1.CO_PRIMARY_METRIC] = list(co_values)
    return {
        "cell_id": cell.cell_id,
        "params": cell.as_params(),
        "n_queries": n,
        "query_ids": query_ids or [str(i) for i in range(n)],
        "per_query": per_query,
        "means": {m: sum(v) / n for m, v in per_query.items()},
        "counters": [],
        "rankings": {},
        "regime": {"scale_regime": g1.REGIME_HNSW, "hnsw_built": True,
                   "chunks_per_doc": 1.2, "n_chunks": 240,
                   "indexed_vectors": 240, "indexing_threshold": 100},
        "sanity": {"verdict": verdict, "dense_deficit_rate": 0.0,
                   "bm25_deficit_rate": 0.0, "dense_starved_rate": 0.0,
                   "bm25_starved_rate": 0.0, "leg_depth": 10,
                   "dense_matchable": 1000,
                   "assertion": "hits == min(D, matchable) per leg"},
        "cost": {"p50_query_ms": 1.0, "p95_query_ms": p95, "mean_dense_ms": 1.0,
                 "mean_bm25_ms": 1.0, "mean_union_depth": 15.0, "mean_overlap": 5.0},
    }


def _default_cell(rung: str = "n200", n: int = 200) -> g1.Cell:
    return g1.Cell(rung, n, "hybrid", 60, g1.DEFAULT_DEPTH, False, None)


def _alt_cell(rung: str = "n200", n: int = 200) -> g1.Cell:
    return g1.Cell(rung, n, "hybrid", 60, g1.PRIMARY_DEPTH, False, None)


def _noise(seed: int, n: int, scale: float = 0.05) -> list[float]:
    import random as _r
    rng = _r.Random(seed)
    return [rng.uniform(-scale, scale) for _ in range(n)]


def test_primary_comparison_detects_a_real_improvement():
    n = 60
    base = [0.30 + 0.001 * i + e for i, e in enumerate(_noise(1, n))]
    better = [b + 0.08 + e for b, e in zip(base, _noise(2, n, 0.01), strict=True)]
    out = g1.primary_comparison(
        [_cell_result(_default_cell(), base), _cell_result(_alt_cell(), better)],
        iters=500,
    )
    assert out["verdict"] == "DIFFERENT"
    assert out["valid"] is True
    assert out["n_docs"] == 200
    assert out["preregistered"] is False


def test_primary_comparison_declares_equivalence_only_on_real_paired_noise():
    """The old version of this test used a constant offset — zero variance, so
    the bootstrap CI collapsed to a point and TOST fired for the wrong reason.
    Equivalence must be earned against realistic per-query dispersion."""
    n = 120
    base = [0.55 + e for e in _noise(3, n, 0.12)]
    # Same configuration up to per-query jitter well inside delta.
    same = [b + e for b, e in zip(base, _noise(4, n, 0.004), strict=True)]
    out = g1.primary_comparison(
        [_cell_result(_default_cell(), base), _cell_result(_alt_cell(), same)],
        iters=2000,
    )
    assert out["verdict"] == "EQUIVALENT"
    assert out["n_discriminating"] == n     # every query genuinely discriminates
    assert out["diff_ci90"]["lo"] != out["diff_ci90"]["hi"]


def test_equivalence_is_not_reachable_through_degeneracy():
    """Identical per-query scores give a zero-variance paired bootstrap: the CI
    is exactly [0, 0] and TOST would fire at ANY n. That is the absence of a
    measurement, not equivalence."""
    vals = [0.3 + 0.001 * i for i in range(80)]
    out = g1.primary_comparison(
        [_cell_result(_default_cell(), vals), _cell_result(_alt_cell(), vals)],
        iters=300,
    )
    assert out["n_discriminating"] == 0
    assert out["verdict"] == "INCONCLUSIVE"
    assert "degenerate" in out["verdict_reason"]
    # The interval really did collapse — the guard is what saved us.
    assert out["diff_ci90"]["lo"] == out["diff_ci90"]["hi"] == 0.0


def test_a_constant_offset_is_also_degenerate():
    """The subtler route to a fake EQUIVALENT — and the one the OLD test used.

    A constant shift makes every paired difference non-zero, so the
    discriminating-query floor does not fire; yet the difference distribution
    still has zero variance, every bootstrap resample returns the same mean, and
    the interval collapses to a point. Reporting that as 'genuinely no practical
    difference' (§7.5) would be a false claim, so it is INCONCLUSIVE too."""
    base = [0.30 + 0.001 * i for i in range(60)]
    shifted = [b + 0.0005 for b in base]
    out = g1.primary_comparison(
        [_cell_result(_default_cell(), base), _cell_result(_alt_cell(), shifted)],
        iters=300,
    )
    assert out["n_discriminating"] == 60          # the floor does NOT fire
    assert out["diff_ci90"]["lo"] == pytest.approx(out["diff_ci90"]["hi"])
    assert out["verdict"] == "INCONCLUSIVE"
    assert "zero variance" in out["verdict_reason"]


@pytest.mark.parametrize("n_disc", [0, 1, g1.MIN_DISCRIMINATING_QUERIES - 1])
def test_three_way_verdict_floor(n_disc):
    ci0 = _stats.CI(0.0, 0.0, 0.0)
    v, reason = g1.three_way_verdict(ci0, ci0, 1.0, n_disc)
    assert v == "INCONCLUSIVE" and reason is not None


def test_three_way_verdict_above_the_floor_can_conclude():
    tight = _stats.CI(0.001, -0.003, 0.005)
    v, reason = g1.three_way_verdict(tight, tight, 0.5, 50)
    assert v == "EQUIVALENT" and reason is None


def test_three_way_verdict_rejects_a_collapsed_interval():
    point = _stats.CI(0.0005, 0.0005, 0.0005)
    v, reason = g1.three_way_verdict(point, point, 0.5, 60)
    assert v == "INCONCLUSIVE"
    assert "zero variance" in reason


def test_primary_comparison_uses_the_largest_rung():
    vals = [0.3 + 0.002 * i for i in range(20)]
    cells = [
        _cell_result(_default_cell("n50", 50), vals),
        _cell_result(_alt_cell("n50", 50), vals),
        _cell_result(_default_cell(), vals),
        _cell_result(_alt_cell(), vals),
    ]
    out = g1.primary_comparison(cells, iters=200)
    assert out["n_docs"] == 200
    assert out["reference_cell"] == _default_cell().cell_id


def test_void_cells_are_excluded_from_the_rung_screen():
    vals = [0.3 + 0.001 * i for i in range(30)]
    ok = _default_cell("n50", 50)
    bad = g1.Cell("n50", 50, "hybrid", 60, 100, False, None)
    cells = [_cell_result(ok, vals), _cell_result(bad, vals, "INVALID (hit deficit)")]
    tune = set(cells[0]["query_ids"])
    _, _, summary = g1.screen_rung(cells, tune, iters=200)
    assert summary["n_valid_cells"] == 1
    assert summary["n_void_cells"] == 1
    assert summary["reference"] == ok.cell_id
    assert summary["reference_is_default"] is True


def test_a_substituted_reference_is_announced_and_voids_the_rung():
    """If the shipping default is void, 'vs the shipping default' is a false
    caption — the rung must say so rather than swap the reference silently."""
    vals = [0.3 + 0.001 * i for i in range(30)]
    default = _default_cell("n50", 50)
    other = g1.Cell("n50", 50, "hybrid", 60, 100, False, None)
    cells = [
        _cell_result(default, vals, "INVALID (hit deficit)"),
        _cell_result(other, vals),
    ]
    tune = set(cells[0]["query_ids"])
    _, interp, summary = g1.screen_rung(cells, tune, iters=200)
    assert summary["reference"] == other.cell_id
    assert summary["reference_is_default"] is False
    assert summary["reference_substituted"] is True
    assert summary["directional_claims"] == "VOID"
    assert "REFERENCE SUBSTITUTED" in interp


# --------------------------------------------------------------------------- #
# The two-stage protocol (§6.4 / §7.2)
# --------------------------------------------------------------------------- #
def test_split_is_40_60_and_deterministic():
    qids = [f"q{i}" for i in range(100)]
    diff = {q: i / 100 for i, q in enumerate(qids)}
    tune, confirm = g1.stratified_split(qids, diff)
    assert len(tune) == 40 and len(confirm) == 60
    assert set(tune).isdisjoint(confirm)
    assert set(tune) | set(confirm) == set(qids)
    assert (tune, confirm) == g1.stratified_split(qids, diff)


def test_split_is_stratified_by_difficulty():
    """An unstratified split can leave the two halves at different baseline
    difficulty, which would inflate or deflate every stage-2 effect."""
    qids = [f"q{i:03d}" for i in range(200)]
    diff = {q: i / 200 for i, q in enumerate(qids)}
    tune, confirm = g1.stratified_split(qids, diff)
    mt = sum(diff[q] for q in tune) / len(tune)
    mc = sum(diff[q] for q in confirm) / len(confirm)
    assert abs(mt - mc) < 0.02


def test_split_handles_a_tiny_query_set():
    tune, confirm = g1.stratified_split(["a", "b"], {"a": 0.1, "b": 0.9})
    assert len(tune) + len(confirm) == 2


def test_split_fixture_is_written_then_reused(tmp_path):
    qids = [f"q{i}" for i in range(50)]
    diff = {q: i / 50 for i, q in enumerate(qids)}
    p = tmp_path / "split.json"
    a = g1.load_or_write_split(p, qids, diff, queries_sha256="abc",
                               difficulty_cell="n50_x")
    assert a["source"] == "derived" and p.exists()
    b = g1.load_or_write_split(p, qids, diff, queries_sha256="abc",
                               difficulty_cell="n50_x")
    assert b["source"] == "fixture"
    assert b["tune"] == a["tune"] and b["confirm"] == a["confirm"]
    assert b["sha256"] == a["sha256"]
    # A changed QUERY SET must not silently reuse the stale split — and the key
    # is the ids actually split, not the dataset digest, so a --query-limit
    # smoke run can never make its 12-query split authoritative for a full run.
    fewer = qids[:12]
    c = g1.load_or_write_split(p, fewer, diff, queries_sha256="abc",
                               difficulty_cell="n50_x")
    assert c["source"] == "derived"
    assert json.loads(p.read_text())["n_queries"] == 12


def test_split_fixture_is_keyed_on_the_queries_actually_split(tmp_path):
    """The dataset digest is over all 300 SciFact queries; a smoke run splits 12
    of them. Keying reuse on the dataset digest would let the smoke fixture win."""
    p = tmp_path / "split.json"
    full = [f"q{i}" for i in range(50)]
    diff = {q: i / 50 for i, q in enumerate(full)}
    g1.load_or_write_split(p, full[:12], diff, queries_sha256="same-dataset",
                           difficulty_cell="smoke")
    again = g1.load_or_write_split(p, full, diff, queries_sha256="same-dataset",
                                   difficulty_cell="full")
    assert again["source"] == "derived"       # NOT reused
    assert len(again["tune"]) + len(again["confirm"]) == 50


def test_screen_uses_bh_fdr_and_says_it_is_not_a_result():
    n = 40
    qids = [f"q{i}" for i in range(n)]
    base = [0.4 + e for e in _noise(11, n, 0.1)]
    cells = [
        _cell_result(_default_cell("n50", 50), base, query_ids=qids),
        _cell_result(g1.Cell("n50", 50, "hybrid", 60, 100, False, None),
                     [b + 0.2 for b in base], query_ids=qids),
    ]
    _, interp, summary = g1.screen_rung(cells, set(qids[:20]), iters=300)
    assert summary["bh_fdr_q"] == g1.SCREEN_Q
    assert summary["split"] == "tune"
    assert summary["n_queries"] == 20        # the tune half only
    assert "NOT a result" in interp
    assert "Benjamini" in interp


def test_nomination_follows_the_preregistered_rule():
    n = 40
    qids = [f"q{i}" for i in range(n)]
    base = [0.4 + e for e in _noise(21, n, 0.1)]
    d50 = _default_cell("n50", 50)
    d200 = _default_cell("n200", 200)
    best50 = g1.Cell("n50", 50, "hybrid", 20, 100, False, None)
    best200 = g1.Cell("n200", 200, "hybrid", 20, 100, False, None)
    dense = g1.Cell("n200", 200, "vector", None, 50, False, None)
    rr = g1.Cell("n200", 200, "hybrid", 60, 100, True, 50)
    cells = [
        _cell_result(d50, base, query_ids=qids),
        _cell_result(d200, base, query_ids=qids),
        _cell_result(best50, [b + 0.30 for b in base], query_ids=qids),
        _cell_result(best200, [b + 0.25 for b in base], query_ids=qids),
        _cell_result(dense, [b + 0.05 for b in base], query_ids=qids),
        _cell_result(rr, [b + 0.10 for b in base], query_ids=qids),
    ]
    summaries = {
        "n50": {"reference_is_default": True, "n_valid_cells": 3},
        "n200": {"reference_is_default": True, "n_valid_cells": 4},
    }
    nom = g1.nominate(cells, summaries, set(qids))
    assert len(nom["shortlist"]) <= g1.MAX_NOMINATIONS
    assert d200.cell_id in nom["shortlist"]      # (1) the shipping default
    assert best50.cell_id in nom["shortlist"]    # (2) best at the smallest rung
    assert best200.cell_id in nom["shortlist"]   # (3) best at the largest rung
    assert dense.cell_id in nom["shortlist"]     # (4) best dense-only
    assert rr.cell_id in nom["shortlist"]        # (5) best rerank-on


def test_nomination_respects_the_latency_budget():
    """Protocol §5.5: p95 <= 2x the shipping default's p95 at the same rung."""
    n = 20
    qids = [f"q{i}" for i in range(n)]
    base = [0.4 + e for e in _noise(31, n, 0.1)]
    d = _default_cell("n50", 50)
    slow = g1.Cell("n50", 50, "hybrid", 20, 200, False, None)
    cells = [
        _cell_result(d, base, query_ids=qids, p95=10.0),
        _cell_result(slow, [b + 0.4 for b in base], query_ids=qids, p95=100.0),
    ]
    summaries = {"n50": {"reference_is_default": True, "n_valid_cells": 2}}
    nom = g1.nominate(cells, summaries, set(qids))
    assert slow.cell_id not in nom["shortlist"]


def test_nomination_excludes_a_rung_with_a_substituted_reference():
    n = 20
    qids = [f"q{i}" for i in range(n)]
    base = [0.4 + e for e in _noise(41, n, 0.1)]
    other = g1.Cell("n50", 50, "hybrid", 60, 100, False, None)
    cells = [_cell_result(other, base, query_ids=qids)]
    summaries = {"n50": {"reference_is_default": False, "n_valid_cells": 1,
                         "reference_substituted": True}}
    nom = g1.nominate(cells, summaries, set(qids))
    assert nom["shortlist"] == []
    assert "n50" in nom["excluded_rungs"]


def test_stage2_applies_holm_over_the_shortlist_only():
    n = 60
    qids = [f"q{i}" for i in range(n)]
    base = [0.4 + e for e in _noise(51, n, 0.1)]
    d = _default_cell()
    good = g1.Cell("n200", 200, "hybrid", 20, 100, False, None)
    meh = g1.Cell("n200", 200, "hybrid", 10, 50, False, None)
    cells = [
        _cell_result(d, base, query_ids=qids),
        _cell_result(good, [b + 0.10 for b in base], query_ids=qids),
        _cell_result(meh, [b + 0.001 for b in base], query_ids=qids),
    ]
    out = g1.confirm_stage2(cells, [d.cell_id, good.cell_id], set(qids), iters=500)
    assert out["family_size"] == 1            # the default is not tested vs itself
    assert set(out["comparisons"]) == {good.cell_id}
    assert out["alpha"] == g1.CONFIRM_ALPHA
    assert out["comparisons"][good.cell_id]["holm_rejected"] is True
    assert good.cell_id in out["recommended"]


def test_the_co_primary_can_veto_a_recommendation():
    """Protocol §5.1: 'must not be worse on either primary'. The co-primary was
    computed but never voted; a candidate that wins nDCG@10 while losing
    nDCG@5-chunk is a split decision, resolved in favour of nDCG@5."""
    n = 60
    qids = [f"q{i}" for i in range(n)]
    base = [0.4 + e for e in _noise(61, n, 0.1)]
    d = _default_cell()
    cand = g1.Cell("n200", 200, "hybrid", 20, 100, False, None)
    cells = [
        _cell_result(d, base, query_ids=qids, co_values=base),
        _cell_result(
            cand,
            [b + 0.10 for b in base],           # much better on the primary
            query_ids=qids,
            co_values=[b - 0.10 for b in base],  # much WORSE on the co-primary
        ),
    ]
    out = g1.confirm_stage2(cells, [d.cell_id, cand.cell_id], set(qids), iters=500)
    v = out["comparisons"][cand.cell_id]
    assert v["holm_rejected"] is True
    assert v["confirmed_different"] is True
    assert v["co_primary_non_inferior"] is False
    assert v["recommended"] is False
    assert cand.cell_id in out["split_decisions"]


# --------------------------------------------------------------------------- #
# The A/A resolution gate (§6.4)
# --------------------------------------------------------------------------- #
def _replicate(cell_id: str, mean: float, ranking: list[str]) -> dict:
    return {
        "cell_id": cell_id,
        "means": {g1.PRIMARY_METRIC: mean},
        "rankings": {"q1": ranking},
    }


def test_aa_gate_passes_when_replicates_agree():
    reps = [
        _replicate("c", 0.700, ["a", "b", "c"]),
        _replicate("c", 0.7005, ["a", "b", "c"]),
        _replicate("c", 0.6998, ["a", "b", "c"]),
    ]
    out = g1.aa_gate(reps)
    assert out["ran"] is True
    assert out["passed"] is True
    assert out["rbo_mean"] == pytest.approx(1.0)
    assert out["sd"] < g1.DELTA / g1.AA_SD_FACTOR


def test_aa_gate_fails_when_the_noise_floor_swallows_delta():
    reps = [
        _replicate("c", 0.70, ["a", "b", "c"]),
        _replicate("c", 0.75, ["c", "b", "a"]),
        _replicate("c", 0.66, ["b", "a", "c"]),
    ]
    out = g1.aa_gate(reps)
    assert out["passed"] is False
    assert out["sd"] > g1.DELTA / g1.AA_SD_FACTOR
    assert out["rbo_mean"] < 1.0


def test_aa_gate_reports_that_it_did_not_run():
    out = g1.aa_gate([_replicate("c", 0.7, ["a"])])
    assert out["ran"] is False and out["passed"] is None
    assert "replicate" in out["reason"]


# --------------------------------------------------------------------------- #
# The recommendation gate
# --------------------------------------------------------------------------- #
def _open_gate_inputs():
    primary = {"n_docs": 200, "valid": True, "reference_mean": 0.62}
    libs = {"n200": {"hnsw": {"hnsw_built": True}}}
    aa = {"ran": True, "passed": True, "sd": 0.001}
    git = {"dirty": False}
    summaries = {"n200": {"reference_substituted": False}}
    return primary, libs, aa, git, summaries


def test_recommendation_gate_opens_when_everything_holds():
    gate = g1.recommendation_gate(*_open_gate_inputs())
    assert gate["permitted"] is True
    assert gate["blocked_reasons"] == []
    assert gate["decision_rung_regime"] == g1.REGIME_HNSW


def test_recommendation_gate_refuses_when_hnsw_was_never_built():
    """The pilot's actual regime: an exact brute-force scan makes H1b vacuous,
    and nDCG saturates so delta sits inside the ceiling."""
    primary, libs, aa, git, summaries = _open_gate_inputs()
    libs = {"n200": {"hnsw": {"hnsw_built": False}}}
    gate = g1.recommendation_gate(primary, libs, aa, git, summaries)
    assert gate["permitted"] is False
    assert gate["decision_rung_regime"] == g1.REGIME_BRUTE_FORCE
    assert any("HNSW was never built" in r for r in gate["blocked_reasons"])
    assert any("vacuous" in r for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_when_hnsw_status_is_unknown():
    primary, libs, aa, git, summaries = _open_gate_inputs()
    libs = {"n200": {"hnsw": {"error": "boom"}}}
    gate = g1.recommendation_gate(primary, libs, aa, git, summaries)
    assert gate["permitted"] is False
    assert any("UNKNOWN" in r for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_without_the_aa_gate():
    primary, libs, _, git, summaries = _open_gate_inputs()
    gate = g1.recommendation_gate(
        primary, libs, {"ran": False, "reason": "not requested"}, git, summaries
    )
    assert gate["permitted"] is False
    assert any("A/A resolution gate did not run" in r
               for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_when_the_aa_gate_failed():
    primary, libs, _, git, summaries = _open_gate_inputs()
    gate = g1.recommendation_gate(
        primary, libs, {"ran": True, "passed": False, "sd": 0.05}, git, summaries
    )
    assert any("under-resolved" in r for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_at_the_metric_ceiling():
    primary, libs, aa, git, summaries = _open_gate_inputs()
    primary = {**primary, "reference_mean": 0.99}
    gate = g1.recommendation_gate(primary, libs, aa, git, summaries)
    assert any("ceiling" in r for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_on_a_void_primary_cell():
    primary, libs, aa, git, summaries = _open_gate_inputs()
    gate = g1.recommendation_gate({**primary, "valid": False}, libs, aa, git,
                                  summaries)
    assert any("§5.4" in r for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_on_a_substituted_reference():
    primary, libs, aa, git, _ = _open_gate_inputs()
    gate = g1.recommendation_gate(
        primary, libs, aa, git, {"n200": {"reference_substituted": True}}
    )
    assert any("substituted" in r for r in gate["blocked_reasons"])


def test_recommendation_gate_refuses_from_a_dirty_tree():
    primary, libs, aa, _, summaries = _open_gate_inputs()
    gate = g1.recommendation_gate(
        primary, libs, aa,
        {"dirty": True, "dirty_files": ["a.py"], "dirty_digest": "sha256:x"},
        summaries,
    )
    assert any("dirty" in r for r in gate["blocked_reasons"])


def test_gate_banner_shouts_when_shut():
    primary, libs, aa, git, summaries = _open_gate_inputs()
    shut = g1.recommendation_gate(
        primary, {"n200": {"hnsw": {"hnsw_built": False}}}, aa, git, summaries
    )
    b = g1._gate_banner(shut)
    assert "NO RECOMMENDATION MAY BE EMITTED" in b
    assert "HNSW was never built" in b
    assert g1._gate_banner(g1.recommendation_gate(*_open_gate_inputs())).startswith(
        "> ✅"
    )


# --------------------------------------------------------------------------- #
# New _stats primitives
# --------------------------------------------------------------------------- #
def test_benjamini_hochberg_is_less_conservative_than_holm():
    ps = {f"c{i}": p for i, p in enumerate([0.001, 0.02, 0.03, 0.04, 0.20])}
    bh = _stats.benjamini_hochberg(ps, q=0.10)
    holm = _stats.holm_bonferroni(ps, alpha=0.10)
    assert sum(v[1] for v in bh.values()) >= sum(v[1] for v in holm.values())
    assert bh["c0"][1] is True
    assert bh["c4"][1] is False
    # Adjusted p-values are monotone in the raw ordering.
    adj = [bh[f"c{i}"][0] for i in range(5)]
    assert adj == sorted(adj)
    assert all(0.0 <= a <= 1.0 for a in adj)


def test_benjamini_hochberg_discovers_nothing_when_nothing_is_there():
    ps = {f"c{i}": 0.5 + i / 100 for i in range(10)}
    assert not any(v[1] for v in _stats.benjamini_hochberg(ps, q=0.10).values())


def test_rbo_is_1_for_identical_lists_and_0_for_disjoint():
    assert _stats.rbo(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)
    assert _stats.rbo(["a", "b"], ["x", "y"]) == pytest.approx(0.0)
    assert _stats.rbo([], []) == pytest.approx(1.0)


def test_rbo_is_top_weighted():
    """A swap at rank 1 must cost more than the same swap at rank 9."""
    base = [str(i) for i in range(10)]
    top_swap = ["1", "0"] + base[2:]
    deep_swap = base[:8] + ["9", "8"]
    assert _stats.rbo(base, top_swap) < _stats.rbo(base, deep_swap)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_smoke_flag_reduces_to_the_primary_pair():
    args = g1.parse_args([*STORE_FLAGS, "--smoke", "--doc-counts", "50"])
    grid = g1.build_grid(args.doc_counts, args.modes, args.rrf_k, args.depths,
                         args.rerank, args.rerank_candidates)
    assert len(grid) == 2
    assert {c.depth for c in grid} == {g1.DEFAULT_DEPTH, g1.PRIMARY_DEPTH}


def test_cli_exposes_depths_not_top_k_and_multipliers():
    args = g1.parse_args([*STORE_FLAGS, "--depths", "10,20,50"])
    assert args.depths == [10, 20, 50]
    assert not hasattr(args, "multipliers")
    with pytest.raises(SystemExit):
        g1.parse_args([*STORE_FLAGS, "--multipliers", "2,10"])


def test_cli_defaults_request_the_aa_replicates():
    args = g1.parse_args(list(STORE_FLAGS))
    assert args.aa_replicates == g1.AA_REPLICATES >= 3
    assert args.require_clean is False


def test_cli_rejects_an_unknown_mode():
    with pytest.raises(SystemExit):
        g1.parse_args([*STORE_FLAGS, "--modes", "sparse"])


def test_bool_list_parsing():
    assert g1._bool_list("on,off") == [True, False]
    assert g1._bool_list("false") == [False]
    with pytest.raises(argparse.ArgumentTypeError):
        g1._bool_list("maybe")
