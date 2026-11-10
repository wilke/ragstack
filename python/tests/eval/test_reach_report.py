"""The reach comparison's report and J builder (``reach_report.py``; PREREG §3, §4, §7, §10):
the exact NI sign-flip against brute force, the quote / judged outcome logic on both sides (the
split quote, the content-hash rule, the prior-registry reuse), Q's hierarchical decision and its
wording, J's descriptive sentence (owner decision 2026-11-10: J is never decided), every gate
tripping on a forged input (a gate on Q, a caveat on J), the drift check, the seeds ``build-control
--any-paper`` reads, and the public file's hygiene. Fixtures only; no store, no network."""
from __future__ import annotations

import itertools
import json
import random
import sys
from collections.abc import Sequence
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


def pre_args() -> list[str]:
    """``report``'s required prereg pair: this checkout's committed PREREG.md and its sha."""
    return ["--prereg", str(rr.PREREG_FILE), "--prereg-sha256", asc.sha256_file(rr.PREREG_FILE)]


@pytest.fixture(autouse=True)
def _no_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("QDRANT_URL", "ELASTICSEARCH_URL", "RAGSTACK_BASE_URL"):
        monkeypatch.setenv(k, DEAD)


#: the real §9.1 check; the fixture world's inputs are not the frozen files, so the CLI tests run it
#: against a table of the world's own shas (:func:`_frozen_as_given`), and the tests below run it as is
REAL_FROZEN_CHECK = rr.check_frozen_inputs


def freeze_table(by_flag: dict[str, list[str]]) -> str:
    """A §9.1 section listing ``by_flag``'s shas (one row per file; a flag's later rows blank)."""
    lines = ["### 9.1 The freeze set", "", "| input | file | sha256 |", "|---|---|---|",
             f"| S run config | `reach-config.json` | `{'0' * 64}` |"]
    for flag, shas in by_flag.items():
        for i, sha in enumerate(shas):
            lines.append(f"| {'x (`' + flag + '`)' if i == 0 else ''} | `dir/f{i}` | `{sha}` |")
    return "\n".join(["# t", "", *lines, "", "### 9.2 Freeze procedure", ""])


@pytest.fixture(autouse=True)
def _frozen_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    def as_given(inputs: Any, frozen: Any, text: str) -> dict[str, Any]:
        t = freeze_table(rr.report_input_shas(inputs))
        return REAL_FROZEN_CHECK(inputs, rr.frozen_inputs(t), t)
    monkeypatch.setattr(rr, "check_frozen_inputs", as_given)


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


def prior_pairs_of(w: fx.World, prior: Path) -> Path:
    """A prior registry's pairs.jsonl beside it: each row's content_sha256 is the S export's text."""
    text = {r["chunk_id"]: r["content"] for r in w.s_rows}
    rows = [{"pair_id": r["pair_id"], "record_id": r["record_id"], "chunk_id": r["chunk_id"],
             "content_sha256": asc.sha_text(text[r["chunk_id"]])} for r in asc.read_jsonl(prior)]
    return fx.write_jsonl(prior.with_name(prior.stem + "-pairs.jsonl"), rows)


def inputs(w: fx.World, paths: dict[str, Any], *, s_final: bool = True, prior: list[Path] | None = None,
           drift: list[Path] | None = None, build: Path | None | bool = True, main_skip: tuple[str, ...] = (),
           prior_pairs: list[Path] | None = None) -> rr.Inputs:
    extra = ["--w-final", str(paths["w_final"]), "--main-pairs", str(w.main_pairs(skip=main_skip)), *pre_args(),
             "--j-seeds", str(w.tmp / "j-seeds-unused.json")]
    if prior and prior_pairs is None:
        prior_pairs = [prior_pairs_of(w, p) for p in prior]
    for p in prior_pairs or []:
        extra += ["--s-prior-pairs", str(p)]
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


def report(w: fx.World, inp: rr.Inputs, eligible: int | None = None, **kw: Any) -> dict[str, Any]:
    """``build_report`` with the prereg's counts set to the world's (13 records, 12 eligible)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rr, "EXPECTED_RECORDS", len(w.records))
        mp.setattr(rr, "EXPECTED_ELIGIBLE", len(w.records) - 1 if eligible is None else eligible)
        return rr.build_report(inp, **kw)


def j_seeds(w: fx.World, paths: dict[str, Any], out: Path, *extra: str) -> Path:
    """Run ``j-seeds`` (private outputs in ``out``) and return its ``j-seeds.json``."""
    assert rr.main(w.args("j-seeds", "--w-final", str(paths["w_final"]), "--main-pairs", str(w.main_pairs()),
                          *extra, "--out-dir", str(out))) == 0
    return out / "j-seeds.json"


def test_quote_and_judged_outcomes(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    inp = inputs(w, paths)
    rec = report(w, inp, token_counter=lambda t: len(t.split()), tokenizer_name="words")
    assert rec["records"] == 13 and rec["q_eligible"] == 12 and rec["papers"] == 6
    # every W-verbatim quote is S-reachable; the g quotes only across the boundary of S chunks 0 and 1
    assert rec["q_eligibility"]["w_verbatim"] == 12
    assert rec["q_eligibility"]["s_reach_of_w_verbatim"] == {"single": 6, "split": 6}
    assert rec["retrieval_settings"]["per_corpus"]["base"] == {
        "identical": True, "differs": [], "server_version": {"W": "1.6.22", "S": "1.6.30"}}
    assert "collection" in rec["retrieval_settings"]["expected_differences"]
    q, j = rec["families"]["Q"], rec["descriptive"]["J"]
    # Q: 12 eligible records x 4 corpora; (a) and (b) are W-only, (c) is S-only; the split quote hits elsewhere
    assert q["counts"]["units"] == 48 and q["counts"]["S_only"] == 1 and q["counts"]["W_only"] == 2
    assert q["counts"]["S"]["k"] == 46 and q["counts"]["W"]["k"] == 47 and q["counts"]["D_pts"] == -2.083
    # J: all 13 records; (a) found by the S label, (b) by the content-hash rule, (c) W misses, x never found
    assert j["counts"]["units"] == 52 and j["counts"]["S"]["k"] == 48 and j["counts"]["W"]["k"] == 47
    assert j["counts"]["S_only"] == 1 and j["counts"]["W_only"] == 0
    assert rec["gates"] == {"Q": []} and j["caveat_sources"] == {
        "retrieval_and_inputs": [], "unjudged_primary": [], "label_integrity": [], "label_reuse": []}
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


def test_q_eligibility_needs_s_reachability(tmp_path: Path) -> None:
    """PREREG §2: a quote verbatim in its W gold window but not reachable under S (quote_cover over
    every S chunk of the gold document < 0.90) is not Q-eligible; a quote reachable only across an S
    chunk boundary (split) is."""
    w = fx.World(tmp_path)
    c5 = rid(5, "c")
    # S chunk 2 of paper 5 loses the c quote: no S chunk of the document holds it any more
    for r in w.s_rows:
        if r["chunk_id"] == sid(5, 2):
            r["content"] = f"Methods of {paper(5)}. Nothing quoted here. End."
    fx.write_jsonl(w.s_export, w.s_rows)
    paths = scenario(w)
    inp = inputs(w, paths)
    reach = rr.s_reach(w.records, inp.w_export.chunks, inp.s_export.doc_texts)
    assert reach[c5] == "unreachable" and reach[rid(5, "g")] == "split" and reach[rid(4, "c")] == "single"
    assert c5 not in rr.eligible_records(w.records, inp.w_export.chunks, inp.s_export.doc_texts)
    rec = report(w, inp, eligible=11)
    assert rec["q_eligible"] == 11 and rec["q_eligibility"]["w_verbatim"] == 12
    assert rec["q_eligibility"]["s_reach_of_w_verbatim"] == {"single": 5, "split": 6, "unreachable": 1}
    assert rec["families"]["Q"]["counts"]["records"] == 11 and rec["descriptive"]["J"]["counts"]["records"] == 13
    assert rec["gates"]["Q"] == []
    # against the prereg's eligible count the difference is a gate
    assert "Q-eligible records: 11, the prereg's 12" in report(w, inp)["gates"]["Q"]
    # a gold document with no S chunk at all is unreachable
    assert set(rr.s_reach(w.records, inp.w_export.chunks, {}).values()) == {"unreachable"}


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
    # J is descriptive (owner decision 2026-11-10): one S-only unit, no W-only, but no test, no outcome, no gate
    assert set(rec["families"]) == {"Q"} and set(rec["descriptive"]) == {"J"}
    j = rec["descriptive"]["J"]
    assert j["tested"] is False and not {"test", "outcome", "gates"} & set(j)
    assert j["counts"]["S_only"] == 1 and j["counts"]["W_only"] == 0
    js = j["sentence"]
    assert js.startswith("Descriptive (J, not tested): under the default retriever pooled over the four corpus "
                         "collections, judged found@10 (quote@10, or a top-10 chunk the frozen alias judge labelled "
                         "answers) was ")
    assert "No test was computed for J; this sentence decides nothing." in js
    assert "Favourable bound: with those 0 S pair(s) scored answers, J would be " in js
    for word in ("Superior", "Non-inferior", "Non-inferiority", "GATE-NOT-EVALUABLE", "p = ", "δ", "α"):
        assert word not in js
    assert "low observed discordance" not in js
    assert "judge drift lands on S only" in js and "J includes the 1 record(s) that are not Q-eligible (§2)" in js
    assert "judge drift check not evaluable" in js
    # the fixed text of J's descriptive sentence is PREREG §7.2's
    prereg = rr.PREREG_FILE.read_text(encoding="utf-8")
    for frag in ("Descriptive (J, not tested): under the default retriever pooled over the four corpus collections, ",
                 "Of the pairs this cell needs, ", " carry no judged label (S: ",
                 "Favourable bound: with those ", " S pair(s) scored answers, J would be ",
                 "No test was computed for J; this sentence decides nothing."):
        assert frag in js and frag in prereg
    res = {"counts": rr.paired([(f"r{i}", f"P{i}", 1, 1) for i in range(20)]),
           "test": rr.ni_test([(f"r{i}", f"P{i}", 1, 1) for i in range(20)])}
    out, s = rr.decide("Q", res, [], [])
    assert out == "non_inferior" and s.startswith("Non-inferior (Q)") and "not worse than W by 3 points" in s
    assert "Non-inferiority at δ = 3 points holds" in s and "delta" not in s
    # the fixed text of the frozen sentence is PREREG §7.2's, with its Greek letters
    prereg = rr.PREREG_FILE.read_text(encoding="utf-8")
    for frag in ("Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = ",
                 "): S is not worse than W by 3 points or more. Superiority is not shown (p = "):
        assert frag in s and frag in prereg
    assert "under the default retriever pooled over the four corpus collections, " in prereg
    for frag in (". The exact one-sided sign-flip over papers gives p = ", ": a loss of 3 points or more under S is "
                 "not ruled out by this test (this is not evidence that S is worse)."):
        assert frag in sentence and frag in prereg
    res = {"counts": rr.paired([(f"r{i}", f"P{i}", 1, 0) for i in range(20)]),
           "test": rr.ni_test([(f"r{i}", f"P{i}", 1, 0) for i in range(20)])}
    out, s = rr.decide("Q", res, [], ["x"])
    assert out == "superior" and s.startswith("Superior (Q)") and "hierarchical" in s and s.endswith("Caveats: x.")
    assert "; hierarchical, no extra α)." in s and "; hierarchical, no extra α)." in prereg and "alpha" not in s
    out, s = rr.decide("Q", res, ["S base default: 1 failed call(s)"], [])
    assert out == "gate_not_evaluable" and s.startswith("GATE-NOT-EVALUABLE (Q): S base default: 1 failed call(s).")
    assert "decides nothing" in s
    # decide() never produces a J sentence: J is descriptive
    with pytest.raises(ValueError, match="J is descriptive"):
        rr.decide("J", res, [], [])
    # the size caveat cites power.json's δ = 3 cells
    note = rr.size_note(json.loads(rr.POWER_PY.with_name("power.json").read_text()))
    assert "0.0545 at 10 %" in note and "0.0615 at 5 %" in note and "0.0766" in note
    assert note.startswith("simulated size at δ = 3:")


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
    assert "0.0545" in q["sentence"] and "only asymptotically α" in q["sentence"]


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
    assert rec["families"]["Q"]["outcome"] == "gate_not_evaluable"
    # on J the same failures are caveats on its descriptive figures, never an outcome
    j = rec["descriptive"]["J"]
    assert "outcome" not in j and j["caveat_sources"]["retrieval_and_inputs"] == g
    assert all(x in j["caveats"] for x in g) and "S base bm25: 1 failed call(s)" in j["sentence"]
    assert not j["sentence"].startswith("GATE-NOT-EVALUABLE")
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
    # the record count and the eligible count against the prereg's (no override)
    rec = rr.build_report(inputs(w, paths))
    g = gates_of(rec)
    assert "records: 13, the prereg's 1091" in g and "Q-eligible records: 12, the prereg's 1089" in g


def test_retrieval_settings_gate(tmp_path: Path) -> None:
    """PREREG §7.1 (operator default): the S run summaries against the W run summaries; the
    collection names (and builds, versions) differ by design and are declared, not gated."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    rec = report(w, inputs(w, paths))
    assert rec["gates"]["Q"] == []
    req = fx.request("default")
    w.write_runs("S", summary_over={
        "base": {"conditions": {"base": {"gate": None, "request": {**req, "top_k": 20}, "requested": 50},
                                "bm25": {"gate": None, "request": fx.request("bm25"), "requested": 50},
                                "norerank": {"gate": None, "request": fx.request("norerank")}}},
        "neighbour": {"server_assumed": {"retrieval_candidate_multiplier": 3, "rrf_k": 60}},
        "random1": {"study": {"mode": "rechunk", "rechunk_scope": "all_records", "retrievers": list(asc.RETRIEVERS),
                              "request_order": "record file order", "record_set": {"accepted_sha256": "accepted-sha"},
                              "config_sha256_start": "d1" * 32, "config_sha256_end": "d2" * 32,
                              "collection_build": {"n_chunks": 100, "load_receipt_sha256": "receipt-random1"}}}})
    g = gates_of(report(w, inputs(w, paths)))
    assert "settings base: the S run differs from the W run (default request, norerank requested)" in g
    assert "settings neighbour: the S run differs from the W run (server_assumed)" in g
    assert ("settings random1: the S run differs from the W run (server config hash (S at start vs W at start), "
            "server config hash changed within the S run)") in g
    assert not any(x.startswith("settings random2") for x in g)
    assert rr.settings_gates(rr.read_runs(w.w_runs), rr.read_runs(w.w_runs))[0] == []


def j_caveats(rec: dict[str, Any], source: str | None = None) -> list[str]:
    """J's caveats (all, or those from one former-gate source: ``retrieval_and_inputs``,
    ``unjudged_primary``, ``label_integrity``)."""
    j = rec["descriptive"]["J"]
    return j["caveats"] if source is None else j["caveat_sources"][source]


def assert_j_descriptive(rec: dict[str, Any], caveats: Sequence[str]) -> None:
    """J carries the caveats in its list and its sentence, and never an outcome, test or gate."""
    j = rec["descriptive"]["J"]
    assert not {"outcome", "test", "gates"} & set(j) and j["tested"] is False
    assert j["sentence"].startswith("Descriptive (J, not tested): ")
    for c in caveats:
        assert c in j["caveats"] and c in j["sentence"]


def test_j_integrity_problems_are_caveats_not_gates(tmp_path: Path) -> None:
    """Owner decision 2026-11-10 (PREREG §7.1): what were J's gates (unjudged above 5 % in the
    primary cell, label integrity) are caveats on J's descriptive figures; they never gate Q."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    # no S registry: the S pairs are unjudged (> 5 %), and the registry is missing
    rec = report(w, inputs(w, paths, s_final=False))
    unj, integ = j_caveats(rec, "unjudged_primary"), j_caveats(rec, "label_integrity")
    assert len(unj) == 1 and unj[0].startswith("S: ") and "unjudged (> 5%)" in unj[0]
    assert integ == ["S registry: not given"]
    assert_j_descriptive(rec, unj + integ)
    assert gates_of(rec, "Q") == [] and rec["families"]["Q"]["outcome"] != "gate_not_evaluable"
    assert rec["j_labels"]["unjudged_caveat"]["gate"] is False
    # a relabel: the new S registry labels a key a prior registry had already judged
    prior = fx.write_jsonl(tmp_path / "prior.jsonl", [fx.label_row(rid(1, "g"), sid(1, 0), 0)])
    rec = report(w, inputs(w, paths, prior=[prior]))
    integ = j_caveats(rec, "label_integrity")
    assert any("already had a judged label" in x for x in integ)
    assert_j_descriptive(rec, integ)
    assert gates_of(rec, "Q") == []
    # a prior unjudged label may be judged anew (it adds, it does not flip)
    prior_u = fx.write_jsonl(tmp_path / "prior-u.jsonl", [fx.label_row(rid(1, "g"), sid(1, 0), None)])
    rec = report(w, inputs(w, paths, prior=[prior_u]))
    assert j_caveats(rec, "label_integrity") == [] and j_caveats(rec, "unjudged_primary") == []
    # a relabel through the content-hash rule: the S registry judges S chunk 4 of record c2
    rows = paths["s_final"].read_text() + json.dumps(fx.label_row(rid(2, "c"), sid(2, 4), 0)) + "\n"
    forged = tmp_path / "s-final-forged.jsonl"
    forged.write_text(rows)
    rec = report(w, inputs(w, {**paths, "s_final": forged}))
    assert any("already had a judged label" in x for x in j_caveats(rec, "label_integrity"))
    # conflicting labels within one registry, a forged pair_id, a row without ids
    bad = [fx.label_row(rid(1, "g"), sid(1, 3), 1), fx.label_row(rid(5, "g"), sid(5, 3), 1, pid="0" * 16),
           {"k": "pair|x", "label": 1}]
    forged.write_text(paths["s_final"].read_text() + "".join(json.dumps(r) + "\n" for r in bad))
    rec = report(w, inputs(w, {**paths, "s_final": forged}))
    integ = j_caveats(rec, "label_integrity")
    gj = " | ".join(integ)
    assert "conflicting labels" in gj and "pair_id that is not" in gj and "without record_id/chunk_id" in gj
    assert_j_descriptive(rec, integ)
    assert gates_of(rec, "Q") == []


# ------------------------------------------------------------------ drift

def qwen_row(pid: str, *, prompt: str = "p", system: str = "s", reasoning: str | None = None) -> dict[str, Any]:
    """A Qwen label row (the harness's fields the drift check reads); the reasoning defaults to a
    distinct >= 200-character text per call site."""
    text = reasoning if reasoning is not None else f"reasoning {pid} {random.random()} " + "x" * 220
    return {"k": f"q|{pid}", "pair_id": pid, "prompt_sha256": prompt, "system_sha256": system,
            "reasoning_sample": [text], "texts_sample": ["t"], "ct": 10, "verdicts": [1]}


def qwen_rows(pairs: Any, **kw: Any) -> dict[str, dict[str, dict[str, Any]]]:
    return {be: {aj.pair_id(r, c): qwen_row(aj.pair_id(r, c), **kw) for r, c in pairs} for be in ("C", "E")}


def test_drift_sample_and_agreement(tmp_path: Path) -> None:
    pairs = [(f"r{i // 7}", f"c{i}") for i in range(100)]
    s = rr.drift_sample(pairs)
    assert len(s) == 5 and s == rr.drift_sample(list(reversed(pairs)) + pairs[:3])
    assert s == sorted(s, key=lambda p: asc.seeded_key("261109|reach|drift|W", *p))
    assert len(rr.drift_sample(pairs[:30])) == 2 and len(rr.drift_sample(pairs[:10])) == 1  # 0.5 rounds up
    assert len(rr.drift_sample(pairs[:9])) == 0
    # PREREG §3: 5 % of 12,310 is 615.5, which rounds half up to 616
    assert rr.round_half_up(0.05 * 12310) == 616
    w_need = {p: [] for p in pairs}
    w_labels = {p: ("answers" if i % 2 else "not") for i, p in enumerate(pairs)}
    sample = rr.drift_sample(pairs)
    flips = {p: ("not" if w_labels[p] == "answers" else w_labels[p]) for p in sample}
    reg = rr.Registry(dict(flips), [], len(flips), [])
    d = rr.drift_summary(w_need, w_labels, reg, qwen_rows(sample), qwen_rows(sample))
    n_ans = sum(w_labels[p] == "answers" for p in sample)
    assert d["sample"] == 5 and d["rejudged"] == 5 and d["evaluable"] is True
    assert d["prompt_identity"] == {"status": "identical", "compared_rows": 10,
                                    "differs": {"C": {"prompt": 0, "system": 0}, "E": {"prompt": 0, "system": 0}}}
    assert d["cache_check"]["flag"] is False
    assert n_ans == 2 and d["answers_judged_both"] == 2
    assert d["agreement_on_answers"] == 0.0 and d["caveat"].startswith("judge drift:")
    assert d["by_original_label"]["answers"] == {"not": 2} and d["agreement"] == 0.6
    same = rr.Registry({p: w_labels[p] for p in sample}, [], 5, [])
    d = rr.drift_summary(w_need, w_labels, same, qwen_rows(sample), qwen_rows(sample))
    assert d["agreement"] == 1.0 and d["agreement_on_answers"] == 1.0 and d["caveat"] is None
    assert rr.drift_summary(w_need, w_labels, None)["caveat"].startswith("judge drift check not evaluable")
    # without label rows the prompt identity cannot be verified: not evaluable
    d = rr.drift_summary(w_need, w_labels, same)
    assert d["evaluable"] is False and d["prompt_identity"]["status"] == "absent"
    assert d["caveat"] == "judge drift check not evaluable (no label rows given)"


def test_drift_prompt_identity_and_cache_check() -> None:
    """The alias-confirm §6.9 rule, reused on the drift re-judge: a prompt or system sha that differs
    from the original row makes the drift check not evaluable; a byte-identical reasoning text flags
    cache suspected; reasoning too short on every pair reads cache check not evaluable."""
    pairs = [(f"r{i // 7}", f"c{i}") for i in range(100)]
    w_need = {p: [] for p in pairs}
    w_labels = dict.fromkeys(pairs, "answers")
    sample = rr.drift_sample(pairs)
    same = rr.Registry(dict.fromkeys(sample, "answers"), [], 5, [])
    orig = qwen_rows(sample)
    # one re-judged row with another prompt: not evaluable, whatever the agreement
    other = qwen_rows(sample)
    other["C"][aj.pair_id(*sample[0])]["prompt_sha256"] = "other"
    d = rr.drift_summary(w_need, w_labels, same, orig, other)
    assert d["evaluable"] is False and d["prompt_identity"]["status"] == "mismatch"
    assert d["prompt_identity"]["differs"]["C"] == {"prompt": 1, "system": 0}
    assert d["caveat"].startswith("judge drift check not evaluable (the re-judge prompt differs from the original on 1")
    other = qwen_rows(sample)
    other["E"][aj.pair_id(*sample[1])]["system_sha256"] = "other"
    assert rr.drift_summary(w_need, w_labels, same, orig, other)["prompt_identity"]["status"] == "mismatch"
    # no original row of any re-judged pair id: not evaluable
    d = rr.drift_summary(w_need, w_labels, same, {"C": {}, "E": {}}, other)
    assert d["prompt_identity"]["status"] == "no_compared_rows" and d["evaluable"] is False
    # a replayed reasoning text on one pair: cache suspected (a caveat; the check stays evaluable)
    replay = qwen_rows(sample)
    pid = aj.pair_id(*sample[2])
    replay["E"][pid]["reasoning_sample"] = list(orig["E"][pid]["reasoning_sample"])
    d = rr.drift_summary(w_need, w_labels, same, orig, replay)
    assert d["evaluable"] is True and d["cache_check"]["flag"] is True
    assert d["caveat"] == "cache suspected: 1 re-judged (backend, pair) row(s) with Qwen reasoning byte-identical to " \
                         "the original's"
    # identical but short reasoning (< 200 characters) is not eligible: cache check not evaluable
    short = qwen_rows(sample, reasoning="short")
    d = rr.drift_summary(w_need, w_labels, same, short, qwen_rows(sample, reasoning="short"))
    assert d["cache_check"]["flag"] == "not_evaluable" and d["evaluable"] is True
    assert d["caveat"].startswith("cache check not evaluable")


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
                        "--s-prior-pairs", str(prior_pairs_of(w, prior)), "--main-pairs", str(main_pairs),
                        "--out-dir", str(out)))
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
    detail = json.loads((out / "j-seeds-detail.json").read_text())
    assert detail["s_label_before_judging"] == {"prior_unjudged": 1, "reused_prior": 1, "reused_w_content_hash": 2,
                                                "unlabelled": 3}
    assert detail["s_prior_content_check"] == {"given": True, "reused_keys_checked": 1,
                                               "reused_keys_without_pairs_row": 0, "not_reused_content_mismatch": 0,
                                               "not_reused_no_s_text": 0}
    assert detail["not_seeded"] == summ["not_seeded"] == {"prior_content_mismatch": 0, "s_no_export_text": 0,
                                                          "drift_no_w_export_text": 0}
    # PREREG §6 step 3: the public file carries totals per registry only; the breakdowns are private
    assert summ["registries"]["S"] == {"seeds": 3, "orphans": 1} == {k: detail["s"][k] for k in ("seeds", "orphans")}
    for k in ("s_pairs_needed_by_retriever", "s_label_before_judging", "s", "drift", "w_pairs_used", "s_pairs_needed"):
        assert k not in summ and k in detail
    assert "seeds_by_corpus_arm" in detail["s"] and "seeds_by_corpus_arm" not in json.dumps(summ)
    assert summ["outputs_sha256"]["detail"] == asc.sha256_file(out / "j-seeds-detail.json")
    assert summ["s_prior_cross_registry_conflicts"] == 0
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
    w_used = detail["w_pairs_used"]
    dr = summ["registries"]["drift"]
    assert dr["sample"] == rr.round_half_up(0.5 * w_used) == len(dseeds) + dr["orphans"] == dr["seeds"] + dr["orphans"]
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
    out = tmp_path / "reach-report.json"
    js = j_seeds(w, paths, tmp_path / "private")
    rc = rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--s-final", str(paths["s_final"]),
                        "--main-pairs", str(w.main_pairs()), "--build-summary", str(paths["build"]),
                        "--tokenizer", "none", *pre_args(), "--j-seeds", str(js), "--out", str(out)))
    assert rc == 0
    rec = json.loads(out.read_text())
    assert rec["schema"] == "reach-report/1" and rec["confirmatory"] is True
    assert rec["j_seeds_check"] == {"sha256": asc.sha256_file(js), "inputs_match": True}
    assert rec["inputs_sha256"]["j_seeds"] == asc.sha256_file(js)
    # the prereg's counts are not overridable: this 13-record world fails the record-count gate
    assert "records: 13, the prereg's 1091" in rec["gates"]["Q"]
    # PREREG §9.1: a report whose inputs differ from those j-seeds.json was computed from is refused
    js2 = j_seeds(w, paths, tmp_path / "private-2", "--s-prior-final", str(paths["s_final"]), "--s-prior-pairs",
                  str(prior_pairs_of(w, paths["s_final"])))
    with pytest.raises(SystemExit, match=r"inputs differ from those j-seeds.json was computed from \(s_prior_final, "
                                         r"s_prior_pairs\)"):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--s-final", str(paths["s_final"]),
                       "--main-pairs", str(w.main_pairs()), "--build-summary", str(paths["build"]), "--tokenizer",
                       "none", *pre_args(), "--j-seeds", str(js2), "--out", str(tmp_path / "x.json")))
    with pytest.raises(SystemExit, match="not a reach-j-seeds/1 file"):
        rr.check_j_seeds(paths["build"], {})
    assert rec["prereg"]["sha256"] == asc.sha256_file(rr.PREREG_FILE)
    assert set(rec["families"]) == {"Q"} and set(rec["descriptive"]) == {"J"}
    assert rec["context_tokens"]["computed"] is False
    assert rec["code_sha256"]["power_py"] == asc.sha256_file(rr.POWER_PY)
    assert "host" not in rec["provenance"]
    text = out.read_text()
    for r in w.records:
        assert r["record_id"] not in text and r["paper"] not in text and r["quote"][:40] not in text
    for row in w.w_rows + w.s_rows:
        assert row["chunk_id"] not in text and row["doc_id"] not in text
    # a wrong prereg sha is refused before anything is read
    mp = str(w.main_pairs())
    with pytest.raises(SystemExit, match="the prereg's sha256"):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--main-pairs", mp, "--tokenizer", "none",
                       "--prereg", str(rr.PREREG_FILE), "--prereg-sha256", "0" * 64, "--j-seeds", str(js),
                       "--out", str(tmp_path / "x.json")))
    # another file with its own matching sha is refused: the sha must be the committed PREREG.md's
    pre = tmp_path / "PREREG.md"
    pre.write_text("another text\n")
    with pytest.raises(SystemExit, match="not the committed PREREG.md"):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--main-pairs", mp, "--tokenizer", "none",
                       "--prereg", str(pre), "--prereg-sha256", asc.sha256_file(pre), "--j-seeds", str(js),
                       "--out", str(tmp_path / "x.json")))
    with pytest.raises(SystemExit, match="64 lower-case hex"):
        rr.check_prereg(pre, "ABC")
    with pytest.raises(SystemExit, match="needs --prereg"):
        rr.check_prereg(None, None)
    # both are required on the command line, and so is --j-seeds (argparse refuses before anything runs)
    with pytest.raises(SystemExit):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--main-pairs", mp, "--tokenizer", "none",
                       "--j-seeds", str(js), "--out", str(tmp_path / "x.json")))
    with pytest.raises(SystemExit):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--main-pairs", mp, "--tokenizer", "none",
                       *pre_args(), "--out", str(tmp_path / "x.json")))
    with pytest.raises(SystemExit, match="needs --j-seeds"):
        rr.check_j_seeds(None, {})
    assert not (tmp_path / "x.json").exists()


def test_report_refuses_a_public_file_that_would_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    js = j_seeds(w, paths, tmp_path / "private")
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
                           "--main-pairs", str(w.main_pairs()), "--tokenizer", "none", *pre_args(), "--j-seeds",
                           str(js), "--out", str(out)))
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
    assert reg.problems == [] and reg.rows == 3 and reg.per_registry == [{"rows": 3, "keys": 2}]
    # PREREG §3: conflicts are checked per registry; across registries answers-if-any, counted, not gated
    q = fx.write_jsonl(tmp_path / "g.jsonl", [fx.label_row("r-000000001", "chunk-a-0000", 0),
                                             fx.label_row("r-000000001", "chunk-b-0000", 0),
                                             fx.label_row("r-000000001", "chunk-c-0000", 0)])
    reg = rr.read_registry([p, q], "T")
    assert reg.problems == [] and reg.cross_conflicts == 1 and reg.rows == 6
    assert reg.labels[("r-000000001", "chunk-a-0000")] == "answers"
    assert reg.labels[("r-000000001", "chunk-b-0000")] == "not" and reg.labels[("r-000000001", "chunk-c-0000")] == "not"
    assert rr.read_registry([q, p], "T").labels == reg.labels
    within = fx.write_jsonl(tmp_path / "h.jsonl", [fx.label_row("r-000000001", "chunk-a-0000", 0),
                                                  fx.label_row("r-000000001", "chunk-a-0000", 1)])
    reg = rr.read_registry([p, within], "T")
    assert reg.problems == ["T: one (record, chunk) with conflicting labels within one registry (1)"]
    assert reg.labels[("r-000000001", "chunk-a-0000")] == "answers"
    # one chunk id with two contents on a side is refused
    rows = [fx.World.row("chunk-dup-0000", paper(0), "one", 0), fx.World.row("chunk-dup-0000", paper(0), "two", 0)]
    e = fx.write_jsonl(tmp_path / "e.jsonl", rows)
    with pytest.raises(SystemExit, match="other content"):
        rr.read_export({"base": [e]}, {"chunk-dup-0000"})


def test_report_reads_the_drift_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The drift sample the report recomputes is the one j-seeds wrote; agreeing re-judged labels
    lift the J caveat, flipped answers labels raise it; the label rows come from the judge dirs of
    the final files (W: the --w-final dir; drift: the --drift-final dir)."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    monkeypatch.setattr(rr, "DRIFT_SHARE", 0.5)
    inp = inputs(w, paths)
    w_need = rr.needed_pairs(rr.score_lists(w.records, inp.w_runs, inp.w_export.chunks))
    sample = rr.drift_sample(w_need)
    assert len(sample) == rr.round_half_up(0.5 * len(w_need)) > 0
    orig = inp.w_reg.labels
    ddir = tmp_path / "drift-judge"
    for be, f in (("C", "labels-qwen-C.jsonl"), ("E", "labels-qwen-E.jsonl")):
        fx.write_jsonl(tmp_path / f, list(qwen_rows(sorted(w_need))[be].values()))
        fx.write_jsonl(ddir / f, list(qwen_rows(sample)[be].values()))
    same = fx.write_jsonl(ddir / "final.jsonl", [fx.label_row(r, c, int(orig[(r, c)] == "answers")) for r, c in sample])
    rec = report(w, inputs(w, paths, drift=[same]))
    d = rec["drift_check"]
    assert d["evaluable"] and d["rejudged"] == len(sample) and d["agreement"] == 1.0
    assert d["prompt_identity"]["status"] == "identical" and d["prompt_identity"]["compared_rows"] == 2 * len(sample)
    assert d["cache_check"]["flag"] is False
    # every W pair J uses is labelled not in this world: the answers agreement cannot be computed
    assert d["answers_judged_both"] == 0 and d["caveat"].endswith("(no original answers label re-judged)")
    assert d["caveat"] in rec["descriptive"]["J"]["sentence"]
    assert d["labels_outside_sample"] == 0 and rec["descriptive"]["J"]["caveat_sources"]["label_integrity"] == []
    # an original answers label in the sample, re-judged not: the drift caveat
    first = sample[0]
    wf = paths["w_final"].read_text().replace(json.dumps(fx.label_row(*first, 0)), json.dumps(fx.label_row(*first, 1)))
    w2 = tmp_path / "w-final-2.jsonl"
    w2.write_text(wf)
    flipped = fx.write_jsonl(ddir / "final-flip.jsonl", [fx.label_row(r, c, 0) for r, c in sample])
    rec = report(w, inputs(w, {**paths, "w_final": w2}, drift=[flipped]))
    d = rec["drift_check"]
    assert d["answers_judged_both"] == 1 and d["agreement_on_answers"] == 0.0
    assert d["caveat"].startswith("judge drift: the re-judged W answers labels agree") and d["caveat"] in rec[
        "descriptive"]["J"]["sentence"]
    # a re-judge prompt that differs: the drift check is not evaluable, and the J sentence says so
    rows = asc.read_jsonl(ddir / "labels-qwen-C.jsonl")
    rows[0]["prompt_sha256"] = "another prompt"
    fx.write_jsonl(ddir / "labels-qwen-C.jsonl", rows)
    rec = report(w, inputs(w, paths, drift=[same]))
    d = rec["drift_check"]
    assert d["evaluable"] is False and d["prompt_identity"]["status"] == "mismatch"
    assert "judge drift check not evaluable (the re-judge prompt differs" in rec["descriptive"]["J"]["sentence"]
    # a drift registry in a W judge dir is refused (its rows would be compared with themselves)
    with pytest.raises(SystemExit, match="shares a judge dir"):
        inputs(w, paths, drift=[fx.write_jsonl(tmp_path / "drift-here.jsonl", [])])


# ------------------------------------------------------------------ orphans, the favourable bound, the unjudged gate

def test_orphans_count_as_not_answering_and_the_favourable_bound(tmp_path: Path) -> None:
    """PREREG §3: an S pair J needs whose record has no stage-A pair (an orphan) is never judged and
    counts as not answering; the favourable bound scores it answers, and J's descriptive sentence
    always states it (§7.2). The unjudged share above 5 % is a caveat on J, not a gate."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    g1 = rid(1, "g")
    # g1 has no stage-A pair: its two S pairs (scenario (a)) get no registry row
    rows = [r for r in asc.read_jsonl(paths["s_final"]) if r["record_id"] != g1]
    s_final = fx.write_jsonl(tmp_path / "s-final-orphan.jsonl", rows)
    rec = report(w, inputs(w, {**paths, "s_final": s_final}, main_skip=(g1,)))
    jl = rec["j_labels"]
    assert jl["s_orphans"] == 2 and jl["s_unjudged_by_kind"] == {"orphan": 2} and jl["unjudged"]["S"] == 2
    j = rec["descriptive"]["J"]
    # (a) is now W-only under J: S 47 of 52, the same as W, b = c = 1
    assert j["counts"]["S"]["k"] == 47 and j["counts"]["S_only"] == 1 and j["counts"]["W_only"] == 1
    b = j["favourable_bound"]
    assert b["pairs"] == 2 and b["counts"]["S"]["k"] == 48 and b["counts"]["W_only"] == 0
    assert not {"test", "outcome", "changes_outcome"} & set(b)
    assert j["s_orphans_primary"] == 2 and j["s_unjudged_primary_by_kind"] == {"orphan": 2}
    assert j["unjudged_primary"]["S"] == {"pairs_needed": 7, "unjudged": 2, "share": 0.2857}
    # 2 of the 7 pairs J's primary cell needs on S are unjudged (> 5 %): a caveat on J's figures, not a gate
    unj = j["caveat_sources"]["unjudged_primary"]
    assert unj == ["S: 2 of 7 pairs J's primary cell needs are unjudged (> 5%)"]
    assert_j_descriptive(rec, unj)
    assert rec["families"]["Q"]["outcome"] != "gate_not_evaluable" and rec["gates"]["Q"] == []
    # the sentence states the unjudged counts and the favourable bound, with no outcome under either
    assert ("Of the pairs this cell needs, 2 of 7 on S and 0 of " in j["sentence"]
            and " on W carry no judged label (S: 2 orphan(s)). Favourable bound: with those 2 S pair(s) scored "
                "answers, J would be 92.3 % under S, a difference of +1.9 points. No test was computed for J; this "
                "sentence decides nothing." in j["sentence"])
    assert "non_inferior" not in j["sentence"] and "NI p" not in j["sentence"]
    # with every record holding a stage-A pair, an S pair with no row is "no_registry_row", not an orphan
    rec = report(w, inputs(w, {**paths, "s_final": s_final}))
    assert rec["j_labels"]["s_unjudged_by_kind"] == {"no_registry_row": 2} and rec["j_labels"]["s_orphans"] == 0


def test_unjudged_gate_reads_the_primary_cell_and_caveats_the_secondary(tmp_path: Path) -> None:
    """§7.1 (owner default): the 5 % gate reads the pairs J's primary cell needs (default
    retriever); a norerank / BM25 shortfall above 5 % is a caveat on that secondary figure."""
    w = fx.World(tmp_path)
    g2 = rid(2, "g")
    w.s_over[(g2, "base", "bm25")] = [sid(2, 3), sid(3, 3)]  # an S BM25 miss whose pairs nobody judged
    paths = scenario(w)
    rec = report(w, inputs(w, paths))
    j = rec["descriptive"]["J"]
    assert j["caveat_sources"]["unjudged_primary"] == [] and "outcome" not in j
    by = rec["j_labels"]["unjudged_by_retriever"]["S"]
    assert by["default"]["unjudged"] == 0 and by["bm25"]["unjudged"] == 2
    sec = rec["descriptive"]["J"]["secondary"]["by_retriever_pooled"]
    assert sec["bm25"]["caveat"].startswith("S: 2 of ") and "this retriever's J needs are unjudged (> 5%)" in \
        sec["bm25"]["caveat"]
    assert sec["norerank"]["caveat"] is None and "caveat" not in sec["default"]
    assert "caveat" not in rec["families"]["Q"]["secondary"]["by_retriever_pooled"]["bm25"]


def test_prior_registry_content_sha_is_checked(tmp_path: Path) -> None:
    """A prior S label is reused only for the bytes it was judged on: with the prior registry's
    pairs.jsonl given, a key whose content_sha256 is not the S export's text is not reused and
    counts as unjudged (a caveat on J); neither report nor j-seeds refuses (J never blocks Q)."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    g1 = rid(1, "g")
    prior = fx.write_jsonl(tmp_path / "prior" / "final.jsonl", [fx.label_row(g1, sid(1, 3), 0)])
    text = {r["chunk_id"]: r["content"] for r in w.s_rows}
    good = fx.write_jsonl(tmp_path / "prior" / "pairs.jsonl", [
        {"pair_id": aj.pair_id(g1, sid(1, 3)), "record_id": g1, "chunk_id": sid(1, 3),
         "content_sha256": asc.sha_text(text[sid(1, 3)])}])
    rows = [r for r in asc.read_jsonl(paths["s_final"]) if (r["record_id"], r["chunk_id"]) != (g1, sid(1, 3))]
    s_final = fx.write_jsonl(tmp_path / "s-final-noprior.jsonl", rows)
    rec = report(w, inputs(w, {**paths, "s_final": s_final}, prior=[prior], prior_pairs=[good]))
    assert rec["j_labels"]["s_prior_content_check"] == {
        "given": True, "reused_keys_checked": 1, "reused_keys_without_pairs_row": 0,
        "not_reused_content_mismatch": 0, "not_reused_no_s_text": 0}
    j = rec["descriptive"]["J"]
    assert j["caveat_sources"]["label_integrity"] == [] and j["caveat_sources"]["label_reuse"] == []
    assert rec["j_labels"]["s_label_source"]["prior:not"] == 1
    bad = fx.write_jsonl(tmp_path / "prior" / "pairs-bad.jsonl", [
        {"pair_id": aj.pair_id(g1, sid(1, 3)), "record_id": g1, "chunk_id": sid(1, 3),
         "content_sha256": asc.sha_text("other bytes")}])
    rec = report(w, inputs(w, {**paths, "s_final": s_final}, prior=[prior], prior_pairs=[bad]))
    pc = rec["j_labels"]["s_prior_content_check"]
    assert pc["not_reused_content_mismatch"] == 1 and pc["reused_keys_checked"] == 0
    assert rec["j_labels"]["s_label_source"]["prior_content_mismatch:unjudged"] == 1
    assert rec["j_labels"]["s_unjudged_by_kind"]["prior_content_mismatch"] == 1
    j = rec["descriptive"]["J"]
    assert j["caveat_sources"]["label_reuse"] == [
        "1 prior S label(s) not reused: their prior pairs.jsonl content_sha256 is not the sha256 of the S export's "
        "text of the chunk (or the chunk has no S text); those pairs count as unjudged"]
    assert j["caveat_sources"]["label_reuse"][0] in j["caveats"]
    assert rec["gates"]["Q"] == []  # J's label inputs never gate Q
    # even a new S label for that key is not taken over the rule: the key was never to be seeded
    with_new = fx.write_jsonl(tmp_path / "s-final-with.jsonl", [*rows, fx.label_row(g1, sid(1, 3), 1)])
    rec = report(w, inputs(w, {**paths, "s_final": with_new}, prior=[prior], prior_pairs=[bad]))
    assert rec["j_labels"]["s_label_source"]["prior_content_mismatch:unjudged"] == 1
    # j-seeds writes, with the mismatch counted and the key not seeded
    out = tmp_path / "private"
    assert rr.main(w.args("j-seeds", "--w-final", str(paths["w_final"]), "--s-prior-final", str(prior),
                          "--s-prior-pairs", str(bad), "--main-pairs", str(w.main_pairs()), "--out-dir", str(out))) == 0
    summ = json.loads((out / "j-seeds.json").read_text())
    detail = json.loads((out / "j-seeds-detail.json").read_text())
    assert summ["inputs_sha256"]["s_prior_pairs"] == [asc.sha256_file(bad)]
    assert summ["not_seeded"] == {"prior_content_mismatch": 1, "s_no_export_text": 0, "drift_no_w_export_text": 0}
    assert detail["s_label_before_judging"]["prior_content_mismatch"] == 1
    assert (g1, sid(1, 3)) not in {(s_["record_id"], s_["chunk_id"]) for s_ in asc.read_jsonl(out / "s-seed.jsonl")}
    # a reused key with no pairs row is counted (a caveat), not refused
    nopairs = fx.write_jsonl(tmp_path / "prior" / "pairs-none.jsonl", [])
    rec = report(w, inputs(w, {**paths, "s_final": s_final}, prior=[prior], prior_pairs=[nopairs]))
    assert rec["j_labels"]["s_prior_content_check"]["reused_keys_without_pairs_row"] == 1
    assert rec["j_labels"]["s_label_source"]["prior:not"] == 1
    assert rec["descriptive"]["J"]["caveat_sources"]["label_reuse"] == [
        "1 reused prior S label(s) with no prior pairs.jsonl row: their content sha could not be checked"]
    # --s-prior-pairs is required with --s-prior-final, from the same judge dir (PREREG §3)
    with pytest.raises(SystemExit, match="needs --s-prior-pairs"):
        report(w, inputs(w, {**paths, "s_final": s_final}, prior=[prior], prior_pairs=[]))
    elsewhere = fx.write_jsonl(tmp_path / "other" / "pairs.jsonl", asc.read_jsonl(good))
    with pytest.raises(SystemExit, match="each --s-prior-final's judge dir"):
        report(w, inputs(w, {**paths, "s_final": s_final}, prior=[prior], prior_pairs=[elsewhere]))
    with pytest.raises(SystemExit, match="without --s-prior-final"):
        report(w, inputs(w, {**paths, "s_final": s_final}, prior_pairs=[good]))
    # no prior registry: nothing to check
    rec = report(w, inputs(w, {**paths, "s_final": s_final}))
    assert rec["j_labels"]["s_prior_content_check"] == {"given": False}


def test_prior_content_status() -> None:
    sha_of = rr.prior_content_sha_index([{"record_id": "r", "chunk_id": "a", "content_sha256": asc.sha_text("A")},
                                         {"record_id": "r", "chunk_id": "b", "content_sha256": asc.sha_text("x")},
                                         {"record_id": "r", "chunk_id": "c", "content_sha256": asc.sha_text("C")},
                                         {"record_id": "r", "chunk_id": "c", "content_sha256": asc.sha_text("C2")},
                                         {"record_id": "r", "chunk_id": "d", "content_sha256": asc.sha_text("D")}])
    text = {"a": "A", "b": "B", "c": "C"}.get
    got = {k: rr.prior_content_status(("r", k), sha_of, text) for k in "abcde"}
    # two registries' rows of one key with different bytes: not the S text alone, a mismatch
    assert got == {"a": "match", "b": "mismatch", "c": "mismatch", "d": "no_s_text", "e": "no_pairs_row"}


def test_j_seeds_count_pairs_without_text_and_never_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PREREG §6 step 3: an S pair J needs whose chunk has no S export row, or a drift pair with no
    W export row, is counted (a caveat on J) and j-seeds.json is still written."""
    w = fx.World(tmp_path)
    paths = scenario(w)
    monkeypatch.setattr(rr, "DRIFT_SHARE", 1.0)
    g1 = rid(1, "g")
    # S: (a)'s list carries a chunk with no S export row; W: one list of record g1 a chunk with no W row
    w.s_over[(g1, "base", "default")] = [sid(1, 0), "schunk-ghost-0000"]
    w.w_over[(g1, "base", "default")] = [wid(1, 1), "wchunk-ghost-0000"]
    w.write_runs("W")
    w.write_runs("S")
    out = tmp_path / "private"
    assert rr.main(w.args("j-seeds", "--w-final", str(paths["w_final"]), "--main-pairs", str(w.main_pairs()),
                          "--out-dir", str(out))) == 0
    summ = json.loads((out / "j-seeds.json").read_text())
    assert summ["not_seeded"] == {"prior_content_mismatch": 0, "s_no_export_text": 1, "drift_no_w_export_text": 1}
    assert set(summ["inputs_sha256"]) >= {"records", "w_runs", "s_runs", "s_exports", "w_exports"}
    assert "ghost" not in (out / "s-seed.jsonl").read_text() + (out / "drift-seed.jsonl").read_text()
    # report: the no-text pairs are J caveats (and the top-10 chunks without text stay Q gates, §7.1)
    rec = report(w, inputs(w, paths))
    j = rec["descriptive"]["J"]
    assert rec["j_labels"]["s_unjudged_by_kind"]["no_export_text"] == 1
    assert "1 S pair(s) J needs have no S export text (never seeded); they count as unjudged" in \
        j["caveat_sources"]["label_reuse"]
    assert "S: 1 top-10 chunk id(s) with no text in the side's exports" in rec["gates"]["Q"]
    assert "W: 1 top-10 chunk id(s) with no text in the side's exports" in rec["gates"]["Q"]
    assert rec["drift_check"]["sample_without_w_text"] == 1
    assert "1 drift sample pair(s) have no W export text (never re-judged)" in rec["drift_check"]["caveat"]


def test_j_seeds_check_is_order_insensitive(tmp_path: Path) -> None:
    js = tmp_path / "j.json"
    js.write_text(json.dumps({"schema": rr.SCHEMA["seeds"], "inputs_sha256": {"s_prior_pairs": ["b", "a"],
                                                                              "w_final": ["x"]}}))
    assert rr.check_j_seeds(js, {"s_prior_pairs": ["a", "b"], "w_final": ["x"]})["inputs_match"] is True
    with pytest.raises(SystemExit, match=r"\(s_prior_pairs\)"):
        rr.check_j_seeds(js, {"s_prior_pairs": ["a", "c"], "w_final": ["x"]})


# ------------------------------------------------------------------ the §9.1 pins

def test_frozen_inputs_json_is_the_prereg_table() -> None:
    """``frozen-inputs.json`` is :func:`frozen_inputs` of the committed PREREG §9.1 table: 27 rows,
    the S run config not a report input, and the per-flag counts of the table."""
    text = rr.PREREG_FILE.read_text(encoding="utf-8")
    want = rr.frozen_inputs(text)
    assert json.loads(rr.FROZEN_INPUTS.read_text(encoding="utf-8")) == want
    assert len(want["rows"]) == 27
    assert [r for r in want["rows"] if r["flag"] is None] == [
        {"flag": None, "file": "reach-config.json", "sha256": want["rows"][0]["sha256"]}]
    assert {k: len(v) for k, v in want["by_flag"].items()} == {
        "--records": 1, "--w-runs": 8, "--w-export": 4, "--s-export": 4, "--w-final": 1, "--main-pairs": 1,
        "--s-prior-final": 3, "--s-prior-pairs": 3, "--build-summary": 1}
    assert set(want["by_flag"]) == set(rr.FROZEN_FLAGS)
    # the table's own sha cells, nothing else: every row's sha appears in §9.1 verbatim
    sec = text.split("### 9.1 ", 1)[1].split("### 9.2 ", 1)[0]
    assert all(f"`{r['sha256']}`" in sec for r in want["rows"])
    assert sum(line.startswith("| ") for line in sec.splitlines()) == 28  # the header and 27 rows


def test_frozen_inputs_check_refuses_other_inputs() -> None:
    by = {"--records": ["a" * 64], "--w-runs": sorted(["b" * 64, "c" * 64]), "--w-export": ["d" * 64],
          "--s-export": ["e" * 64], "--w-final": ["f" * 64], "--main-pairs": ["1" * 64], "--s-prior-final": ["2" * 64],
          "--s-prior-pairs": ["3" * 64], "--build-summary": ["4" * 64]}
    text = freeze_table(by)
    frozen = rr.frozen_inputs(text)
    inputs = {"records": "a" * 64, "w_runs": {"base/none.jsonl": "c" * 64, "base/none.summary.json": "b" * 64},
              "s_runs": {"base/none.jsonl": "9" * 64}, "w_exports": {"x": "d" * 64}, "s_exports": {"y": "e" * 64},
              "w_final": ["f" * 64], "main_pairs": "1" * 64, "s_prior_final": ["2" * 64], "s_prior_pairs": ["3" * 64],
              "build_summary": "4" * 64}
    assert REAL_FROZEN_CHECK(inputs, frozen, text)["verified"] is True  # order-free; the S runs are post-freeze
    for k, v in (("records", "0" * 64), ("w_final", ["f" * 64, "5" * 64]), ("s_prior_pairs", []),
                 ("build_summary", None), ("w_runs", {"base/none.jsonl": "c" * 64})):
        with pytest.raises(SystemExit, match="not the §9.1 pins"):
            REAL_FROZEN_CHECK({**inputs, k: v}, frozen, text)
    with pytest.raises(SystemExit, match="is not the prereg's §9.1 table"):
        REAL_FROZEN_CHECK(inputs, {**frozen, "rows": frozen["rows"][1:]}, text)
    with pytest.raises(SystemExit, match="no §9.1"):
        rr.parse_freeze_table("# nothing\n")


def test_report_cli_refuses_inputs_that_are_not_the_frozen_ones(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    w = fx.World(tmp_path)
    paths = scenario(w)
    js = j_seeds(w, paths, tmp_path / "private")
    monkeypatch.setattr(rr, "check_frozen_inputs", REAL_FROZEN_CHECK)
    out = tmp_path / "r.json"
    with pytest.raises(SystemExit, match=r"not the §9.1 pins \(--records, --w-runs"):
        rr.main(w.args("report", "--w-final", str(paths["w_final"]), "--s-final", str(paths["s_final"]),
                       "--main-pairs", str(w.main_pairs()), "--build-summary", str(paths["build"]),
                       "--tokenizer", "none", *pre_args(), "--j-seeds", str(js), "--out", str(out)))
    assert not out.exists()


def test_frozen_inputs_cli_writes_the_table(tmp_path: Path) -> None:
    out = tmp_path / "f.json"
    assert rr.main(["frozen-inputs", "--out", str(out)]) == 0
    assert out.read_bytes() == rr.FROZEN_INPUTS.read_bytes()


def test_read_export_refuses_duplicate_basenames(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    a = tmp_path / "a" / "chunks.jsonl"
    b = tmp_path / "b" / "chunks.jsonl"
    for p_ in (a, b):
        p_.parent.mkdir()
        p_.write_bytes(w.w_export.read_bytes())
    with pytest.raises(SystemExit, match="share the basename 'chunks.jsonl'"):
        rr.read_export(rr.parse_exports([f"base={a}", f"neighbour={b}"]), set())


def test_read_summaries_reads_only_the_summaries(tmp_path: Path) -> None:
    w = fx.World(tmp_path)
    d = w.write_runs("W")
    for c in rr.CORPORA:
        (d / c / "none.jsonl").unlink()
    runs = rr.read_summaries(d)
    assert runs.rows == {} and set(runs.summaries) == set(rr.CORPORA)
    assert set(runs.sha256) == {f"{c}/none.summary.json" for c in rr.CORPORA}
    assert rr.settings_gates(runs, runs)[0] == []
