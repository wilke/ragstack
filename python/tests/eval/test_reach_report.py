"""The reach comparison's report and J builder (``reach_report.py``; PREREG §3, §4, §7, §10):
the exact NI sign-flip against brute force, the quote / judged outcome logic on both sides (the
split quote, the content-hash rule, the prior-registry reuse), the hierarchical decision and its
wording, every gate tripping on a forged input, the drift check, the seeds ``build-control
--any-paper`` reads, and the public file's hygiene. Fixtures only; no store, no network."""
from __future__ import annotations

import itertools
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
_EVAL = _HERE.parents[1] / "scripts" / "eval"
for _p in (_HERE, _EVAL, _EVAL / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_study_common as asc  # noqa: E402
import alias_study_judge as aj  # noqa: E402
import reach_fixture as fx  # noqa: E402
import reach_report as rr  # noqa: E402
from reach_fixture import paper, rid, sid, wid  # noqa: E402

DEAD = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def _no_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("QDRANT_URL", "ELASTICSEARCH_URL", "RAGSTACK_BASE_URL"):
        monkeypatch.setenv(k, DEAD)


# ------------------------------------------------------------------ the exact sign-flip

def brute(xs: list[int]) -> float:
    t = sum(xs)
    hits = sum(sum(s * x for s, x in zip(signs, xs, strict=True)) >= t
               for signs in itertools.product((1, -1), repeat=len(xs)))
    return hits / 2 ** len(xs)


def test_sign_flip_p_is_power_py_s_and_exact() -> None:
    rng = random.Random(261109)
    cases = [[3, 3, 3], [0, 0], [5, -5], [12, -100, 4, 4, 8], [7], [-3], [100, 200, -300, 0, 50]]
    cases += [[rng.choice((-1, 1)) * rng.randrange(0, 30) * 4 for _ in range(rng.randrange(1, 12))] for _ in range(40)]
    for xs in cases:
        assert rr.sign_flip_p_exact(xs) == pytest.approx(brute(xs), abs=1e-12), xs
    # the same function power.py's simulation validated, imported from the committed file
    assert rr.load_power().sign_flip_p_exact is not None
    assert rr.POWER_PY.name == "power.py" and rr.POWER_PY.is_file()


def test_ni_test_builds_x_from_cluster_sums_and_is_hierarchical() -> None:
    # (record, paper, s, w): paper A has 3 units, T = -1; paper B 2 units, T = 0; paper C 1 unit, T = +1
    units = [("r1", "A", 0, 1), ("r1", "A", 1, 1), ("r2", "A", 1, 1),
             ("r3", "B", 1, 1), ("r3", "B", 0, 0), ("r4", "C", 1, 0)]
    res = rr.ni_test(units, delta_pts=3)
    x = [100 * -1 + 3 * 3, 100 * 0 + 2 * 3, 100 * 1 + 1 * 3]
    assert res["clusters"] == 3 and res["ni"]["p"] == round(brute(x), 6)
    assert res["ni"]["reject"] is False and res["superiority"] == {"tested": False, "p": None, "reject": False}
    # many papers with no discordance: NI rejects, superiority is tested and does not reject (T = 0)
    flat = [(f"r{i}", f"P{i}", 1, 1) for i in range(6)]
    res = rr.ni_test(flat)
    assert res["ni"]["p"] == round(0.5 ** 6, 6) and res["ni"]["reject"] is True
    assert res["superiority"]["tested"] is True and res["superiority"]["p"] == 1.0
    assert res["superiority"]["reject"] is False
    # S wins in every paper: both reject
    wins = [(f"r{i}", f"P{i}", 1, 0) for i in range(6)]
    res = rr.ni_test(wins)
    assert res["ni"]["reject"] and res["superiority"]["reject"] and res["superiority"]["p"] == round(0.5 ** 6, 6)
    assert rr.cluster_sums([("a", "P", 1, 0), ("b", "P", 0, 1), ("c", "P", 1, 0)]) == [(3, 1)]


# ------------------------------------------------------------------ the world and its scenario

def scenario(w: fx.World) -> dict[str, Any]:
    """(a) g of paper 1, base, default: S serves only the first half of the split quote (S misses
    Q), and the S registry labels that chunk answers (S found J). (b) c of paper 2, neighbour,
    default: S misses, but its top 10 holds S chunk 4, byte-identical to W window 2, which W's
    registry labels answers for that record (content-hash rule: S found J). (c) g of paper 3,
    random1, default: W misses (its gold is out of the top 10), S hits. (d) the non-verbatim
    record x misses on both sides everywhere; every pair it needs is labelled not (its S chunk 2 by
    the content-hash rule: it is byte-identical to W window 1)."""
    g1, c2, g3, x = rid(1, "g"), rid(2, "c"), rid(3, "g"), rid(0, "x")
    w.s_over[(g1, "base", "default")] = [sid(1, 0), sid(1, 3)]
    w.s_over[(c2, "neighbour", "default")] = [sid(2, 4), sid(2, 3)]
    w.w_over[(g3, "random1", "default")] = [wid(3, 2), wid(4, 2)]
    w.write_runs("W")
    w.write_runs("S")
    wl = [fx.label_row(g3, wid(3, 2), 0), fx.label_row(g3, wid(4, 2), 0), fx.label_row(c2, wid(2, 2), 1)]
    wl += [fx.label_row(x, c, 0) for c in w.w_default(x)]
    sl = [fx.label_row(g1, sid(1, 0), 1), fx.label_row(g1, sid(1, 3), 0), fx.label_row(c2, sid(2, 3), 0)]
    # x's S chunk 2 is byte-identical to its W window 1 (judged not): the content-hash rule labels it
    sl += [fx.label_row(x, c, 0) for c in w.s_default(x) if c != sid(0, 2)]
    paths = {"w_final": fx.write_jsonl(w.tmp / "w-final.jsonl", [{"k": "batch|1", "pairs": 3}, *wl]),
             "s_final": fx.write_jsonl(w.tmp / "s-final.jsonl", sl),
             "build": w.build_summary()}
    return paths


def inputs(w: fx.World, paths: dict[str, Any], *, s_final: bool = True, prior: list[Path] | None = None,
           drift: list[Path] | None = None, build: Path | None | bool = True) -> rr.Inputs:
    extra = ["--w-final", str(paths["w_final"])]
    if s_final:
        extra += ["--s-final", str(paths["s_final"])]
    for p in prior or []:
        extra += ["--s-prior-final", str(p)]
    for p in drift or []:
        extra += ["--drift-final", str(p)]
    if build is True:
        extra += ["--build-summary", str(paths["build"])]
    elif build:
        extra += ["--build-summary", str(build)]
    a = parser_args(w.args("report", *extra, "--out", str(w.tmp / "out.json")))
    return rr.load_inputs(a, report=True)


def parser_args(argv: list[str]) -> Any:
    """``main``'s parser, without running the command."""
    import argparse

    captured: dict[str, Any] = {}
    real = argparse.ArgumentParser.parse_args

    def grab(self: argparse.ArgumentParser, args: Any = None, namespace: Any = None) -> Any:
        captured["a"] = real(self, args, namespace)
        raise SystemExit(0)
    argparse.ArgumentParser.parse_args = grab  # type: ignore[method-assign]
    try:
        with pytest.raises(SystemExit):
            rr.main(argv)
    finally:
        argparse.ArgumentParser.parse_args = real  # type: ignore[method-assign]
    return captured["a"]


def report(w: fx.World, inp: rr.Inputs, **kw: Any) -> dict[str, Any]:
    return rr.build_report(inp, expected_records=len(w.records), expected_eligible=len(w.records) - 1, **kw)


def test_quote_and_judged_outcomes(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    inp = inputs(w, paths)
    rec = report(w, inp, token_counter=lambda t: len(t.split()), tokenizer_name="words")
    assert rec["records"] == 13 and rec["q_eligible"] == 12 and rec["papers"] == 6
    q, j = rec["families"]["Q"], rec["families"]["J"]
    # Q: 12 eligible records x 4 corpora; (a) and (b) are W-only, (c) is S-only; the split quote hits elsewhere
    assert q["counts"]["units"] == 48 and q["counts"]["S_only"] == 1 and q["counts"]["W_only"] == 2
    assert q["counts"]["S"]["k"] == 46 and q["counts"]["W"]["k"] == 47 and q["counts"]["D_pts"] == -2.083
    # J: all 13 records; (a) found by the S label, (b) by the content-hash rule, (c) W misses, x never found
    assert j["counts"]["units"] == 52 and j["counts"]["S"]["k"] == 48 and j["counts"]["W"]["k"] == 47
    assert j["counts"]["S_only"] == 1 and j["counts"]["W_only"] == 0
    assert rec["gates"]["common"] == [] and rec["gates"]["J_only"] == []
    src = rec["j_labels"]["s_label_source"]
    assert src["w_content_hash:answers"] == 1 and src["w_content_hash:not"] == 1 and src["new:answers"] == 1
    assert "none:unjudged" not in src
    assert rec["j_labels"]["unjudged"] == {"W": 0, "S": 0}
    # secondary: per corpus, (a) sits in base and (c) in random1; G vs C
    sec = q["secondary"]
    assert sec["by_corpus_default"]["base"]["W_only"] == 1 and sec["by_corpus_default"]["random1"]["S_only"] == 1
    assert sec["by_corpus_default"]["neighbour"]["W_only"] == 1 and sec["by_corpus_default"]["random2"]["units"] == 12
    assert sec["by_kind_primary"]["G"]["units"] == 24 and sec["by_kind_primary"]["C"]["units"] == 24
    assert sec["by_retriever_pooled"]["bm25"]["discordance"] == 0.0 and sec["tested"] is False
    # discordant units by the S coverage of the gold window: g windows are split in two S chunks (< 0.8)
    cov = rec["discordant_by_gold_window_coverage_under_S"]
    assert sum(cov["W_only"].values()) == 2 and sum(cov["S_only"].values()) == 1
    # (a): a g window split over two S chunks; (b): a c window held whole by S chunk 2
    assert cov["W_only"] == {"0.5-0.8": 1, ">=0.8": 1}
    tok = rec["context_tokens"]
    assert tok["computed"] and tok["W"]["by_corpus_default"]["base"]["lists"] == 13
    assert rec["context_tokens"]["S"]["by_retriever_pooled"]["default"]["lists"] == 52


def test_decisions_and_wording(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    rec = report(w, inputs(w, scenario(w)))
    q = rec["families"]["Q"]
    # 6 papers, discordance 3 of 48 units, D < 0: p_NI is far from 0.05 at this size, not shown
    assert q["outcome"] == "ni_not_shown" and q["test"]["ni"]["p"] == 0.453125
    sentence = q["sentence"]
    assert "on 48 paired (record, corpus) units (12 records in 6 papers)" in sentence and "quote@10" in sentence
    assert sentence.startswith("Non-inferiority not shown (Q)") and "not evidence that S is worse" in sentence
    assert "-2.1 points (1 units found under S only, 2 under W only" in sentence
    j = rec["families"]["J"]
    # J: one S-only unit, no W-only: every paper's X_k > 0, p = 1/64; superiority p = 1/2
    assert j["outcome"] == "non_inferior" and j["test"]["ni"]["p"] == 0.015625 and j["test"]["superiority"]["p"] == 0.5
    assert "low observed discordance (1.9 %)" in j["sentence"]
    assert "judge drift lands on S only" in j["sentence"] and "not verbatim" in j["sentence"]
    assert "judge drift check not evaluable" in j["sentence"]
    res = {"counts": rr.paired([(f"r{i}", f"P{i}", 1, 1) for i in range(20)]),
           "test": rr.ni_test([(f"r{i}", f"P{i}", 1, 1) for i in range(20)])}
    out, s = rr.decide("Q", res, [], [])
    assert out == "non_inferior" and s.startswith("Non-inferior (Q)") and "not worse than W by 3 points" in s
    res = {"counts": rr.paired([(f"r{i}", f"P{i}", 1, 0) for i in range(20)]),
           "test": rr.ni_test([(f"r{i}", f"P{i}", 1, 0) for i in range(20)])}
    out, s = rr.decide("J", res, [], ["x"])
    assert out == "superior" and s.startswith("Superior (J)") and "hierarchical" in s and s.endswith("Caveats: x.")
    out, s = rr.decide("J", res, ["S base default: 1 failed call(s)"], [])
    assert out == "gate_not_evaluable" and s.startswith("GATE-NOT-EVALUABLE (J): S base default: 1 failed call(s).")
    assert "decides nothing" in s
    # the size caveat cites power.json's delta = 3 cells
    note = rr.size_note(json.loads(rr.POWER_PY.with_name("power.json").read_text()))
    assert "0.0545 at 10 %" in note and "0.0615 at 5 %" in note and "0.0766" in note


def test_low_discordance_rejection_carries_the_size_caveat(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    w.write_runs("W")
    w.write_runs("S")
    paths = {"w_final": fx.write_jsonl(tmp_path / "w-final.jsonl", [fx.label_row(rid(0, "x"), c, 0)
                                                                     for c in w.w_default(rid(0, "x"))]),
             "s_final": fx.write_jsonl(tmp_path / "s-final.jsonl", [fx.label_row(rid(0, "x"), c, 0)
                                                                     for c in w.s_default(rid(0, "x"))]),
             "build": w.build_summary()}
    inp = inputs(w, paths)
    inp.power_json = json.loads(rr.POWER_PY.with_name("power.json").read_text())
    rec = report(w, inp)
    q = rec["families"]["Q"]
    assert q["counts"]["discordance"] == 0.0 and q["test"]["ni"]["reject"] is True  # 6 papers: p = 1/64
    assert q["outcome"] == "non_inferior" and "low observed discordance" in q["sentence"]
    assert "0.0545" in q["sentence"]


# ------------------------------------------------------------------ gates on forged inputs

def gates_of(rec: dict[str, Any], fam: str = "Q") -> list[str]:
    return rec["families"][fam]["gates"]


def test_retrieval_and_census_gates(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    w.write_runs("S", failed=lambda r, c, ret: r == rid(0, "g") and c == "base" and ret == "bm25",
                 summary_over={"neighbour": {"conditions": {"base": {"gate": "GATE-NOT-EVALUABLE: depth"},
                                                            "bm25": {"gate": None}, "norerank": {"gate": None}}},
                               "random2": {"status": "hard_stop"}})
    rec = report(w, inputs(w, paths))
    g = gates_of(rec)
    assert "S base bm25: 1 failed call(s)" in g and "S neighbour default: a fired gate" in g
    assert "S random2: run status 'hard_stop'" in g
    assert rec["families"]["Q"]["outcome"] == rec["families"]["J"]["outcome"] == "gate_not_evaluable"
    # census: another total in the build summary, and a live census that changed after the run
    w.write_runs("S", summary_over={"base": {"census": {"live_before": {"total": 100, "stamps": {fx.STAMP: 100}},
                                                        "live_after": {"total": 99, "stamps": {fx.STAMP: 99}}}}})
    bs = json.loads(paths["build"].read_text())
    bs["collections"]["random1"]["census"]["total"] = 101
    forged = tmp_path / "build-forged.json"
    forged.write_text(json.dumps(bs))
    rec = report(w, inputs(w, paths, build=forged))
    g = gates_of(rec)
    assert any(x.startswith("census base: differs") and "live_after" in x for x in g)
    assert any(x.startswith("census random1: differs") and "n_chunks" in x for x in g)
    rec = report(w, inputs(w, paths, build=False))
    assert "census: no build summary given" in gates_of(rec)
    # an S run that is not the reach run, a missing row, an unresolved chunk text
    w.s_over[(rid(4, "c"), "random2", "norerank")] = [sid(4, 2), "schunk-not-in-any-export-0000"]
    w.write_runs("S", summary_over={"base": {"study": {"mode": "rechunk", "collection_build": {
        "n_chunks": 100, "load_receipt_sha256": "receipt-base"}}}})
    rows = (w.s_runs / "neighbour" / "none.jsonl").read_text().splitlines()
    (w.s_runs / "neighbour" / "none.jsonl").write_text("\n".join(rows[1:]) + "\n")
    rec = report(w, inputs(w, paths))
    g = gates_of(rec)
    assert "S base: not a reach run (rechunk mode with all_records)" in g
    assert "S neighbour default: 1 record(s) without a row" in g
    assert "S: 1 top-10 chunk id(s) with no text in the side's exports" in g
    # the record count and the eligible count against the prereg's
    rec = rr.build_report(inputs(w, paths))
    g = gates_of(rec)
    assert "records: 13, the prereg's 1091" in g and "Q-eligible records: 12, the prereg's 1089" in g


def test_j_only_gates(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    # no S registry: the S pairs are unjudged (> 5 %), and the registry is missing
    rec = report(w, inputs(w, paths, s_final=False))
    gj = gates_of(rec, "J")
    assert any(x.startswith("S: ") and "unjudged (> 5%)" in x for x in gj) and "S registry: not given" in gj
    assert gates_of(rec, "Q") == [] and rec["families"]["Q"]["outcome"] != "gate_not_evaluable"
    assert rec["families"]["J"]["outcome"] == "gate_not_evaluable"
    # a relabel: the new S registry labels a key a prior registry had already judged
    prior = fx.write_jsonl(tmp_path / "prior.jsonl", [fx.label_row(rid(1, "g"), sid(1, 0), 0)])
    rec = report(w, inputs(w, paths, prior=[prior]))
    assert any("already had a judged label" in x for x in gates_of(rec, "J"))
    # a prior unjudged label may be judged anew (it adds, it does not flip)
    prior_u = fx.write_jsonl(tmp_path / "prior-u.jsonl", [fx.label_row(rid(1, "g"), sid(1, 0), None)])
    rec = report(w, inputs(w, paths, prior=[prior_u]))
    assert gates_of(rec, "J") == []
    # a relabel through the content-hash rule: the S registry judges S chunk 4 of record c2
    rows = paths["s_final"].read_text() + json.dumps(fx.label_row(rid(2, "c"), sid(2, 4), 0)) + "\n"
    forged = tmp_path / "s-final-forged.jsonl"
    forged.write_text(rows)
    rec = report(w, inputs(w, {**paths, "s_final": forged}))
    assert any("already had a judged label" in x for x in gates_of(rec, "J"))
    # conflicting labels within one registry, a forged pair_id, a row without ids
    bad = [fx.label_row(rid(1, "g"), sid(1, 3), 1), fx.label_row(rid(5, "g"), sid(5, 3), 1, pid="0" * 16),
           {"k": "pair|x", "label": 1}]
    forged.write_text(paths["s_final"].read_text() + "".join(json.dumps(r) + "\n" for r in bad))
    rec = report(w, inputs(w, {**paths, "s_final": forged}))
    gj = " | ".join(gates_of(rec, "J"))
    assert "conflicting labels" in gj and "pair_id that is not" in gj and "without record_id/chunk_id" in gj
    assert gates_of(rec, "Q") == []


# ------------------------------------------------------------------ drift

def test_drift_sample_and_agreement(tmp_path: Path) -> None:
    pairs = [(f"r{i // 7}", f"c{i}") for i in range(100)]
    s = rr.drift_sample(pairs)
    assert len(s) == 5 and s == rr.drift_sample(list(reversed(pairs)) + pairs[:3])
    assert s == sorted(s, key=lambda p: asc.seeded_key("261109|reach|drift|W", *p))
    assert len(rr.drift_sample(pairs[:30])) == 2 and len(rr.drift_sample(pairs[:10])) == 1  # 0.5 rounds up
    assert len(rr.drift_sample(pairs[:9])) == 0
    w_need = {p: [] for p in pairs}
    w_labels = {p: ("answers" if i % 2 else "not") for i, p in enumerate(pairs)}
    sample = rr.drift_sample(pairs)
    flips = {p: ("not" if w_labels[p] == "answers" else w_labels[p]) for p in sample}
    reg = rr.Registry(dict(flips), [], len(flips), [])
    d = rr.drift_summary(w_need, w_labels, reg)
    n_ans = sum(w_labels[p] == "answers" for p in sample)
    assert d["sample"] == 5 and d["rejudged"] == 5 and d["evaluable"] is True
    assert n_ans == 2 and d["answers_judged_both"] == 2
    assert d["agreement_on_answers"] == 0.0 and d["caveat"].startswith("judge drift:")
    assert d["by_original_label"]["answers"] == {"not": 2} and d["agreement"] == 0.6
    same = rr.Registry({p: w_labels[p] for p in sample}, [], 5, [])
    d = rr.drift_summary(w_need, w_labels, same)
    assert d["agreement"] == 1.0 and d["agreement_on_answers"] == 1.0 and d["caveat"] is None
    assert rr.drift_summary(w_need, w_labels, None)["caveat"].startswith("judge drift check not evaluable")


# ------------------------------------------------------------------ j-seeds

def test_j_seeds_feed_build_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    # a prior registry judged one S pair (reused by key) and left one unjudged (seeded anew)
    prior = fx.write_jsonl(tmp_path / "prior.jsonl", [fx.label_row(rid(1, "g"), sid(1, 3), 0),
                                                      fx.label_row(rid(0, "x"), sid(0, 3), None)])
    main_pairs = w.main_pairs(skip=[rid(2, "c")])
    monkeypatch.setattr(rr, "DRIFT_SHARE", 0.5)
    out = tmp_path / "private"
    rc = rr.main(w.args("j-seeds", "--w-final", str(paths["w_final"]), "--s-prior-final", str(prior),
                        "--main-pairs", str(main_pairs), "--out-dir", str(out)))
    assert rc == 0
    seeds = asc.read_jsonl(out / "s-seed.jsonl")
    keys = {(s["record_id"], s["chunk_id"]) for s in seeds}
    orph = asc.read_jsonl(out / "orphans.jsonl")
    okeys = {(o["record_id"], o["chunk_id"]) for o in orph if o["stage"] == rr.S_STAGE}
    x = rid(0, "x")
    # needed S pairs: (a) 2, (b) 2, x 3; reused: sid(1,3) by key, sid(2,4) and x's sid(0,2) by the content hash
    assert keys | okeys == {(rid(1, "g"), sid(1, 0)), (x, sid(0, 3)), (x, sid(1, 3)), (rid(2, "c"), sid(2, 3))}
    assert okeys == {(rid(2, "c"), sid(2, 3))}  # its record has no stage-A pair
    summ = json.loads((out / "j-seeds.json").read_text())
    assert summ["s_label_before_judging"] == {"prior_unjudged": 1, "reused_prior": 1, "reused_w_content_hash": 2,
                                              "unlabelled": 3}
    assert summ["s"]["orphans"] == 1 and summ["s"]["seeds"] == 3
    for s in seeds:
        assert s["stage"] == rr.S_STAGE and s["pair_id"] == aj.pair_id(s["record_id"], s["chunk_id"])
        assert s["control_of"] == aj.pair_id(s["record_id"], wid(int(s["record_id"].split("-")[2][4:]), 2))
    # x's sid(1,3) lies in paper 1, not the paper of x's stage-A pair: other paper (needs --any-paper)
    other = {(s["record_id"], s["chunk_id"]): s["same_paper_as_window"] for s in seeds}
    assert other[(x, sid(1, 3))] is False and other[(x, sid(0, 3))] is True
    # the export subset holds exactly the seed chunks, rows unchanged
    exp = asc.read_jsonl(out / "s-export.jsonl")
    assert sorted(r["chunk_id"] for r in exp) == sorted({s["chunk_id"] for s in seeds})
    src = {r["chunk_id"]: r for r in w.s_rows}
    assert all(r == src[r["chunk_id"]] for r in exp)
    # build-control --any-paper takes every seed row
    chunks = asc.read_chunk_exports([w.w_export, out / "s-export.jsonl"])
    arm_of = {r["chunk_id"]: "base" for r in w.w_rows}
    built, counts = aj.build_control_pairs(seeds, asc.read_jsonl(main_pairs), chunks,
                                           {r["chunk_id"] for r in exp}, any_paper=True, arm_of=arm_of)
    assert counts["pairs"] == len(seeds) and counts["passage_paper_not_stage_a_window_paper"] == 1
    # drift: half of the W pairs J uses, a stage-A pair controlling itself where it is one
    dseeds = asc.read_jsonl(out / "drift-seed.jsonl")
    w_used = summ["w_pairs_used"]
    assert summ["drift"]["sample"] == rr.round_half_up(0.5 * w_used) == len(dseeds) + summ["drift"]["orphans"]
    for s in dseeds:
        own = aj.pair_id(s["record_id"], s["chunk_id"])
        if s["chunk_id"] == wid(int(s["record_id"].split("-")[2][4:]), 2):
            assert s["control_of"] == own
    dexp = asc.read_jsonl(out / "drift-export.jsonl")
    assert sorted(r["chunk_id"] for r in dexp) == sorted({s["chunk_id"] for s in dseeds})
    # the summary is counts only
    text = (out / "j-seeds.json").read_text()
    for r in w.records:
        assert r["record_id"] not in text and r["paper"] not in text
    assert "chunk-" not in text and "wchunk" not in text and "schunk" not in text
    # write-once: a second run with other content is refused
    (out / "s-seed.jsonl").write_text("{}\n")
    with pytest.raises(SystemExit, match="exists with other content"):
        rr.main(w.args("j-seeds", "--w-final", str(paths["w_final"]), "--main-pairs", str(main_pairs),
                       "--out-dir", str(out)))


def test_j_seeds_refuse_inside_the_repository(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    with pytest.raises(SystemExit, match="inside the repository"):
        rr.main(w.args("j-seeds", "--w-final", str(paths["w_final"]), "--main-pairs", str(w.main_pairs()),
                       "--out-dir", str(_HERE / "never-here")))
    assert not (_HERE / "never-here").exists()


# ------------------------------------------------------------------ the public file

def test_report_cli_writes_a_public_counts_only_file(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    pre = tmp_path / "PREREG.md"
    pre.write_text("frozen\n")
    out = tmp_path / "reach-report.json"
    rc = rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--s-final", str(paths["s_final"]),
                        "--build-summary", str(paths["build"]), "--tokenizer", "none", "--prereg", str(pre),
                        "--prereg-sha256", asc.sha256_file(pre), "--expected-records", "13",
                        "--expected-eligible", "12", "--out", str(out)))
    assert rc == 0
    rec = json.loads(out.read_text())
    assert rec["schema"] == "reach-report/1" and rec["confirmatory"] is True
    assert set(rec["families"]) == {"Q", "J"} and rec["context_tokens"]["computed"] is False
    assert rec["code_sha256"]["power_py"] == asc.sha256_file(rr.POWER_PY)
    assert "host" not in rec["provenance"]
    text = out.read_text()
    for r in w.records:
        assert r["record_id"] not in text and r["paper"] not in text and r["quote"][:40] not in text
    for row in w.w_rows + w.s_rows:
        assert row["chunk_id"] not in text and row["doc_id"] not in text
    # a wrong prereg sha is refused before anything is read
    with pytest.raises(SystemExit, match="the prereg's sha256"):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--tokenizer", "none", "--prereg", str(pre),
                       "--prereg-sha256", "0" * 64, "--out", str(tmp_path / "x.json")))
    assert not (tmp_path / "x.json").exists()


def test_report_refuses_a_public_file_that_would_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    real = rr.build_report

    def leaky(*a: Any, **kw: Any) -> dict[str, Any]:
        rec = real(*a, **kw)
        rec["oops"] = {"which": rid(3, "g"), "chunk": sid(2, 4), "q": fx.quote(1, "c")}
        return rec
    for key in ("which", "chunk", "q"):
        def one(*a: Any, key: str = key, **kw: Any) -> dict[str, Any]:
            rec = leaky(*a, **kw)
            rec["oops"] = {key: rec["oops"][key]}
            return rec
        monkeypatch.setattr(rr, "build_report", one)
        out = tmp_path / f"leak-{key}.json"
        with pytest.raises(SystemExit, match="REFUSED: the public file"):
            rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--s-final", str(paths["s_final"]),
                           "--tokenizer", "none", "--out", str(out)))
        assert not out.exists()


def test_parse_exports_and_registry_reading(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="ARM=PATH"):
        rr.parse_exports(["nowhere=x.jsonl"])
    with pytest.raises(SystemExit, match="ARM=PATH"):
        rr.parse_exports(["x.jsonl"])
    p = fx.write_jsonl(tmp_path / "f.jsonl", [{"k": "batch|1"}, fx.label_row("r-000000001", "chunk-a-0000", 1),
                                             fx.label_row("r-000000001", "chunk-a-0000", 1),
                                             fx.label_row("r-000000001", "chunk-b-0000", None)])
    reg = rr.read_registry([p], "T")
    assert reg.labels == {("r-000000001", "chunk-a-0000"): "answers", ("r-000000001", "chunk-b-0000"): "unjudged"}
    assert reg.problems == [] and reg.rows == 3
    # one chunk id with two contents on a side is refused
    rows = [fx.World.row("chunk-dup-0000", paper(0), "one", 0), fx.World.row("chunk-dup-0000", paper(0), "two", 0)]
    e = fx.write_jsonl(tmp_path / "e.jsonl", rows)
    with pytest.raises(SystemExit, match="other content"):
        rr.read_export({"base": [e]}, {"chunk-dup-0000"})


def test_report_reads_the_drift_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The drift sample the report recomputes is the one j-seeds wrote; agreeing re-judged labels
    lift the J caveat, flipped answers labels raise it."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    monkeypatch.setattr(rr, "DRIFT_SHARE", 0.5)
    inp = inputs(w, paths)
    w_need = rr.needed_pairs(rr.score_lists(w.records, inp.w_runs, inp.w_export.chunks))
    sample = rr.drift_sample(w_need)
    assert len(sample) == rr.round_half_up(0.5 * len(w_need)) > 0
    orig = inp.w_reg.labels
    same = fx.write_jsonl(tmp_path / "drift-same.jsonl", [fx.label_row(r, c, int(orig[(r, c)] == "answers"))
                                                          for r, c in sample])
    rec = report(w, inputs(w, paths, drift=[same]))
    d = rec["drift_check"]
    assert d["evaluable"] and d["rejudged"] == len(sample) and d["agreement"] == 1.0
    # every W pair J uses is labelled not in this world: the answers agreement cannot be computed
    assert d["answers_judged_both"] == 0 and d["caveat"].endswith("(no original answers label re-judged)")
    assert d["caveat"] in rec["families"]["J"]["sentence"]
    assert d["labels_outside_sample"] == 0 and rec["gates"]["J_only"] == []
    # an original answers label in the sample, re-judged not: the drift caveat
    first = sample[0]
    wf = paths["w_final"].read_text().replace(json.dumps(fx.label_row(*first, 0)), json.dumps(fx.label_row(*first, 1)))
    w2 = tmp_path / "w-final-2.jsonl"
    w2.write_text(wf)
    flipped = fx.write_jsonl(tmp_path / "drift-flip.jsonl", [fx.label_row(r, c, 0) for r, c in sample])
    rec = report(w, inputs(w, {**paths, "w_final": w2}, drift=[flipped]))
    d = rec["drift_check"]
    assert d["answers_judged_both"] == 1 and d["agreement_on_answers"] == 0.0
    assert d["caveat"].startswith("judge drift: the re-judged W answers labels agree") and d["caveat"] in rec[
        "families"]["J"]["sentence"]
