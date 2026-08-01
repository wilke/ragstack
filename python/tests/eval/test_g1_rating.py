"""Unit tests for the G1 human relevance-rating apparatus.

Everything here is offline — no Qdrant, no ES, no embedding fleet, no LLM. The
LLM path is exercised against a stub chat client, which is also how the
paraphrase-first invariant (§9 T1b(a): the verbatim chunk must never reach the
query-writing prompt) is *tested* rather than merely documented.

The eval scripts live under ``python/scripts/eval`` and import each other as
siblings, so the directory goes on ``sys.path`` the same way the harnesses do.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import _g1_rating as g1r  # noqa: E402
import _stats  # noqa: E402
import g1_agreement as agree  # noqa: E402
import g1_make_pool as pool  # noqa: E402
import g1_make_queries as mkq  # noqa: E402

RATING_TOOL = _EVAL_DIR / "rating_tool" / "index.html"


# --------------------------------------------------------------------------- #
# Blinding (protocol §4.4)
# --------------------------------------------------------------------------- #
def _good_record(pair_id: str = "p-abc") -> dict:
    return {
        "pair_id": pair_id,
        "assignment_id": "a-1",
        "rater_id": "alice",
        "set": "live",
        "query_id": "q1",
        "query": "What confers cefiderocol resistance?",
        "chunk_text": "NDM-producing isolates ...",
        "doc_title": "A paper",
    }


def test_blinding_accepts_the_allowed_shape():
    assert g1r.blinding_violations(_good_record()) == []
    g1r.assert_blind([_good_record()])


@pytest.mark.parametrize(
    "field",
    ["cell_id", "rrf_k", "llm_grade", "grade", "rank", "score", "best_rank",
     "bm25_score", "hybrid_rank", "judge_reason", "rerank_candidates", "doc_id"],
)
def test_blinding_rejects_leaky_fields(field):
    rec = {**_good_record(), field: "x"}
    assert field in g1r.blinding_violations(rec)
    with pytest.raises(ValueError):
        g1r.assert_blind([rec])


def test_blinding_rejects_missing_required_fields():
    rec = _good_record()
    del rec["chunk_text"]
    with pytest.raises(ValueError, match="missing required"):
        g1r.assert_blind([rec])


def test_rating_tool_denylist_matches_python():
    """The browser re-checks blinding on load; the two lists must not drift."""
    html = RATING_TOOL.read_text(encoding="utf-8")

    def _js_set(name: str) -> set[str]:
        m = re.search(rf"const {name} = (?:new Set\(\[|\[)(.*?)\]", html, re.S)
        assert m, f"{name} not found in the rating tool"
        return set(re.findall(r'"([^"]+)"', m.group(1)))

    assert _js_set("ALLOWED_KEYS") == set(g1r.ASSIGNMENT_ALLOWED_KEYS)
    assert _js_set("DENY_KEYS") == set(g1r.BLINDING_DENY_KEYS)
    assert _js_set("DENY_SUBSTRINGS") == set(g1r.BLINDING_DENY_SUBSTRINGS)
    assert _js_set("REQUIRED_KEYS") == set(g1r.ASSIGNMENT_REQUIRED_KEYS)


def test_rating_tool_is_self_contained():
    """No network of any kind: the tool must work from file:// with no server."""
    html = re.sub(r"<!--.*?-->", "", RATING_TOOL.read_text(encoding="utf-8"), flags=re.S)
    for probe in ("http://", "https://", "fetch(", "XMLHttpRequest", "<script src", "@import"):
        assert probe not in html, f"rating tool references {probe!r} — it must be self-contained"


# --------------------------------------------------------------------------- #
# Pair ids
# --------------------------------------------------------------------------- #
def test_pair_id_is_content_addressed_and_stable():
    a = g1r.pair_id("q1", "c1")
    assert a == g1r.pair_id("q1", "c1")
    assert a != g1r.pair_id("q1", "c2")
    assert a != g1r.pair_id("c1", "q1")  # not symmetric — order is part of the key
    assert a.startswith("p-")


# --------------------------------------------------------------------------- #
# IDF overlap — the T1b covariate (§9)
# --------------------------------------------------------------------------- #
def test_idf_overlap_is_1_for_a_verbatim_copy_and_0_for_disjoint_text():
    idf = g1r.idf_table(["alpha beta gamma", "delta epsilon", "beta gamma delta"])
    verbatim = g1r.idf_overlap("alpha beta gamma", "alpha beta gamma extra", idf)
    assert verbatim["idf_overlap"] == pytest.approx(1.0)
    disjoint = g1r.idf_overlap("zeta eta", "alpha beta", idf)
    assert disjoint["idf_overlap"] == 0.0
    assert disjoint["jaccard"] == 0.0


def test_idf_overlap_weights_rare_terms_more_than_common_ones():
    """A shared *rare* term must move the covariate more than a shared common one —
    otherwise the covariate cannot distinguish a copied entity name from a shared
    stopword-ish token, which is the whole point of weighting it."""
    docs = ["common term here"] * 20 + ["rare_marker present"]
    idf = g1r.idf_table(docs)
    shared_rare = g1r.idf_overlap("rare_marker common", "rare_marker absent", idf)
    shared_common = g1r.idf_overlap("rare_marker common", "common absent", idf)
    assert shared_rare["idf_overlap"] > shared_common["idf_overlap"]


def test_distribution_reports_tertile_edges():
    d = g1r.distribution([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    assert d["n"] == 6
    assert len(d["tertile_edges"]) == 2
    assert d["tertile_edges"][0] <= d["tertile_edges"][1]


# --------------------------------------------------------------------------- #
# Query screening (protocol §4.2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty"),
        ("What is it?", "too_short"),
        ("word " * 70 + "?", "too_long"),
        ("What did this study find about carbapenem resistance mechanisms?", "names_document"),
        ("Carbapenem resistance mechanisms in Klebsiella are varied.", "not_a_question"),
        ("What mechanisms confer cefiderocol resistance in Klebsiella pneumoniae?", None),
    ],
)
def test_screen_query_reasons(text, reason):
    assert mkq.screen_query(text) == reason


def test_screen_query_rejects_title_answerable():
    title = "High prevalence of cefiderocol resistance among NDM-producing Klebsiella pneumoniae"
    idf = g1r.idf_table([title, "unrelated text about ribosomes", "another document entirely"])
    q = "What is the prevalence of cefiderocol resistance among NDM-producing Klebsiella pneumoniae?"
    assert mkq.screen_query(q, title=title, idf=idf) == "title_answerable"
    other = "How does efflux pump overexpression alter aminoglycoside uptake in mycobacteria?"
    assert mkq.screen_query(other, title=title, idf=idf) is None


def test_screen_query_detects_duplicates():
    seen: set[str] = set()
    q = "Which enzymes hydrolyse carbapenems in Enterobacterales?"
    assert mkq.screen_query(q, seen=seen) is None
    seen.add(mkq.normalize(q))
    assert mkq.screen_query("  which ENZYMES hydrolyse carbapenems in Enterobacterales?  ", seen=seen) == "duplicate"


# --------------------------------------------------------------------------- #
# LLM generation — the paraphrase-first invariant (§9 T1b(a))
# --------------------------------------------------------------------------- #
class _StubLLM:
    """Records every prompt it is given, so the test can assert what the model saw."""

    def __init__(self):
        self.prompts: list[str] = []

    async def complete_text(self, prompt: str, max_tokens: int = 512, temperature: float = 0.0) -> str:
        self.prompts.append(prompt)
        if prompt.startswith(mkq.PARAPHRASE_PROMPT[:40]):
            return "A summary of a finding about resistance in a bacterial pathogen."
        if prompt.startswith(mkq.CRITIC_PROMPT[:30]):
            return "YES"
        return "Which resistance determinants drive treatment failure in Gram-negative infections?"


def _chunks(n: int = 12) -> list[dict]:
    return [
        {
            "chunk_id": f"PMC1#{i}",
            "doc_id": "PMC1",
            "title": "Some article title",
            "text": f"UNIQUEMARKER{i} carbapenemase activity was measured across {i} isolates.",
        }
        for i in range(n)
    ]


def test_generation_never_shows_the_chunk_to_the_query_pass():
    llm = _StubLLM()
    chunk = _chunks(1)[0]
    asyncio.run(mkq.generate_one(llm, chunk["text"]))
    assert len(llm.prompts) == 2
    paraphrase_prompt, query_prompt = llm.prompts
    assert chunk["text"] in paraphrase_prompt
    # The registered mitigation: the verbatim chunk is absent from pass 2.
    assert chunk["text"] not in query_prompt
    assert "UNIQUEMARKER" not in query_prompt


def test_generate_queries_is_deterministic_and_records_the_covariate():
    chunks = _chunks(20)
    idf = g1r.idf_table(c["text"] for c in chunks)
    accepted_a, _ = asyncio.run(
        mkq.generate_queries(_StubLLM(), chunks, n=5, seed=0, idf=idf, progress=False)
    )
    accepted_b, _ = asyncio.run(
        mkq.generate_queries(_StubLLM(), chunks, n=5, seed=0, idf=idf, progress=False)
    )
    assert [a["source_chunk_id"] for a in accepted_a] == [b["source_chunk_id"] for b in accepted_b]
    assert all("idf_overlap" in a["covariates"] for a in accepted_a)
    assert all(a["paraphrase"] for a in accepted_a)
    # The stub always returns the same query text, so only the first survives.
    assert len(accepted_a) == 1


# --------------------------------------------------------------------------- #
# Human query ingestion
# --------------------------------------------------------------------------- #
def test_human_and_llm_paths_produce_the_same_schema(tmp_path):
    chunks = _chunks(6)
    idf = g1r.idf_table(c["text"] for c in chunks)
    llm_acc, _ = asyncio.run(
        mkq.generate_queries(_StubLLM(), chunks, n=1, seed=0, idf=idf, progress=False)
    )
    human_acc, human_rej = mkq.ingest_human_queries(
        [
            {"text": "How does porin loss change carbapenem MICs in Klebsiella?", "author_id": "expert-1"},
            {"text": "What did this paper conclude?", "author_id": "expert-1"},
            {"text": "How does porin loss change carbapenem MICs in Klebsiella?", "author_id": "expert-2"},
        ],
        chunks_by_id={c["chunk_id"]: c for c in chunks},
        idf=idf,
    )
    assert [r["reason"] for r in human_rej] == ["names_document", "duplicate"]
    llm_out = mkq.finalize(llm_acc, "llm")
    human_out = mkq.finalize(human_acc, "human")
    assert set(llm_out[0]) == set(human_out[0])
    assert llm_out[0]["source"] == "llm"
    assert human_out[0]["source"] == "human"
    assert human_out[0]["author_id"] == "expert-1"
    assert all(q["query_id"].startswith("g1q-") for q in llm_out + human_out)


def test_human_input_reads_csv(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text("text,author_id\nWhich efflux pumps export tigecycline in Acinetobacter?,e1\n",
                 encoding="utf-8")
    rows = g1r.read_table(p)
    accepted, rejected = mkq.ingest_human_queries(rows)
    assert not rejected
    assert accepted[0]["author_id"] == "e1"
    assert accepted[0]["covariates"] is None  # no declared source chunk → honest null


# --------------------------------------------------------------------------- #
# Pooling (protocol §4.3)
# --------------------------------------------------------------------------- #
def _rankings() -> dict[str, dict[str, list[str]]]:
    return {
        "P200_hybrid_rrf60_d10_rr0": {"q1": [f"c{i}" for i in range(1, 26)], "q2": ["c9", "c8"]},
        "P200_vector_rrfna_d10_rr0": {"q1": ["c3", "c30", "c1"], "q2": ["c8", "c7"]},
    }


def test_pool_is_a_deduped_fixed_depth_union():
    pairs = pool.pool_pairs(_rankings(), pool_depth=20)
    q1 = [p for p in pairs if p["query_id"] == "q1"]
    ids = {p["chunk_id"] for p in q1}
    # depth 20 truncates c21..c25 out of the first cell but keeps the second cell's c30
    assert "c20" in ids and "c21" not in ids and "c30" in ids
    assert len(ids) == len(q1), "pairs must be deduped"
    c1 = next(p for p in q1 if p["chunk_id"] == "c1")
    assert c1["best_rank"] == 1 and c1["n_cells"] == 2
    c3 = next(p for p in q1 if p["chunk_id"] == "c3")
    assert c3["best_rank"] == 1  # rank 1 in the vector cell beats rank 3 in the hybrid cell


def test_pool_depth_is_honoured_per_cell_not_globally():
    shallow = pool.pool_pairs(_rankings(), pool_depth=2)
    ids = {p["chunk_id"] for p in shallow if p["query_id"] == "q1"}
    assert ids == {"c1", "c2", "c3", "c30"}


def test_pool_order_is_deterministic():
    a = [p["pair_id"] for p in pool.pool_pairs(_rankings())]
    b = [p["pair_id"] for p in pool.pool_pairs(dict(reversed(list(_rankings().items()))))]
    assert a == b


def test_rank_band_and_stratum():
    assert pool.rank_band(1) == "r01"
    assert pool.rank_band(5) == "r02_05"
    assert pool.rank_band(20) == "r11_20"
    assert pool.rank_band(999) == "r21_plus"
    row = {"pair_id": "p-x", "best_rank": 3}
    assert pool.stratum_of(row, None) == "r02_05"
    assert pool.stratum_of(row, {"p-x": 2}) == "r02_05/g2"
    assert pool.stratum_of(row, {"other": 2}) == "r02_05/gna"


# --------------------------------------------------------------------------- #
# Allocation and stratified subsampling
# --------------------------------------------------------------------------- #
def test_allocate_respects_the_budget_the_floor_and_the_cap():
    sizes = {"a": 100, "b": 50, "c": 3}
    alloc = pool.allocate(sizes, 60, min_per_stratum=10)
    assert sum(alloc.values()) == 60
    assert alloc["c"] == 3, "cannot draw more than the stratum holds"
    assert alloc["b"] >= 10, "the floor is what makes rare strata observable"
    assert alloc["a"] > alloc["b"], "proportional above the floor"


def test_allocate_trims_floors_that_exceed_the_budget():
    alloc = pool.allocate({"a": 50, "b": 50, "c": 50}, 12, min_per_stratum=10)
    assert sum(alloc.values()) == 12
    assert max(alloc.values()) - min(alloc.values()) <= 1


def test_allocate_returns_everything_when_n_exceeds_the_pool():
    sizes = {"a": 4, "b": 5}
    assert pool.allocate(sizes, 100) == sizes


def test_allocate_balanced_equalizes_and_spills_surplus():
    alloc = pool.allocate_balanced({"a": 2, "b": 100, "c": 100}, 30)
    assert sum(alloc.values()) == 30
    assert alloc["a"] == 2
    assert abs(alloc["b"] - alloc["c"]) <= 1


def _rows(n: int, ranks: tuple[int, ...] = (1, 3, 8, 15)) -> list[dict]:
    return [
        {"pair_id": f"p-{i:04d}", "query_id": f"q{i % 20}", "chunk_id": f"c{i}",
         "best_rank": ranks[i % len(ranks)]}
        for i in range(n)
    ]


def test_stratified_subsample_is_deterministic_and_covers_every_stratum():
    rows = _rows(400)
    pick_a = pool.stratified_pick(rows, 80, key=lambda r: pool.rank_band(r["best_rank"]),
                                  seed=0, min_per_stratum=5)
    pick_b = pool.stratified_pick(list(reversed(rows)), 80,
                                  key=lambda r: pool.rank_band(r["best_rank"]),
                                  seed=0, min_per_stratum=5)
    assert [p["pair_id"] for p in pick_a] == [p["pair_id"] for p in pick_b]
    assert len(pick_a) == 80
    assert len({p["stratum"] for p in pick_a}) == 4
    assert len({p["pair_id"] for p in pick_a}) == 80


def test_stratified_subsample_changes_with_the_seed():
    rows = _rows(400)
    a = {p["pair_id"] for p in pool.stratified_pick(rows, 40, key=lambda r: "x", seed=0)}
    b = {p["pair_id"] for p in pool.stratified_pick(rows, 40, key=lambda r: "x", seed=1)}
    assert a != b


def test_rare_stratum_survives_the_floor():
    """The reason the floor exists: a grade-2 stratum of 6 pairs in a pool of 2000
    would draw ~0 under pure proportional allocation, and κ(judge–human) would be
    estimated with essentially no information about the labels that matter."""
    rows = _rows(2000, ranks=(15,))
    for r in rows[:6]:
        r["best_rank"] = 1
    picked = pool.stratified_pick(rows, 300, key=lambda r: pool.rank_band(r["best_rank"]),
                                  seed=0, min_per_stratum=5)
    assert sum(1 for p in picked if p["stratum"] == "r01") >= 5


# --------------------------------------------------------------------------- #
# Assignment and the overlap set
# --------------------------------------------------------------------------- #
def test_assignment_double_rates_every_pair_and_balances_load():
    pairs = [f"p-{i:04d}" for i in range(400)]
    out = pool.assign(pairs, ["alice", "bob", "cara"], replication=2, seed=0)
    counts = Counter(p for v in out.values() for p in v)
    assert set(counts) == set(pairs)
    assert set(counts.values()) == {2}
    loads = [len(v) for v in out.values()]
    assert max(loads) - min(loads) <= 1
    for rater, assigned in out.items():
        assert len(set(assigned)) == len(assigned), f"{rater} got a duplicate"


def test_overlap_set_goes_to_every_rater():
    pairs = [f"p-{i:04d}" for i in range(100)]
    overlap = pairs[:20]
    raters = ["a", "b", "c", "d"]
    out = pool.assign(pairs, raters, replication=2, overlap_ids=overlap, seed=0)
    for pid in overlap:
        assert all(pid in out[r] for r in raters), "the overlap set is what makes Fleiss' κ exist"
    counts = Counter(p for v in out.values() for p in v)
    assert all(counts[p] == 2 for p in pairs[20:])
    assert sum(len(v) for v in out.values()) == len(overlap) * 4 + 80 * 2


def test_assignment_is_deterministic_and_seed_sensitive():
    pairs = [f"p-{i:04d}" for i in range(120)]
    a = pool.assign(pairs, ["x", "y", "z"], replication=2, seed=0)
    b = pool.assign(pairs, ["x", "y", "z"], replication=2, seed=0)
    c = pool.assign(pairs, ["x", "y", "z"], replication=2, seed=1)
    assert a == b
    assert a != c


def test_a_query_author_never_judges_their_own_query():
    """SOP §3.4: an expert who wrote the query knows the answer they had in mind,
    which is exactly the information a relevance judgment must be free of."""
    pairs = [f"p-{i:04d}" for i in range(60)]
    forbidden = {p: {"expert"} for p in pairs[:10]}
    out = pool.assign(pairs, ["expert", "b", "c"], replication=2, seed=0, forbidden=forbidden)
    assert not (set(out["expert"]) & set(pairs[:10]))
    counts = Counter(p for v in out.values() for p in v)
    assert set(counts.values()) == {2}


def test_assignment_fails_when_the_conflict_leaves_too_few_raters():
    with pytest.raises(ValueError, match="eligible rater"):
        pool.assign(["p-1"], ["a", "b"], replication=2, forbidden={"p-1": {"a"}})


def test_assignment_rejects_impossible_replication():
    with pytest.raises(ValueError):
        pool.assign(["p-1"], ["a", "b"], replication=3)
    with pytest.raises(ValueError):
        pool.assign(["p-1"], ["a", "a"], replication=1)


def test_assignment_records_are_blind_and_complete():
    pool_rows = [{"pair_id": g1r.pair_id("q1", "c1"), "query_id": "q1", "chunk_id": "c1",
                  "best_rank": 3, "n_cells": 4, "cells": ["P200_hybrid_rrf60_d10_rr0"]}]
    recs = pool.assignment_records(
        [pool_rows[0]["pair_id"]],
        rater_id="alice",
        assignment_id="a-1",
        set_label="live",
        pool_by_id={r["pair_id"]: r for r in pool_rows},
        queries={"q1": "What confers resistance?"},
        chunks={"c1": {"chunk_id": "c1", "doc_id": "PMC1", "text": "body text", "title": ""}},
        titles={"PMC1": "A title"},
    )
    assert recs[0]["doc_title"] == "A title"
    assert "doc_id" not in recs[0] and "best_rank" not in recs[0] and "cells" not in recs[0]
    assert g1r.blinding_violations(recs[0]) == []


def test_assignment_records_fail_loudly_on_a_missing_chunk():
    pid = g1r.pair_id("q1", "c1")
    with pytest.raises(KeyError):
        pool.assignment_records(
            [pid], rater_id="a", assignment_id="a-1", set_label="live",
            pool_by_id={pid: {"pair_id": pid, "query_id": "q1", "chunk_id": "c1"}},
            queries={"q1": "q"}, chunks={}, titles={},
        )


# --------------------------------------------------------------------------- #
# κ (protocol §4.4)
# --------------------------------------------------------------------------- #
def test_cohen_kappa_endpoints():
    cats = [0, 1, 2]
    a = [0, 1, 2, 0, 1, 2, 0, 1, 2]
    assert _stats.cohen_kappa(a, a, cats) == pytest.approx(1.0)
    # Independent-but-matched marginals → κ ≈ 0 by construction.
    b = [0, 0, 0, 1, 1, 1, 2, 2, 2]
    assert abs(_stats.cohen_kappa(a, b, cats)) < 0.2


def test_cohen_kappa_matches_a_hand_worked_example():
    """2x2 textbook case: Po = 0.7, Pe = 0.5, κ = 0.4."""
    a = [0] * 30 + [0] * 20 + [1] * 10 + [1] * 40
    b = [0] * 30 + [1] * 20 + [0] * 10 + [1] * 40
    assert _stats.cohen_kappa(a, b, [0, 1]) == pytest.approx(0.4, abs=1e-9)


def test_weighted_kappa_forgives_boundary_disagreements():
    """A 1-vs-2 split costs less under linear weights than a 0-vs-2 split, which is
    how the labels are actually used (nDCG gain is ``2**grade - 1``)."""
    cats = [0, 1, 2]
    base = [0, 1, 2] * 10
    near = [0, 2, 2] * 10  # 1↔2 confusions
    far = [2, 1, 0] * 10   # 0↔2 confusions
    assert _stats.cohen_kappa(base, near, cats, "linear") > _stats.cohen_kappa(base, near, cats, "none")
    assert _stats.cohen_kappa(base, near, cats, "linear") > _stats.cohen_kappa(base, far, cats, "linear")


def test_fleiss_kappa_endpoints():
    unanimous = [[3, 0, 0], [0, 3, 0], [0, 0, 3], [3, 0, 0]]
    assert _stats.fleiss_kappa(unanimous) == pytest.approx(1.0)
    split = [[1, 1, 1]] * 20
    assert _stats.fleiss_kappa(split) < 0.0
    with pytest.raises(ValueError):
        _stats.fleiss_kappa([[3, 0, 0], [1, 1, 0]])


def test_kappa_bootstrap_ci_brackets_the_point_and_narrows_with_n():
    small = agree.kappa_with_ci([0, 1, 2] * 10, [0, 1, 1] * 10, iters=400, seed=0)
    large = agree.kappa_with_ci([0, 1, 2] * 200, [0, 1, 1] * 200, iters=400, seed=0)
    assert small.lo <= small.point <= small.hi
    assert (large.hi - large.lo) < (small.hi - small.lo)


def test_kappa_se_forecast_justifies_the_300_500_default():
    """The operator's stated reason for 300-500 pairs, made checkable: 100 pairs
    give κ a ±0.15 CI against a 0.4 gate."""
    probs = [0.7, 0.2, 0.1]
    sd_100 = _stats.kappa_se_forecast(100, probs, 0.5, iters=200)
    sd_400 = _stats.kappa_se_forecast(400, probs, 0.5, iters=200)
    assert sd_400 < sd_100
    assert 1.96 * sd_100 > 0.10, "a 100-pair CI must be wide enough to straddle the gate"
    assert 1.96 * sd_400 < 0.12
    # The generative model has population κ = the target, so the forecast is
    # centred on a design point rather than on an arbitrary simulation.
    assert _stats.kappa_se_forecast(400, probs, 0.8, iters=200) < sd_400


def test_bootstrap_statistic_ci_is_seeded():
    a = agree.kappa_with_ci([0, 1, 2] * 20, [0, 1, 1] * 20, iters=300, seed=0)
    b = agree.kappa_with_ci([0, 1, 2] * 20, [0, 1, 1] * 20, iters=300, seed=0)
    assert (a.lo, a.point, a.hi) == (b.lo, b.point, b.hi)


# --------------------------------------------------------------------------- #
# Bands
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,band",
    [(0.9, "STRONG"), (0.7, "SUBSTANTIAL"), (0.45, "MODERATE"), (0.2, "FAIL"), (-0.1, "FAIL")],
)
def test_kappa_bands(value, band):
    assert agree.kappa_band(value)[0] == band


def test_band_verdict_uses_the_lower_band_when_the_ci_spans_a_boundary():
    v = agree.banded_verdict(_stats.CI(0.62, 0.41, 0.80))
    assert v["band_point"] == "SUBSTANTIAL"
    assert v["band_lower_bound"] == "MODERATE"
    assert v["band_effective"] == "MODERATE"
    assert v["spans_boundary"] is True
    assert v["meets_gate"] is True

    tight = agree.banded_verdict(_stats.CI(0.71, 0.65, 0.78))
    assert tight["band_effective"] == "SUBSTANTIAL"
    assert tight["spans_boundary"] is False


def test_band_verdict_flags_the_gate():
    assert agree.banded_verdict(_stats.CI(0.35, 0.2, 0.5))["meets_gate"] is False
    assert agree.banded_verdict(_stats.CI(0.35, 0.2, 0.5))["band_effective"] == "FAIL"


# --------------------------------------------------------------------------- #
# Consensus and adjudication
# --------------------------------------------------------------------------- #
def test_consensus_rules():
    assert agree.consensus({"a": 2, "b": 2}) == (2, "unanimous")
    assert agree.consensus({"a": 1, "b": 2}) == (1, "majority_lower_tie")
    assert agree.consensus({"a": 1, "b": 2, "c": 2}) == (2, "majority_lower_tie")
    assert agree.consensus({"a": 0, "b": 2}) == (None, "needs_adjudication")
    assert agree.consensus({"a": 0}) == (0, "single_rater")
    assert agree.consensus({"a": 0, "b": 2}, {"p1": 1}, "p1") == (1, "adjudicated")


# --------------------------------------------------------------------------- #
# End-to-end analysis
# --------------------------------------------------------------------------- #
def _judgments(n: int = 60, disagree_every: int = 10) -> list[dict]:
    rows = []
    for i in range(n):
        pid = f"p-{i:04d}"
        g = i % 3
        rows.append({"pair_id": pid, "grade": g, "rater_id": "alice", "seconds_on_item": 12.0,
                     "timestamp": f"2026-08-01T00:00:{i:02d}Z", "set": "live"})
        other = 2 - g if i % disagree_every == 0 else g
        rows.append({"pair_id": pid, "grade": other, "rater_id": "bob", "seconds_on_item": 9.0,
                     "timestamp": f"2026-08-01T00:00:{i:02d}Z", "set": "live"})
    return rows


def test_analyse_produces_kappa_consensus_and_an_adjudication_queue():
    res = agree.analyse(_judgments(), None, None, None, iters=200)
    assert res["n_pairs"] == 60
    assert res["n_double_rated"] == 60
    assert res["raters"] == ["alice", "bob"]
    hh = res["human_human"]["pairwise"][0]
    assert hh["n_items"] == 60
    assert hh["kappa"]["kappa"] > 0.5
    # i % 10 == 0 with i % 3 in {0,1,2}: only the 0↔2 flips reach the queue.
    assert res["adjudication_queue"]
    assert all(pid.startswith("p-") for pid in res["adjudication_queue"])
    assert [r["pair_id"] for r in res["adjudication_rows"]] == res["adjudication_queue"]
    assert all(len(r["grades"]) == 2 and r["resolved_grade"] is None
               for r in res["adjudication_rows"])
    assert res["consensus_methods"]["unanimous"] > 0


def test_analyse_computes_the_judge_gate_and_the_human_ceiling():
    judgments = _judgments(60, disagree_every=100)  # near-perfect human agreement
    llm = {f"p-{i:04d}": (i % 3) if i % 5 else (2 - i % 3) for i in range(60)}
    res = agree.analyse(judgments, llm, None, None, iters=200)
    jh = res["judge_human"]
    assert jh["n_items"] > 0
    assert jh["gate"] == agree.GATE
    assert "band_effective" in jh and "consequence" in jh
    assert jh["normalized_vs_human_ceiling"] is not None


def test_rubric_failure_when_humans_disagree_more_than_the_gate():
    """κ(judge–human) is meaningless above the human ceiling — the report must say
    the rubric is the finding, not the judge."""
    rows = []
    for i in range(90):
        pid = f"p-{i:04d}"
        rows.append({"pair_id": pid, "grade": i % 3, "rater_id": "alice", "set": "live",
                     "seconds_on_item": 8.0, "timestamp": "2026-08-01T00:00:00Z"})
        rows.append({"pair_id": pid, "grade": (i * 2 + 1) % 3, "rater_id": "bob", "set": "live",
                     "seconds_on_item": 8.0, "timestamp": "2026-08-01T00:00:00Z"})
    llm = {f"p-{i:04d}": i % 3 for i in range(90)}
    res = agree.analyse(rows, llm, None, None, iters=200)
    assert res["human_human"]["mean_pairwise_kappa"] < agree.GATE
    assert res["judge_human"]["band_effective"] == "RUBRIC_FAILURE"


def test_judge_band_is_capped_by_the_human_ceiling():
    """SOP §6.4.2: with humans at moderate agreement, a high κ(judge–human) cannot
    be demonstrated — the judge is scored against a target the humans dispute."""
    rows = []
    for i in range(120):
        pid = f"p-{i:04d}"
        alice = i % 3
        # disagree on a third of the items, spread evenly over the three grades so
        # the marginals stay comparable and κ lands in the 0.4-0.6 band
        bob = (alice + 1) % 3 if i % 9 in (0, 4, 8) else alice
        rows.append({"pair_id": pid, "grade": alice, "rater_id": "alice", "set": "live",
                     "seconds_on_item": 20.0, "timestamp": "2026-08-01T00:00:00Z"})
        rows.append({"pair_id": pid, "grade": bob, "rater_id": "bob", "set": "live",
                     "seconds_on_item": 20.0, "timestamp": "2026-08-01T00:00:00Z"})
    res = agree.analyse(rows, None, None, None, iters=200)
    hh = res["human_human"]["mean_pairwise_kappa"]
    assert agree.GATE <= hh < 0.60, f"fixture must sit in the capped band, got {hh}"

    llm = {r["pair_id"]: r["grade"] for r in rows if r["rater_id"] == "alice"}
    res2 = agree.analyse(rows, llm, None, None, iters=200)
    jh = res2["judge_human"]
    assert jh["band_point"] in ("SUBSTANTIAL", "STRONG")
    assert jh["band_effective"] == "MODERATE"
    assert jh["ceiling_rule"] == "capped_by_kappa_human_human"


def test_dedupe_keeps_the_latest_and_reports_test_retest():
    rows = [
        {"pair_id": "p-1", "grade": 1, "rater_id": "a", "timestamp": "2026-01-01T00:00:00Z"},
        {"pair_id": "p-1", "grade": 2, "rater_id": "a", "timestamp": "2026-01-02T00:00:00Z"},
        {"pair_id": "p-2", "grade": 0, "rater_id": "a", "timestamp": "2026-01-01T00:00:00Z"},
        {"pair_id": "p-2", "grade": 0, "rater_id": "a", "timestamp": "2026-01-02T00:00:00Z"},
    ]
    kept, info = agree.dedupe(sorted(rows, key=lambda r: (r["rater_id"], r["pair_id"], r["timestamp"])))
    assert {r["pair_id"]: r["grade"] for r in kept} == {"p-1": 2, "p-2": 0}
    assert info["n_repeat_ratings"] == 2
    assert info["test_retest_agreement"] == 0.5


def test_rater_stats_expose_the_disqualification_inputs():
    rows = [
        {"pair_id": f"p-{i}", "grade": 0, "rater_id": "speedy", "seconds_on_item": 1.0,
         "timestamp": "2026-08-01T00:00:00Z", "shuffle_seed": 7, "assignment_id": "a-1"}
        for i in range(20)
    ]
    stats = agree.rater_stats(rows)["speedy"]
    assert stats["median_seconds"] == 1.0
    assert stats["share_under_5s"] == 1.0
    assert stats["unused_grades"] == ["1", "2"]


def test_calibration_scoring_passes_and_fails():
    key = {f"p-{i}": i % 3 for i in range(30)}
    good = [{"pair_id": f"p-{i}", "grade": i % 3, "rater_id": "ok", "set": "calibration",
             "timestamp": "t", "seconds_on_item": 20.0} for i in range(30)]
    bad = [{"pair_id": f"p-{i}", "grade": (i + 1) % 3, "rater_id": "no", "set": "calibration",
            "timestamp": "t", "seconds_on_item": 2.0} for i in range(30)]
    scored = agree.score_calibration(good + bad, key)
    assert scored["ok"]["passed"] is True
    assert scored["ok"]["exact_agreement"] == 1.0
    assert scored["no"]["passed"] is False


def test_markdown_report_renders():
    res = agree.analyse(_judgments(30), {f"p-{i:04d}": i % 3 for i in range(30)}, None, None, iters=100)
    md = agree.render_markdown(res)
    assert "# G1 rating agreement" in md
    assert "Judge–human" in md
    assert "Consequence" in md


# --------------------------------------------------------------------------- #
# Driver smoke test — pooling through to blinded assignment files
# --------------------------------------------------------------------------- #
def test_make_pool_end_to_end(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    queries = {f"q{i}": f"What is the effect of factor {i} on growth rate?" for i in range(12)}
    chunks = [
        {"chunk_id": f"c{j}", "doc_id": f"PMC{j % 4}", "text": f"passage {j} about growth", "title": ""}
        for j in range(40)
    ]
    for cell in ("P200_hybrid_rrf60_d10_rr0", "P200_vector_rrfna_d10_rr0"):
        offset = 0 if "hybrid" in cell else 5
        with (raw / f"{cell}.rankings.jsonl").open("w", encoding="utf-8") as fh:
            for i in range(12):
                ids = [f"c{(i * 3 + k + offset) % 40}" for k in range(20)]
                fh.write(json.dumps({"query_id": f"q{i}", "chunk_ids": ids}) + "\n")

    qpath = tmp_path / "queries.jsonl"
    g1r.write_jsonl(qpath, [{"query_id": k, "text": v, "source": "llm"} for k, v in queries.items()])
    cpath = tmp_path / "chunks.jsonl"
    g1r.write_jsonl(cpath, chunks)
    out = tmp_path / "round1"

    rc = pool.main([
        "--rankings", str(raw), "--queries", str(qpath), "--chunks", str(cpath),
        "--raters", "alice,bob,cara", "--replication", "2", "--subsample", "120",
        "--overlap-n", "20", "--calibration-n", "10", "--allow-any-size",
        "--out-dir", str(out),
    ])
    assert rc == 0

    man = json.loads((out / "manifest.json").read_text())
    assert man["pooling"]["pool_depth"] == 20
    assert man["assignment"]["overlap_n"] == 20
    assert man["assignment"]["total_judgments"] == 20 * 3 + 100 * 2
    assert man["kappa_precision_forecast"]["double_rated_n"] == 120
    assert man["protocol"]["sha256"], "the protocol hash is required provenance (§8.1)"

    seen: Counter = Counter()
    for rater in ("alice", "bob", "cara"):
        recs = g1r.read_jsonl(out / "assignments" / f"{rater}.jsonl")
        g1r.assert_blind(recs)  # would raise if any config field leaked
        assert {r["rater_id"] for r in recs} == {rater}
        seen.update(r["pair_id"] for r in recs)
    assert set(seen.values()) == {2, 3}

    calib = g1r.read_jsonl(out / "calibration.jsonl")
    assert len(calib) == 10
    g1r.assert_blind([{k: v for k, v in r.items() if k != "rater_id"} | {"rater_id": "x"} for r in calib])
    live_ids = set(seen)
    assert not (live_ids & {r["pair_id"] for r in calib}), (
        "a calibration pair must not reappear as live work — the rater has seen its gold label"
    )
    key = g1r.read_jsonl(out / "calibration_key.template.jsonl")
    assert all(r["gold_grade"] is None for r in key)


def test_make_pool_rejects_an_underpowered_subsample(tmp_path):
    with pytest.raises(SystemExit, match="0.4 gate"):
        pool.main([
            "--rankings", str(tmp_path), "--queries", "x", "--chunks", "y",
            "--raters", "a,b", "--subsample", "100", "--out-dir", str(tmp_path / "o"),
        ])
