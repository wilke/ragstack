"""Unit tests for the G1 retrieval-parameter sweep harness.

Everything here is offline: sampling determinism, the production depth
arithmetic, grid expansion, the chunk-level metric expansion, per-leg accounting
(against fake stores), and the manifest shape. The parts that need the live
embedding fleet + Qdrant/ES — ``build_library_index`` and ``evaluate_cell`` — are
exercised by the smoke run, not here.

The eval scripts live under ``python/scripts/eval`` and import each other as
siblings, so the directory goes on ``sys.path`` the same way the harnesses do.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import g1_library_sweep as g1  # noqa: E402

from ragstack.models import Chunk, ScoredChunk  # noqa: E402
from ragstack.retrieval.retriever import HybridRetriever  # noqa: E402
from ragstack.scoring.scorers import RRFScorer  # noqa: E402


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
    assert g1.library_collection_name(200, "deadbeef") == "g1_lib_200docs_deadbeef"


# --------------------------------------------------------------------------- #
# Sampling determinism
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
    """Prefix-greedy accumulation makes metrics paired across library sizes."""
    corpus, qrels = _corpus(), _qrels()
    s50 = g1.sample_library(corpus, qrels, 50, seed=0)
    s100 = g1.sample_library(corpus, qrels, 100, seed=0)
    s200 = g1.sample_library(corpus, qrels, 200, seed=0)
    assert s50.query_ids == s100.query_ids[: len(s50.query_ids)]
    assert s100.query_ids == s200.query_ids[: len(s100.query_ids)]
    assert set(s50.judged_doc_ids) <= set(s100.judged_doc_ids) <= set(s200.judged_doc_ids)


def test_judged_fraction_is_respected():
    corpus, qrels = _corpus(), _qrels()
    s = g1.sample_library(corpus, qrels, 100, seed=0, judged_fraction=0.25)
    assert len(s.judged_doc_ids) <= 25 + 2  # the accepting query may straddle


def test_sample_library_rejects_nonsense_size():
    with pytest.raises(ValueError):
        g1.sample_library(_corpus(), _qrels(), 0)


# --------------------------------------------------------------------------- #
# Depth arithmetic — the production composition
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("top_k", "mult", "rr", "cand", "expected"),
    [
        (5, 2, False, 50, 10),    # shipping defaults, rerank off
        (5, 2, True, 50, 100),    # shipping defaults, rerank ON -> 10x breadth
        (5, 10, False, 50, 50),   # the pre-registered alternative
        (10, 1, False, 0, 10),
        (5, 2, True, 3, 10),      # rerank_candidates below top_k is a no-op
    ],
)
def test_leg_depth_matches_production_composition(top_k, mult, rr, cand, expected):
    assert g1.leg_depth_for(top_k, mult, rr, cand) == expected


# --------------------------------------------------------------------------- #
# Grid expansion
# --------------------------------------------------------------------------- #
def test_grid_collapses_rrf_k_outside_hybrid():
    grid = g1.build_grid([50], ["vector", "bm25"], [1, 60, 240], [5], [2], [False], [50])
    assert {c.rrf_k for c in grid if c.mode in ("vector", "bm25")} == {None}
    # 2 modes x 1 rrf(None) + the 2 forced primary hybrid cells
    assert len([c for c in grid if c.mode != "hybrid"]) == 2


def test_grid_always_contains_the_preregistered_primary_pair():
    """Even when the CLI grid excludes them, the primary comparison must exist."""
    grid = g1.build_grid([50, 200], ["bm25"], [60], [7], [3], [False], [50])
    for n in (50, 200):
        cells = [c for c in grid if c.n_docs == n]
        assert any(c.is_default for c in cells)
        assert any(c.is_primary_alt for c in cells)


def test_grid_cell_ids_are_unique_and_stable():
    grid = g1.build_grid([50], ["hybrid"], [10, 60], [5], [2, 10], [False, True], [50])
    ids = [c.cell_id for c in grid]
    assert len(ids) == len(set(ids))
    assert "n50_hybrid_rrf60_tk5_m2_rr0" in ids
    assert "n50_hybrid_rrf60_tk5_m10_rr0" in ids
    assert "n50_hybrid_rrf60_tk5_m2_rr50" in ids


def test_grid_does_not_emit_rerank_candidates_when_rerank_is_off():
    grid = g1.build_grid([50], ["hybrid"], [60], [5], [2], [False], [10, 50, 100])
    assert {c.rerank_candidates for c in grid} == {None}


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


def test_run_manifest_carries_every_required_provenance_section():
    args = g1.parse_args(["--doc-counts", "50", "--smoke"])
    grid = g1.build_grid([50], ["hybrid"], [60], [5], [2], [False], [50])
    m = g1.build_run_manifest(
        run_id="g1-test", argv=["g1_library_sweep.py", "--smoke"], args=args,
        dataset=g1.dataset_provenance(_fake_docs(), {"1": "q"}, {"1": {"d0": 1}}, "src"),
        indexes={}, grid=grid, started_at="2026-01-01T00:00:00+00:00",
    )
    assert m["schema_version"] == "ragstack.eval_run/v1"
    for key in ("run_id", "protocol_version", "git", "ragstack_version", "dataset",
                "build_spec", "libraries", "grid", "runtime", "seeds", "argv", "cwd"):
        assert key in m, key
    assert m["build_spec"]["spec_hash"] and len(m["build_spec"]["spec_hash"]) == 8
    assert m["build_spec"]["chunk_config"] == g1.CHUNK_CONFIG_KEY
    assert m["grid"]["primary_comparison"]["preregistered"] is True
    assert m["grid"]["primary_comparison"]["metric"] == g1.PRIMARY_METRIC
    assert m["seeds"]["bootstrap"] == 0
    assert set(m["runtime"]["packages"]) >= {"qdrant-client", "elasticsearch", "numpy"}
    assert m["argv"] == ["g1_library_sweep.py", "--smoke"]


def test_build_spec_matches_the_collection_name_hash():
    desc, spec_hash, _ = g1.build_spec()
    assert desc.startswith("fixed_tok512/512/64")
    assert g1.library_collection_name(200, spec_hash).endswith(spec_hash)


# --------------------------------------------------------------------------- #
# Statistics wiring
# --------------------------------------------------------------------------- #
def _cell_result(cell: g1.Cell, values: list[float], verdict: str = "PASS") -> dict:
    n = len(values)
    per_query = {m: list(values) for m in g1.METRIC_NAMES}
    return {
        "cell_id": cell.cell_id,
        "params": cell.as_params(),
        "n_queries": n,
        "query_ids": [str(i) for i in range(n)],
        "per_query": per_query,
        "means": {m: sum(v) / n for m, v in per_query.items()},
        "counters": [],
        "sanity": {"verdict": verdict, "dense_deficit_rate": 0.0,
                   "bm25_deficit_rate": 0.0, "dense_starved_rate": 0.0,
                   "bm25_starved_rate": 0.0, "leg_depth": 10,
                   "dense_matchable": 1000,
                   "assertion": "hits == min(D, matchable) per leg"},
        "cost": {"p50_query_ms": 1.0, "p95_query_ms": 2.0, "mean_dense_ms": 1.0,
                 "mean_bm25_ms": 1.0, "mean_union_depth": 15.0, "mean_overlap": 5.0},
    }


def test_primary_comparison_detects_a_real_improvement():
    base = [0.30 + 0.001 * i for i in range(60)]
    better = [b + 0.08 for b in base]
    ref = g1.Cell("n200", 200, "hybrid", 60, 5, 2, False, None)
    alt = g1.Cell("n200", 200, "hybrid", 60, 5, g1.PRIMARY_MULTIPLIER, False, None)
    out = g1.primary_comparison(
        [_cell_result(ref, base), _cell_result(alt, better)], iters=500
    )
    assert out["verdict"] == "DIFFERENT"
    assert out["diff_ci95"]["point"] == pytest.approx(0.08, abs=1e-9)
    assert out["valid"] is True
    assert out["n_docs"] == 200


def test_primary_comparison_declares_equivalence_when_nothing_moves():
    base = [0.30 + 0.001 * i for i in range(60)]
    same = [b + 0.0005 for b in base]
    ref = g1.Cell("n200", 200, "hybrid", 60, 5, 2, False, None)
    alt = g1.Cell("n200", 200, "hybrid", 60, 5, g1.PRIMARY_MULTIPLIER, False, None)
    out = g1.primary_comparison(
        [_cell_result(ref, base), _cell_result(alt, same)], iters=500
    )
    assert out["verdict"] == "EQUIVALENT"


def test_primary_comparison_uses_the_largest_rung():
    ref50 = g1.Cell("n50", 50, "hybrid", 60, 5, 2, False, None)
    alt50 = g1.Cell("n50", 50, "hybrid", 60, 5, g1.PRIMARY_MULTIPLIER, False, None)
    ref200 = g1.Cell("n200", 200, "hybrid", 60, 5, 2, False, None)
    alt200 = g1.Cell("n200", 200, "hybrid", 60, 5, g1.PRIMARY_MULTIPLIER, False, None)
    vals = [0.3] * 20
    out = g1.primary_comparison(
        [_cell_result(c, vals) for c in (ref50, alt50, ref200, alt200)], iters=200
    )
    assert out["n_docs"] == 200
    assert out["reference_cell"] == ref200.cell_id


def test_void_cells_are_excluded_from_the_rung_statistics():
    vals = [0.3 + 0.001 * i for i in range(30)]
    ok = g1.Cell("n50", 50, "hybrid", 60, 5, 2, False, None)
    bad = g1.Cell("n50", 50, "hybrid", 60, 5, 10, False, None)
    cells = [_cell_result(ok, vals), _cell_result(bad, vals, "INVALID (hit deficit)")]
    _, _, summary = g1.stats_for_rung(cells, iters=200)
    assert summary["n_valid_cells"] == 1
    assert summary["n_void_cells"] == 1
    assert summary["reference"] == ok.cell_id


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_smoke_flag_reduces_to_the_primary_pair():
    args = g1.parse_args(["--smoke", "--doc-counts", "50"])
    grid = g1.build_grid(args.doc_counts, args.modes, args.rrf_k, args.top_k,
                         args.multipliers, args.rerank, args.rerank_candidates)
    assert len(grid) == 2
    assert {c.multiplier for c in grid} == {2, g1.PRIMARY_MULTIPLIER}


def test_cli_rejects_an_unknown_mode():
    with pytest.raises(SystemExit):
        g1.parse_args(["--modes", "sparse"])


def test_bool_list_parsing():
    assert g1._bool_list("on,off") == [True, False]
    assert g1._bool_list("false") == [False]
    with pytest.raises(argparse.ArgumentTypeError):
        g1._bool_list("maybe")
