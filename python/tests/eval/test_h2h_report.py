"""Tooling of the adopted-spec head-to-head PREREG (scripts/eval/h2h, scripts/eval/h2h_report.py,
docs/plans/results/adopted-spec-h2h-2026-11/power.py).

Every §4.5 row under both cost orders on synthetic numbers, the boundary values of every
threshold, the mutation check (swap the order on a tie: the decision and the closing clause
change exactly as §4.5 says), H3, the Holm family per setting, every file-checkable gate on
synthetic receipts / stderr / counts (absent inputs fail), the cross-check pass and fail
(D1 and the ladder at its own level), the decision.json integrity gate, the
GATE-NOT-EVALUABLE paths with the gate counts in the sentence, S-regex missing end to end,
the public-file guard (values, keys, tuples, paths, host:port), the PREREG sha256 refusal,
the end-to-end report on synthetic per_question rows (three-arm and five-arm, a failing
stratum, refusals routed by arm), the seeded re-embed sample, the config derivation, the §5
power numbers and the pinned workflow texts. No network; no private file."""
from __future__ import annotations

import difflib
import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import h2h_report as cli  # noqa: E402
from h2h import dryrun_check, gates, make_configs, reembed_sample, report, rules  # noqa: E402
from oa_chunk_eval import decide  # noqa: E402
from oa_chunk_eval.score import set_rows  # noqa: E402
from oa_chunk_eval.stats import paired_diff  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
STUDY = REPO / "docs" / "plans" / "results" / "adopted-spec-h2h-2026-11"
ITERS, SEED = 2000, 0
COMMIT = "5d65bead" * 5
CLEAN_PROV = {"version": "v1.6.25", "commit": "67e53fd4" * 5, "dirty": False, "untracked": 0, "citable": True,
              "source": "git", "describe": "v1.6.25-0-g67e53fd", "schema": "ragstack.experiment_provenance/1"}


def iv(point: float, lo: float, hi: float, n: int = 2035) -> dict[str, Any]:
    return {"point": point, "lo": lo, "hi": hi, "n": n}


NUMBERS = {"D1": iv(3.49, 2.11, 4.86), "hit_S": 82.9, "hit_inc": 82.5, "n": 2035, "pi": 0.047}


def run(order, *, gates_=(), D, E=None, ctx=0.8, strata=(), k=11):
    return rules.decide_both_orders(order, gates=list(gates_), D=D, E=E, D1=NUMBERS["D1"], ctx_ratio=ctx,
                                    strata_failed=list(strata), k_strata=k, hit_S=NUMBERS["hit_S"],
                                    hit_inc=NUMBERS["hit_inc"], n=NUMBERS["n"], pi=NUMBERS["pi"])


# ------------------------------------------------------------------- §4.1 / §4.5, both orders


@pytest.mark.parametrize("order", ["A", "B"])
def test_row1_superior_both_orders(order):
    r = run(order, D=iv(1.8, 0.4, 3.2), E=iv(-1.8, -3.2, -0.4))
    assert (r["decision"], r["row"]) == ("S", 1)
    assert r["H1"] and r["H2"]
    assert r["other_order"]["decision"] == "S" and r["other_order"]["row"] == 1
    s = r["sentence"]
    assert s.startswith("Deploy S: in setting (a), hit@5 was 82.90% under S against 82.50% under ft512o64s (n = 2,035)")
    assert "S − ft512o64s = +1.80 [+0.40, +3.20], discordance 0.047" in s
    assert "non-inferiority at 2.0 points holds and S is superior (lower bound > 0)." in s
    assert "Gate G1: ft512o64s − ft512o64 = +3.49 [+2.11, +4.86]." in s
    assert s.endswith(f"Under cost order {rules.OTHER[order]} the decision would be S.")


def test_tie_rows_2_3_and_the_mutation_check():
    D, E = iv(-0.4, -1.4, 0.5), iv(0.4, -0.5, 1.4)
    a = run("A", D=D, E=E)
    b = run("B", D=D, E=E)
    assert (a["decision"], a["row"]) == ("ft512o64s", 2)
    assert (b["decision"], b["row"]) == ("S", 3)
    assert a["other_order"] == {"order": "B", "decision": "S", "row": 3, "row_label": rules.ROWS[3]}
    assert b["other_order"] == {"order": "A", "decision": "ft512o64s", "row": 2, "row_label": rules.ROWS[2]}
    assert "the tie goes to the cheaper arm under cost order A (ft512o64s − S = +0.40 [-0.50, +1.40])" in a["sentence"]
    assert a["sentence"].endswith("Under cost order B the decision would be S.")
    assert "ft512o64s is not shown superior to S (ft512o64s − S = +0.40 [-0.50, +1.40]); the tie goes to the cheaper arm under cost order B" in b["sentence"]
    assert b["sentence"].endswith("Under cost order A the decision would be ft512o64s.")
    assert a["H1"] and a["H2"] is False


def test_row4_ladder_order_b_vs_row2_order_a():
    D, E = iv(-1.2, -1.9, -0.3), iv(1.2, 0.3, 1.9)
    b = run("B", D=D, E=E)
    a = run("A", D=D, E=E)
    assert (b["decision"], b["row"]) == ("ft512o64s", 4)
    assert "ft512o64s is superior to S (ft512o64s − S = +1.20 [+0.30, +1.90], lower bound > 0; frozen ladder)" in b["sentence"]
    assert b["sentence"].endswith("Under cost order A the decision would be ft512o64s.")
    assert (a["decision"], a["row"]) == ("ft512o64s", 2)
    assert a["sentence"].endswith("Under cost order B the decision would be ft512o64s.")


@pytest.mark.parametrize("order", ["A", "B"])
def test_row5_margin_with_and_without_stratum(order):
    r = run(order, D=iv(-1.5, -2.4, -0.6))
    assert (r["decision"], r["row"]) == ("ft512o64s", 5)
    assert "a loss of 2.0 points or more under S is not ruled out (this is not evidence that S is worse)." in r["sentence"]
    assert "inferior" not in r["sentence"].split("not ruled out")[1].split("Gate G1")[0].replace("not shown", "")
    assert r["other_order"]["row"] == 5 and r["sentence"].endswith(f"Under cost order {rules.OTHER[order]} the decision would be ft512o64s.")
    failed = [{"dim": "journal_family", "value": "virology", "n": 301, "point": -3.1, "lo": -6.0, "hi": -0.4}]
    r2 = run(order, D=iv(-1.5, -2.4, -0.6), strata=failed)
    assert r2["row"] == 5
    assert "; and S failed the stratum guard (journal_family = virology, point -3.10, upper bound -0.40 at 1 − 0.05/11)." in r2["sentence"]


@pytest.mark.parametrize("order", ["A", "B"])
def test_row6_ctx_rule(order):
    r = run(order, D=iv(1.3, 0.5, 2.1), ctx=1.6)
    assert (r["decision"], r["row"]) == ("ft512o64s", 6)
    assert r["result"]["h1_threshold"] == 2.0
    assert "S returns 1.60× the baseline's context at k = 5 (> 1.5) and did not clear +2.0 points (context-token rule)." in r["sentence"]
    assert run(order, D=iv(1.3, 0.5, 2.1), ctx=1.4)["row"] == 1
    assert run(order, D=iv(3.0, 2.2, 3.8), ctx=1.6)["row"] == 1


@pytest.mark.parametrize("order", ["A", "B"])
def test_row7_stratum_only(order):
    failed = [{"dim": "length_band", "value": "long", "n": 641, "point": -2.6, "lo": -5.1, "hi": -0.2}]
    r = run(order, D=iv(0.3, -0.7, 1.3), strata=failed, k=10)
    assert (r["decision"], r["row"]) == ("ft512o64s", 7)
    assert "failed the stratum guard (length_band = long, point -2.60, upper bound -0.20 at 1 − 0.05/10)." in r["sentence"]
    assert "within 2.0 points of ft512o64s on the pooled set" in r["sentence"]


@pytest.mark.parametrize("order", ["A", "B"])
def test_row8_gate_not_evaluable_counts_each_gate(order):
    gl = ["[G1] (a) lower(ft512o64s − ft512o64) = -0.20 ≤ 0", "[docs_failed] S/main-03: n_docs_failed 2",
          "[docs_failed] S/main-07: n_docs_failed 1", "uncoded gate"]
    r = run(order, gates_=gl, D=iv(1.8, 0.4, 3.2))
    assert (r["decision"], r["row"]) == ("GATE-NOT-EVALUABLE", 8)
    s = r["sentence"]
    assert s.startswith("GATE-NOT-EVALUABLE: G1 ×1, docs_failed ×2, other ×1. The contrasts were computed (")
    assert "decide nothing; the server default stays as it is." in s
    assert s.endswith(f"Under cost order {rules.OTHER[order]} the decision would be GATE-NOT-EVALUABLE.")
    assert r["H1"] is None and r["H2"] is None
    # 34 failures (an S-regex built with nltk by mistake) still give a short sentence
    many = [f"[segmenter] S-regex/main-{i:02d}: segmenter_version 'punkt-abbrev-v1', expected 'regex-abbrev-v1'" for i in range(17)]
    many += [f"[stderr_lines] S-regex/main-{i:02d}: no 'NLTK Punkt is unavailable' line in worker stderr (expected ≥ 1)" for i in range(17)]
    assert sum(map(len, many)) > 1500
    s2 = run(order, gates_=many, D=iv(1.8, 0.4, 3.2))["sentence"]
    assert "segmenter ×17, stderr_lines ×17" in s2 and len(s2) < 600


def test_boundaries_of_every_threshold():
    """lo = −2.0 passes H1 (≥); lo = 0 fails H2 (>); ctx 1.5 does not raise the threshold (>);
    loE = 0 keeps the tie with S under Order B (>); H3 lo = −2.0 passes (≥); G1 lo = 0 fails (>)."""
    assert run("A", D=iv(-1.0, -2.0, 0.0), E=iv(1.0, 0.0, 2.0))["row"] == 2   # H1 holds at exactly −2.0, H2 fails at exactly 0
    assert run("B", D=iv(-1.0, -2.0, 0.0), E=iv(1.0, 0.0, 2.0))["row"] == 3   # loE = 0: not superior
    assert run("B", D=iv(-1.0, -2.0, 0.0), E=iv(1.0, 1e-9, 2.0))["row"] == 4
    assert run("A", D=iv(-1.0, -2.0 - 1e-9, 0.0))["row"] == 5
    assert run("A", D=iv(1.0, 1e-9, 2.0))["row"] == 1
    assert run("A", D=iv(1.0, 0.5, 2.0), ctx=1.5)["row"] == 1
    assert run("A", D=iv(1.0, 0.5, 2.0), ctx=1.5 + 1e-9)["row"] == 6
    assert run("A", D=iv(2.5, 2.0, 3.0), ctx=1.6)["row"] == 1    # raised threshold met at exactly +2.0
    assert rules.h3(gates=[], D3=iv(-1.0, -2.0, 0.0), strata_failed=[], k_strata=11)["any_worker"]
    assert not rules.h3(gates=[], D3=iv(-1.0, -2.0 - 1e-9, 0.0), strata_failed=[], k_strata=11)["any_worker"]
    assert rules.g1_holds(iv(1.0, 1e-9, 2.0)) and not rules.g1_holds(iv(1.0, 0.0, 2.0)) and not rules.g1_holds({"lo": None})
    tested = [{"dim": "j", "value": "x", "n": 40, "point": -2.0, "lo": -6, "hi": -0.1},      # point = −2.0: not < −2.0
              {"dim": "j", "value": "y", "n": 40, "point": -2.0 - 1e-9, "lo": -6, "hi": 0.0},  # hi = 0: not < 0
              {"dim": "j", "value": "z", "n": 40, "point": -2.0 - 1e-9, "lo": -6, "hi": -1e-9}]
    assert [s["value"] for s in rules.stratum_failures(tested)] == ["z"]


def test_exactly_one_row_per_path_self_check():
    seen = set()
    cases = [{"D": iv(1.8, 0.4, 3.2)}, {"D": iv(-0.4, -1.4, 0.5), "E": iv(0.4, -0.5, 1.4)},
             {"D": iv(-1.2, -1.9, -0.3), "E": iv(1.2, 0.3, 1.9)}, {"D": iv(-1.5, -2.4, -0.6)},
             {"D": iv(1.3, 0.5, 2.1), "ctx": 1.6},
             {"D": iv(0.3, -0.7, 1.3), "strata": [{"dim": "x", "value": "y", "n": 40, "point": -3.0, "lo": -6, "hi": -0.1}]},
             {"gates_": ["[g] g"], "D": iv(1.8, 0.4, 3.2)}]
    for c in cases:
        for order in rules.ORDERS:
            seen.add(run(order, **c)["row"])
    assert seen == set(range(1, 9))


def test_decide_refuses_bad_inputs():
    with pytest.raises(rules.RuleError):
        rules.decide("C", gates=[], D=iv(0, 0, 0), E=None, ctx_ratio=None, strata_failed=[], k_strata=0)
    with pytest.raises(rules.RuleError):
        rules.decide("B", gates=[], D=iv(0.1, -0.5, 0.7), E=None, ctx_ratio=None, strata_failed=[], k_strata=0)
    with pytest.raises(rules.RuleError):
        rules.decide("A", gates=[], D={"point": 0.1, "lo": None, "hi": 0.7}, E=None, ctx_ratio=None, strata_failed=[], k_strata=0)


# ------------------------------------------------------------------- H3, Holm


def test_h3_paths():
    ok = rules.h3(gates=[], D3=iv(-0.2, -0.9, 0.5), strata_failed=[], k_strata=11, overlap_share=0.9987)
    assert ok["any_worker"]
    assert ok["sentence"] == "Segmenter: S-regex − S = -0.20 [-0.90, +0.50]; S may be deployed on any worker (on this corpus; chunk-id overlap 99.9 %)."
    m = rules.h3(gates=[], D3=iv(-1.9, -2.6, -1.2), strata_failed=[], k_strata=11)
    assert not m["any_worker"] and "(reason: margin; chunk-id overlap not computed)" in m["sentence"]
    st = rules.h3(gates=[], D3=iv(-0.2, -0.9, 0.5), strata_failed=[{"dim": "length_band", "value": "short", "n": 611, "point": -2.5, "lo": -5, "hi": -0.3}], k_strata=11)
    assert not st["any_worker"] and "(reason: stratum (length_band = short, point -2.50, upper bound -0.30 at 1 − 0.05/11); chunk-id" in st["sentence"]
    g = rules.h3(gates=["[segmenter] S-regex/main-02: segmenter_version 'punkt-abbrev-v1', expected 'regex-abbrev-v1'"] * 3,
                 D3=iv(-0.2, -0.9, 0.5), strata_failed=[], k_strata=11)
    assert not g["evaluable"] and not g["any_worker"]
    assert "only where Punkt is asserted (reason: not evaluable: segmenter ×3;" in g["sentence"]
    nr = rules.h3(gates=["[arm_missing] S-regex: not run"], D3=None, strata_failed=[], k_strata=0)
    assert nr["sentence"].startswith("Segmenter: S-regex − S = not computed; S may be deployed only where Punkt is asserted (reason: not evaluable: arm_missing ×1")


def test_holm_family_per_setting():
    p = {"C1": 0.01, "C2": 0.03, "C3": 0.5, "C4": 0.0001}
    a = rules.holm_family("a", p)
    assert set(a) == {"C1", "C2"}
    assert a["C1"]["p_holm"] == 0.02 and a["C1"]["holm_sig"] and a["C2"]["p_holm"] == 0.03 and a["C2"]["holm_sig"]
    d = rules.holm_family("d", p)
    assert set(d) == {"C1", "C2", "C3", "C4"}
    assert d["C4"]["p_holm"] == pytest.approx(0.0004) and not d["C3"]["holm_sig"]
    assert rules.holm_family("b", {"C1": 0.2}) == {"C1": {"p": 0.2, "p_holm": 0.2, "holm_sig": False}}


def test_gate_counts():
    assert gates.gate_counts(["[a] x", "[b] y", "[a] z", "plain"]) == {"a": 2, "b": 1, "other": 1}
    assert rules.gate_clause([]) == "(none listed)"


# ------------------------------------------------------------------- gates on synthetic data


def receipt(arm: str, label: str, n_docs: int, n_chunks: int, build: str = "1", **over) -> dict[str, Any]:
    seg = {"S": "punkt-abbrev-v1", "S-regex": "regex-abbrev-v1"}[arm]
    r = {"status": "completed", "n_docs": n_docs, "n_docs_failed": 0, "error": "", "n_chunks": n_chunks,
         "chunk_ids": [f"{label}-{arm}-{i}" for i in range(n_chunks)],
         "provenance": {"image_build": build, "image_commit": COMMIT, "image_version": "v1.6.24",
                        "segmenter_version": seg, "tool_image": None, "tool_image_digest": None, "workflow_id": None},
         "chunking": {"v": 1, "method": "sentence", "chunk_size": 512, "size_tolerance": 0.25, "min_piece": 128,
                      "ceiling": 640, "structure_cuts": "sections/1", "window_budget": 4080}}
    for k, v in over.items():
        if "." in k:
            a, b = k.split(".", 1)
            r[a][b] = v
        else:
            r[k] = v
    return r


def arm_receipts(arm: str, build: str, n_main: int = 241_930) -> dict[str, dict[str, Any]]:
    per = [n_main // 16] * 16
    per[-1] += n_main - sum(per)
    out = {f"main-{i:02d}": receipt(arm, f"main-{i:02d}", per[i], 50 + i, build) for i in range(16)}
    out["sentinel"] = receipt(arm, "sentinel", 40, 168, build)
    return out


def chk(r, arm="S", build="1", commit=COMMIT):
    return gates.check_receipt(r, arm, "x", expect_build=build, expect_commit=commit)


def test_receipt_gates():
    assert chk(receipt("S", "x", 40, 5)) == []
    assert chk(receipt("S", "x", 40, 5, **{"chunking.ceiling": None})) == ["[chunking] S/x: chunking.ceiling = None, expected 640"]
    r = receipt("S", "x", 40, 5)
    del r["chunking"]["v"]
    assert chk(r) == ["[chunking] S/x: chunking.v missing"]
    r = receipt("S", "x", 40, 5)
    del r["chunking"]
    assert chk(r) == ["[chunking] S/x: no chunking object on the receipt"]
    assert chk(receipt("S", "x", 40, 5, **{"provenance.segmenter_version": "regex-abbrev-v1"})) == \
        ["[segmenter] S/x: segmenter_version 'regex-abbrev-v1', expected 'punkt-abbrev-v1'"]
    assert chk(receipt("S-regex", "x", 40, 5, build="1"), "S-regex", "2") == ["[image_build] S-regex/x: image_build '1', expected '2'"]
    assert chk(receipt("S", "x", 40, 5, **{"provenance.image_commit": None})) == \
        ["[image_commit] S/x: image_commit is null (not run from a stamped image)"]
    assert chk(receipt("S", "x", 40, 5, **{"provenance.image_commit": "ab" * 20})) == \
        [f"[image_commit] S/x: image_commit {'ab' * 6} != pinned {COMMIT[:12]}"]
    # absent pins fail
    assert chk(receipt("S", "x", 40, 5), build=None) == ["[image_build] S/x: no pinned build number to compare against (absent)"]
    assert chk(receipt("S", "x", 40, 5), commit=None) == ["[image_commit] S/x: no pinned image commit to compare against (absent)"]
    assert chk(receipt("S", "x", 40, 5, n_docs_failed=3)) == ["[docs_failed] S/x: n_docs_failed 3"]
    assert chk(receipt("S", "x", 40, 5, status="failed", error="boom"))[:2] == ["[receipt_status] S/x: status 'failed'", "[receipt_error] S/x: error 'boom'"]
    r = receipt("S", "x", 40, 5)
    r["n_chunks"] = 4
    assert chk(r) == ["[chunk_ids] S/x: n_chunks 4 != distinct chunk_ids 5"]
    bad, census = gates.check_arm_receipts(arm_receipts("S", "1"), "S", expect_build="1", expect_commit=COMMIT, expect_sentinel_chunks=168)
    assert bad == [] and census["expected_store_count"] == sum(50 + i for i in range(16)) + 168 and census["chunk_ids_carried"]
    bad, _ = gates.check_arm_receipts(arm_receipts("S", "1", n_main=241_929), "S", expect_build="1", expect_commit=COMMIT, expect_sentinel_chunks=168)
    assert bad == ["[docs] S: 241,929 documents in the main shards, expected 241,930"]
    bad, _ = gates.check_arm_receipts(arm_receipts("S", "1"), "S", expect_build="1", expect_commit=COMMIT, expect_sentinel_chunks=None)
    assert bad == ["[sentinel_chunks] S: no pinned sentinel chunk count (absent)"]
    bad, _ = gates.check_arm_receipts(arm_receipts("S", "1"), "S", expect_build="1", expect_commit=COMMIT, expect_sentinel_chunks=193)
    assert bad == ["[sentinel_chunks] S: sentinel n_chunks 168, expected 193 (dry run)"]
    rr = arm_receipts("S-regex", "2")
    rr["main-04"]["provenance"]["image_commit"] = "other"
    bad, c2 = gates.check_arm_receipts(rr, "S-regex", expect_build="2", expect_commit=COMMIT, expect_sentinel_chunks=168)
    assert bad == ["[image_commit] S-regex/main-04: image_commit other != pinned " + COMMIT[:12],
                   "[image_commit] S-regex: 2 distinct image_commit values across receipts"]
    _, cs = gates.check_arm_receipts(arm_receipts("S", "1"), "S", expect_build="1", expect_commit=COMMIT, expect_sentinel_chunks=168)
    assert gates.check_same_commit(cs, c2) == ["[image_commit] image_commit missing on one arm"]
    _, cr = gates.check_arm_receipts(arm_receipts("S-regex", "2"), "S-regex", expect_build="2", expect_commit=COMMIT, expect_sentinel_chunks=168)
    assert gates.check_same_commit(cs, cr) == []
    del rr["sentinel"]
    bad, _ = gates.check_arm_receipts(rr, "S-regex", expect_build="2", expect_commit=COMMIT, expect_sentinel_chunks=168)
    assert "[receipts] S-regex: no sentinel receipt" in bad


WARN = "sentence_spans: NLTK Punkt is unavailable; using the regex sentence fallback (segmenter_version=regex-abbrev-v1)\n"


def test_stderr_gates():
    clean = "[chunker] sentence: chunk_size=512\nUnclosed client session\n"
    assert gates.check_stderr({"a": clean, "b": clean}, "S")[0] == []
    assert gates.check_stderr({"a": clean + WARN}, "S")[0] == ["[stderr_lines] S/a: 1 'NLTK Punkt is unavailable' line(s) in worker stderr (expected 0)"]
    assert gates.check_stderr({"a": clean + WARN}, "S-regex")[0] == []
    assert gates.check_stderr({"a": clean}, "S-regex")[0] == ["[stderr_lines] S-regex/a: no 'NLTK Punkt is unavailable' line in worker stderr (expected ≥ 1)"]
    assert gates.check_stderr({"a": None}, "S")[0] == ["[stderr_missing] S/a: worker stderr not captured"]
    big = WARN + "x" * (256 * 1024)
    assert gates.check_stderr({"a": big}, "S-regex", tail_limited=True)[0][0].startswith("[stderr_tail] S-regex/a: stderr tail at the 256 KB limit")
    assert gates.check_stderr({"a": big}, "S-regex", tail_limited=False)[0] == []
    _, info = gates.check_stderr({"a": clean + WARN + WARN}, "S-regex")
    assert info["warning_lines"] == {"a": 2} and info["total_warning_lines"] == 2


def test_count_set_version_and_misc_gates(tmp_path):
    assert gates.check_store_counts("S", 100, 100, 100) == []
    assert gates.check_store_counts("S", 100, 100, 99) == ["[counts] S: qdrant 100 != elasticsearch 99", "[counts] S: elasticsearch 99 != receipts 100"]
    assert gates.check_store_counts("S", None, 100, 100)[0].startswith("[counts] S: store counts incomplete")
    assert gates.check_reused_census("ft512o64", {"qdrant": 705204, "es": 705204}, {"qdrant": 705204, "es": 705204}) == []
    assert gates.check_reused_census("ft512o64", {"qdrant": 705204, "es": 705204}, {"qdrant": 705203, "es": 705204}) == \
        ["[census] ft512o64: live qdrant 705,203 != frozen 705,204"]
    assert gates.check_reused_census("ft512o64", None, {"qdrant": 1, "es": 1}) == ["[census] ft512o64: no frozen census"]
    assert gates.check_reused_census("ft512o64", {"qdrant": 1, "es": 1}, None) == ["[census] ft512o64: no live census"]
    ids = [f"q{i}" for i in range(5)]
    frozen = {"decision": {"n": 5, "sha256": gates.ids_sha256(ids)}, "sensitivity": {"n": 6, "sha256": gates.ids_sha256(ids + ["q9"])}}
    assert gates.check_sets(ids, ids + ["q9"], frozen) == []
    bad = gates.check_sets(ids[:4], ids + ["q8"], frozen)
    assert bad[0] == "[sets] decision set: n = 4, frozen 5" and bad[2].startswith("[sets] sensitivity set: ids sha256")
    assert gates.check_sets(ids, ids, {}) == ["[sets] decision set: no frozen n / sha256 (absent)", "[sets] sensitivity set: no frozen n / sha256 (absent)"]
    assert gates.ids_sha256(["b", "a"]) == hashlib.sha256(b"a\nb\n").hexdigest()
    ok, label = gates.check_one_version({"a/3": {"S": ["1.6.25+67e53fd"], "ft512o64": ["1.6.25+67e53fd"]}, "a/5": {"S-regex": ["1.6.25+67e53fd"]}})
    assert ok == [] and label == "1.6.25+67e53fd"
    bad, label = gates.check_one_version({"a/3": {"S": ["1.6.25+67e53fd"]}, "d/3": {"S": ["1.6.26+abcdef0"]}})
    assert bad and label is None
    assert gates.check_one_version({"a/3": {}})[0] == ["[version] no server version recorded on any row"]
    for bad_label in ("unknown", "error:timeout", "1.6.25", "1.6.25+zz"):
        assert gates.check_one_version({"a/3": {"S": [bad_label]}})[0] == [f"[version] server version label {bad_label!r} is not version+git_sha"]
    sc = {"missing_by_arm": {"S": 0, "ft512o64s": 2}, "n_questions": 2437, "refusals": ["S-regex: unlocated_share=0.02 > 0.01", "records_without_text=3 (…)"],
          "request_mismatch_by_arm": {"ft512o64": 1}, "request_unverified_by_arm": {"S": 4},
          "server_versions": {"S": ["1.6.25+67e53fd"], "ft512o64s": ["1.6.25+67e53fd", "1.6.26+abcdef0"], "ft512o64": ["1.6.25+67e53fd"]}}
    bad, _ = gates.check_retrieval(sc, ("S", "ft512o64s", "ft512o64", "S-regex"), 2437)
    assert bad == [("ft512o64s", "[rows] ft512o64s: 2 question(s) without an ok retrieval row"),
                   ("S-regex", "[score_refusal] S-regex: unlocated_share=0.02 > 0.01"),
                   (None, "[score_refusal] records_without_text=3 (…)"),
                   ("ft512o64", "[request] ft512o64: 1 row(s) request mismatch"),
                   ("S", "[request] S: 4 row(s) request unverified"),
                   ("ft512o64s", "[version] ft512o64s: 2 server versions in its rows"),
                   ("S-regex", "[version] S-regex: no server version recorded on its rows")]
    bad, _ = gates.check_retrieval({"missing_by_arm": {}, "n_questions": 2436, "refusals": []}, ("S",), 2437)
    assert bad == [(None, "[rows] 2436 questions scored, expected 2437"), (None, "[version] score.json records no server_versions (absent)"),
                   ("S", "[version] S: no server version recorded on its rows")]
    api = {"request_extra": {"rerank": True, "use_graph": False, "retrieval_mode": "hybrid"}, "top_k": 10}
    fr = {"rerank": True, "use_graph": False, "retrieval_mode": "hybrid", "top_k": 10}
    assert gates.check_request_fields(api, fr) == []
    assert gates.check_request_fields({"request_extra": {"rerank": False, "use_graph": False, "retrieval_mode": "hybrid"}}, fr) == \
        ["[request] request field rerank = False, frozen True", "[request] request field top_k not set in the config"]
    assert gates.check_request_fields(None, fr) == ["[request] no config api block to check (absent)"]
    assert gates.check_request_fields(api, None) == ["[request] no frozen request fields for this setting (absent)"]
    p = tmp_path / "shard.jsonl"
    p.write_text("x\n")
    sha = gates.sha256_file(str(p))
    pins = {f"main-{i:02d}": sha for i in range(16)} | {"sentinel": sha}
    assert gates.check_shard_hashes(pins, dict(pins)) == []
    assert gates.check_shard_hashes({"main-00": sha}, {"main-00": sha}) == ["[shards] 1 pinned shard files, expected 17"]
    assert gates.check_shard_hashes({}, {}) == ["[shards] 0 pinned shard files, expected 17"]
    actual = {k: v for k, v in pins.items() if k != "sentinel"} | {"main-00": "0" * 64, "extra": sha}
    bad = gates.check_shard_hashes(pins, actual)
    assert bad == [f"[shards] shard main-00: sha256 {'0' * 12} != pinned {sha[:12]}", "[shards] shard sentinel: not hashed",
                   "[shards] shard extra: in the run but not pinned"]
    cos = [{"chunk_id": f"c{i}", "cosine": 0.9995} for i in range(200)]
    pinned = gates.ids_sha256(c["chunk_id"] for c in cos)
    assert gates.check_reembed(cos, 0.999, 200, pinned)[0] == []
    assert gates.check_reembed(cos, 0.999, 200, None)[0] == ["[reembed] no pinned sample ids sha256 (absent)"]
    assert gates.check_reembed(cos, 0.999, 200, "0" * 64)[0] == [f"[reembed] cosines cover ids sha256 {pinned[:12]}, pinned sample {'0' * 12}"]
    cos[7]["cosine"] = 0.998
    bad, desc = gates.check_reembed(cos, 0.999, 200, pinned)
    assert bad == ["[reembed] 1 chunk(s) below cosine 0.999 (min 0.998000)"] and desc["n_below"] == 1
    assert gates.check_reembed(cos[:199], 0.999, 200, pinned)[0][0] == "[reembed] 199 cosines, expected 200"
    assert gates.check_model_ids({"embedding_model": "m", "reranker_model": "r"}, {"embedding_model": "m", "reranker_model": "r2"}) == \
        ["[model_ids] reranker_model: run 'r2' != freeze 'r'"]
    assert gates.check_model_ids({"embedding_model": "m"}, {"embedding_model": "m", "reranker_model": "r"}) == ["[model_ids] reranker_model: not recorded at the freeze"]
    assert gates.check_provenance(CLEAN_PROV) == []
    assert gates.check_provenance(None) == ["[provenance] no harness provenance recorded (absent)"]
    assert gates.check_provenance({**CLEAN_PROV, "dirty": True, "untracked": 2}) == \
        ["[provenance] harness tree dirty = True (clean tree required)", "[provenance] harness tree untracked = 2 (0 required)"]
    assert gates.check_provenance({**CLEAN_PROV, "dirty": None, "commit": None})[0] == "[provenance] harness tree dirty = None (clean tree required)"
    ov = gates.chunk_id_overlap(["a", "b", "c"], ["b", "c", "d", "e"])
    assert ov == {"n_S": 3, "n_S_regex": 4, "shared": 2, "share_of_S": pytest.approx(2 / 3), "share_of_S_regex": 0.5}
    old = [{"question_id": "q1", "hits": [{"chunk_id": "a"}, {"chunk_id": "b"}]}, {"question_id": "q2", "hits": [{"chunk_id": "c"}]},
           {"question_id": "q3", "status": "error", "hits": []}]
    new = [{"question_id": "q1", "hits": [{"chunk_id": "b"}, {"chunk_id": "a"}]}, {"question_id": "q2", "hits": [{"chunk_id": "d"}]},
           {"question_id": "q3", "hits": [{"chunk_id": "e"}]}]
    assert gates.top5_drift(old, new) == {"k": 5, "n_compared": 2, "n_same": 1, "share_same": 0.5}


def test_decision_integrity_gate():
    good = {"forced_unchecked": [], "tie_break": "paired_ladder", "stratum_correction": "bonferroni",
            "margins": {"M_ni": 2.0, "M_mid": 2.0, "M_high": 4.0}, "margins_match_noise": True,
            "inputs": {"set": "clean_excl_noise", "n": 2035}, "ladder": {"order": ["ft512o64", "S", "ft512o64s"]}}
    assert gates.check_decision_integrity(good, "B", 2035) == []
    assert gates.check_decision_integrity(good, "A", 2035) == ["[decision_json] ladder order ['ft512o64', 'S', 'ft512o64s'] does not follow cost order A"]
    bad = {**good, "forced_unchecked": ["noise.json missing"], "tie_break": "cheapest", "stratum_correction": "none",
           "margins": {"M_ni": 3.0, "M_mid": 2.0}, "margins_match_noise": False, "inputs": {"set": "clean", "n": 2335},
           "ladder": {"order": ["S", "ft512o64"]}}
    out = gates.check_decision_integrity(bad, "B", 2035)
    assert [g.split("]")[0] for g in out] == ["[decision_json"] * 8
    assert out[0].startswith("[decision_json] decide ran with --force-unchecked") and "margins.M_ni = 3.0, frozen 2.0" in out[3]
    assert gates.check_decision_integrity({}, "B", 2035)[:2] == ["[decision_json] tie_break None, expected 'paired_ladder'",
                                                                 "[decision_json] stratum_correction None, expected 'bonferroni'"]


# ------------------------------------------------------------------- the public-file guard


def test_assert_public():
    report.assert_public({"a": {"b": [1, "fine", {"c": "S − ft512o64s = +1.00"}, ("t", 2)]}})
    report.assert_public({"x": "1 − 0.05/11; sections/1; chars/4; vLLM /v1/models; 1.6.25+67e53fd; k = 5"})
    for bad in ({"path": "x"}, {"question_id": "q"}, {"out_dir": "x"}, {"chunk_ids": []}, {"content": ""}, {"doc_id": 1},
                {"x": "see /rag/ingest/oa"}, {"x": "out_dir=/rag/x/y"}, {"x": "file:/home/x"}, {"x": "/scratch/u/"},
                {"x": "/lus/g/"}, {"x": "~/Development/x"}, {"x": "http://localhost:24040"}, {"x": "localhost"},
                {"x": "svc:4242"}, {"x": "PMC1234567"}, {"x": "sub_0123abcd-e7a4"},
                {"x": "7d0e8e2f-1c2b-4a1e-9b3f-0a1b2c3d4e5f"}, {"x": "10.0.0.1"}, {"x": "somehost.example.org"},
                {"x": "box.internal"}, {"x": "node.local"}, {"x": "svc.example.net"}, {"x": "y" * 1501},
                {"/rag/x/": 1}, {"host-name.anl.gov": 1}, {"x": ("ok", "/home/u/")}, {"x": ["ok", {"y": "~/x"}]}):
        with pytest.raises(report.ReportError):
            report.assert_public(bad)


# ------------------------------------------------------------------- synthetic per-question fixture


FAMILIES = ["plos-one", "sci-reports", "virology", "infectious-disease", "microbiology", "molbio-immunology", "multidisciplinary", "other"]
BANDS = ["short", "medium", "long"]


def make_rows(n: int, seed: int, shifts: dict[str, float], noise_every: int = 7, ambiguous_every: int = 50,
              s_penalty: tuple[str, str, float] | None = None) -> list[dict[str, Any]]:
    """Per-question rows for arms ft512o64, ft512o64s, S, S-regex, sent512s. The baseline hits
    with p = 0.78; each other arm is the baseline with 3 % of questions flipped down and
    (3 % + shift) flipped up (net = ``shifts[arm]`` points, discordance ≈ 6 % + shift).
    S-regex copies S with 1 % discordance. ``s_penalty = (dim, value, points)`` additionally
    flips S down inside one stratum (a stratum-guard fixture)."""
    rng = np.random.default_rng(seed)
    base = rng.random(n) < 0.78
    rows = []
    arms = {"ft512o64": base}
    m, h = float((~base).mean()), float(base.mean())
    strata = [(FAMILIES[i % 8], BANDS[i % 3]) for i in range(n)]
    for a in ("ft512o64s", "S", "sent512s"):
        v = base.copy()
        q_up = min(1.0, (0.03 + shifts.get(a, 0) / 100) / m)
        flip_up = (~base) & (rng.random(n) < q_up)
        flip_down = base & (rng.random(n) < 0.03 / h)
        v[flip_up] = True
        v[flip_down] = False
        arms[a] = v
    if s_penalty:
        dim, val, pts = s_penalty
        idx = 0 if dim == "journal_family" else 1
        inside = np.array([s[idx] == val for s in strata])
        hit_in = inside & arms["S"]
        share = pts / 100 * inside.sum() / max(1, hit_in.sum())
        down = hit_in & (rng.random(n) < share)
        arms["S"] = arms["S"].copy()
        arms["S"][down] = False
    v = arms["S"].copy()
    mm = rng.random(n) < 0.01
    v[mm] = ~v[mm]
    arms["S-regex"] = v
    for i in range(n):
        r = {"question_id": f"qid-{i:05d}", "pmcid": f"doc{i}", "ambiguous_gold": i % ambiguous_every == 0,
             "noise_subset": i % noise_every == 0,
             "strata": {"journal_family": strata[i][0], "length_band": strata[i][1], "section_type": "results", "year_band": "unknown"},
             "arms": {}}
        for a, vec in arms.items():
            hit = int(vec[i])
            ctx = 2300 if a == "ft512o64" else 1900
            r["arms"][a] = {"1": {"primary": int(hit and i % 2 == 0), "coverage": 1.0, "strict": hit, "ctx_tokens": ctx // 5},
                            "5": {"primary": hit, "coverage": float(hit), "strict": hit, "ctx_tokens": ctx},
                            "10": {"primary": int(hit or i % 5 == 0), "coverage": 1.0, "strict": hit, "ctx_tokens": ctx * 2},
                            "locate": {"offsets": 5, "other_record": 5}, "quote_occurrences": 1, "top1_article": f"doc{i}"}
        rows.append(r)
    return rows


def score_json(arm_names: list[str], n: int, version: str = "1.6.25+67e53fd") -> dict[str, Any]:
    return {"arms": arm_names, "baseline": "ft512o64", "primary_set": "clean_excl_noise", "sensitivity_set": "clean",
            "bootstrap": {"iters": ITERS, "seed": SEED}, "missing_by_arm": dict.fromkeys(arm_names, 0), "n_questions": n,
            "refusals": [], "request_mismatch_by_arm": dict.fromkeys(arm_names, 0), "request_unverified_by_arm": dict.fromkeys(arm_names, 0),
            "server_versions": {a: [version] for a in arm_names}, "tokenizer": "chars/4", "tokens_estimated": True}


def decision_json(rows: list[dict[str, Any]], ladder_alpha: float = 0.05) -> dict[str, Any]:
    """What the harness's decide writes for the three-arm config: ft512o64s − ft512o64 at
    95 % and a paired ladder over [ft512o64, S, ft512o64s] (Order B ranks) at the ladder's
    own level, computed with the harness's own functions on the same rows."""
    rs = set_rows(rows, "clean_excl_noise")
    vec = {a: np.array([r["arms"][a]["5"]["primary"] for r in rs]) for a in ("ft512o64", "S", "ft512o64s")}
    d = paired_diff(vec["ft512o64s"], vec["ft512o64"], ITERS, SEED, alpha=0.05)
    winner, steps = decide.paired_ladder(["ft512o64", "S", "ft512o64s"], vec, ITERS, SEED, ladder_alpha)
    return {"winner": winner, "forced_unchecked": [], "tie_break": "paired_ladder", "stratum_correction": "bonferroni",
            "margins": {"M_ni": 2.0, "M_mid": 2.0, "M_high": 4.0}, "margins_match_noise": True,
            "inputs": {"set": "clean_excl_noise", "n": len(rs)},
            "arms": {"ft512o64s": {"point": round(d["point"], 4), "lo": round(d["lo"], 4), "hi": round(d["hi"], 4), "pass": True},
                     "S": {"pass": True}},
            "ladder": {"order": ["ft512o64", "S", "ft512o64s"], "alpha_two_sided": ladder_alpha, "level": 1 - ladder_alpha, "steps": steps}}


def write_setting(d: Path, rows: list[dict[str, Any]], arms: list[str], with_decision: bool, ladder_alpha: float = 0.05) -> None:
    d.mkdir(parents=True, exist_ok=True)
    sub = [{**r, "arms": {a: r["arms"][a] for a in arms}} for r in rows]
    (d / "per_question.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sub))
    (d / "score.json").write_text(json.dumps(score_json(arms, len(rows))))
    if with_decision:
        (d / "decision.json").write_text(json.dumps(decision_json(sub, ladder_alpha)))


DEC_ARMS = ["ft512o64", "S", "ft512o64s"]
REP_ARMS = ["ft512o64", "ft512o64s", "S", "S-regex", "sent512s"]
REQUEST = {"a": {"rerank": True, "use_graph": False, "retrieval_mode": "hybrid", "top_k": 10},
           "d": {"rerank": False, "use_graph": False, "retrieval_mode": "hybrid", "top_k": 10},
           "b": {"rerank": False, "use_graph": False, "retrieval_mode": "vector", "top_k": 10},
           "c": {"rerank": False, "use_graph": False, "retrieval_mode": "bm25", "top_k": 10}}


def build_fixture(tmp_path: Path, shifts: dict[str, float], *, n: int = 1400, seed: int = 1,
                  break_decision: bool = False, bad_sregex: bool = False, drop_reembed: bool = False,
                  drop_sregex: bool = False, drop_sent512s_rows: bool = False, s_penalty=None,
                  ladder_alpha: float = 0.05, break_ladder: bool = False, wrong_request: bool = False,
                  refuse_sregex: bool = False, logs_disagree: bool = False, drop_logs: bool = False) -> tuple[Path, Path]:
    rows = make_rows(n, seed, shifts, s_penalty=s_penalty)
    rep_arms = [a for a in REP_ARMS if not (drop_sregex and a == "S-regex") and not (drop_sent512s_rows and a == "sent512s")]
    for s in ("a", "d", "b", "c"):
        write_setting(tmp_path / f"dec-{s}", rows, DEC_ARMS, True, ladder_alpha)
        write_setting(tmp_path / f"rep-{s}", rows, rep_arms, False)
        cfg = {"api": {"request_extra": {k: v for k, v in REQUEST[s].items() if k != "top_k"}, "top_k": REQUEST[s]["top_k"]}}
        if wrong_request and s == "a":
            cfg["api"]["request_extra"]["rerank"] = False
        (tmp_path / f"config-{s}.json").write_text(json.dumps(cfg))
    if break_decision or break_ladder:
        p = tmp_path / "dec-a" / "decision.json"
        d = json.loads(p.read_text())
        if break_decision:
            d["arms"]["ft512o64s"]["lo"] = round(d["arms"]["ft512o64s"]["lo"] + 0.5, 4)
        if break_ladder:
            d["ladder"]["steps"][0]["lo"] = round(d["ladder"]["steps"][0]["lo"] + 0.5, 4)
        p.write_text(json.dumps(d))
    if refuse_sregex:
        p = tmp_path / "rep-a" / "score.json"
        sc = json.loads(p.read_text())
        sc["refusals"] = ["S-regex: unlocated_share=0.02 > 0.01 (unlocated 3, offsets_unverified 0)"]
        p.write_text(json.dumps(sc))
    rs, rs_s = set_rows(rows, "clean_excl_noise"), set_rows(rows, "clean")
    arms_man = {}
    for arm, build in (("S", "1"), ("S-regex", "2")):
        if drop_sregex and arm == "S-regex":
            continue
        rd = tmp_path / "receipts" / arm
        rd.mkdir(parents=True)
        recs = arm_receipts(arm, build)
        if bad_sregex and arm == "S-regex":
            recs["main-05"]["provenance"]["segmenter_version"] = "punkt-abbrev-v1"
        rpaths, spaths = {}, {}
        for label, r in recs.items():
            (rd / f"{label}.receipt.json").write_text(json.dumps(r))
            rpaths[label] = str(rd / f"{label}.receipt.json")
            (rd / f"{label}.ingest-stderr.txt").write_text("[chunker] sentence\n" + (WARN if arm == "S-regex" else ""))
            spaths[label] = str(rd / f"{label}.ingest-stderr.txt")
        tail = "[chunker] sentence\n" + (WARN if (arm == "S-regex") != (logs_disagree and arm == "S") else "")
        (rd / "sentinel-logs.json").write_text(json.dumps({"data": {"stderr": tail, "exit_code": 0}}))
        total = sum(len(set(r["chunk_ids"])) for r in recs.values())
        arms_man[arm] = {"receipts": rpaths, "stderr": spaths, "qdrant_count": total, "es_count": total}
        if not drop_logs:
            arms_man[arm]["sentinel_logs_json"] = str(rd / "sentinel-logs.json")
    shards = {}
    for label in [f"main-{i:02d}" for i in range(16)] + ["sentinel"]:
        p = tmp_path / "shards" / f"{label}.jsonl"
        p.parent.mkdir(exist_ok=True)
        p.write_text(f"{label}\n")
        shards[label] = str(p)
    cosines = [{"chunk_id": f"c{i}", "cosine": 0.9999} for i in range(200)]
    cos = tmp_path / "cosines.json"
    cos.write_text(json.dumps(cosines))
    frozen = {"cost_order": "B", "shards": {k: gates.sha256_file(v) for k, v in shards.items()},
              "images": {"S": {"build": "1", "commit": COMMIT}, "S-regex": {"build": "2", "commit": COMMIT}},
              "sets": {"decision": {"n": len(rs), "sha256": gates.ids_sha256(r["question_id"] for r in rs)},
                       "sensitivity": {"n": len(rs_s), "sha256": gates.ids_sha256(r["question_id"] for r in rs_s)},
                       "all_rows": n},
              "sentinel": {"n_chunks": 168, "doc_ids_also_in_main_shards": 0},
              "reused_census": {"ft512o64": {"qdrant": 705204, "es": 705204}, "ft512o64s": {"qdrant": 859280, "es": 859280},
                                "sent512s": {"qdrant": 850312, "es": 850312}},
              "model_ids": {"embedding_model": "SFR-Embedding-Mistral", "reranker_model": "bge-reranker"},
              "reembed": {"n": 200, "cosine_threshold": 0.999, "ids_sha256": gates.ids_sha256(c["chunk_id"] for c in cosines)},
              "frozen_request": REQUEST}
    fz = tmp_path / "frozen-inputs.json"
    fz.write_text(json.dumps(frozen))
    man = {"settings": {s: {"decision_dir": str(tmp_path / f"dec-{s}"), "reporting_dir": str(tmp_path / f"rep-{s}"),
                            "config": str(tmp_path / f"config-{s}.json")} for s in "adbc"},
           "arms": arms_man,
           "reused_census_live": {k: dict(v) for k, v in frozen["reused_census"].items()},
           "shard_files": shards,
           "reembed": ({} if drop_reembed else {"cosines": str(cos)}) | {"run_model_ids": dict(frozen["model_ids"])}}
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(man))
    return mp, fz


def build(mp: Path, fz: Path, prov=CLEAN_PROV) -> dict[str, Any]:
    return report.build_report(json.loads(mp.read_text()), json.loads(fz.read_text()), {"prereg_sha256": "x" * 64, "checked": True}, prov)


TIE = {"ft512o64s": 4.0, "S": 4.0, "sent512s": 3.0}


def test_report_end_to_end_tie_and_superior(tmp_path):
    mp, fz = build_fixture(tmp_path / "tie", TIE)
    rep = build(mp, fz)
    report.assert_public(rep)
    a = rep["settings"]["a"]
    assert a["gates"] == {} and rep["server_version"] == "1.6.25+67e53fd" and a["G1"] is True
    assert rep["decision"] == "S" and rep["row"] == 3 and rep["other_order"]["decision"] == "ft512o64s"
    assert a["cross_check"]["d1_match"] and all(st["match"] for st in a["cross_check"]["ladder_steps"]) and a["cross_check"]["ladder_steps"]
    assert a["H3"]["any_worker"] and rep["H3_any_worker"]
    assert "(on this corpus; chunk-id overlap 0.0 %)" in a["H3"]["sentence"]  # synthetic ids differ by arm
    assert set(a["contrasts"]) == {"C1", "C2", "C3", "C4"} and "p_holm" in a["contrasts"]["C1"] and "p_holm" not in a["contrasts"]["C3"]
    assert a["contrasts"]["C3"]["in_family"] is False and rep["settings"]["d"]["contrasts"]["C3"]["in_family"]
    assert "p_holm" in rep["settings"]["d"]["contrasts"]["C3"]
    assert a["n_decision"] == a["n_five_arm"] and a["stratum_guard"]["k"] == 11
    assert rep["settings"]["d"]["G1"] is None and "H3" not in rep["settings"]["d"] and rep["settings"]["d"]["D3"]
    # the guard's interval is wider than the 95 % one on every tested stratum (1 − 0.05/11)
    for st in a["stratum_guard"]["tested"]:
        assert st["hi"] > st["hi95"] and st["lo"] < st["lo95"]
    assert rep["files"]["chunk_id_overlap"]["share_of_S"] == 0.0 and rep["files"]["harness_provenance"] == CLEAN_PROV
    assert rep["sentences"][0].startswith("Deploy S: in setting (a)") and rep["sentences"][1].startswith("Segmenter: S-regex − S =")
    mp2, fz2 = build_fixture(tmp_path / "sup", {"ft512o64s": 4.0, "S": 12.0, "sent512s": 3.0})
    rep2 = build(mp2, fz2)
    assert rep2["row"] == 1 and rep2["other_order"]["row"] == 1
    assert json.dumps(rep2)


def test_report_failing_stratum_row7(tmp_path):
    mp, fz = build_fixture(tmp_path / "st", TIE, n=4800, s_penalty=("journal_family", "virology", 12.0))
    rep = build(mp, fz)
    a = rep["settings"]["a"]
    assert rep["row"] == 7 and a["sequence"]["result"]["strata_failed"][0]["value"] == "virology"
    assert "failed the stratum guard (journal_family = virology, point" in rep["sentences"][0]
    assert "at 1 − 0.05/11)" in rep["sentences"][0]


def test_report_gate_not_evaluable_paths(tmp_path):
    mp, fz = build_fixture(tmp_path / "x", TIE, break_decision=True)
    rep = build(mp, fz)
    assert rep["decision"] == "GATE-NOT-EVALUABLE" and rep["row"] == 8
    assert any(g.startswith("[cross_check] (a) decision.json ft512o64s") for g in rep["settings"]["a"]["gates"]["decision"])
    assert rep["settings"]["a"]["H3"]["any_worker"]
    assert rep["sentences"][0].startswith("GATE-NOT-EVALUABLE: cross_check ×1. The contrasts")
    # a ladder step that does not reproduce (and the ladder at its own level, 0.01)
    mp, fz = build_fixture(tmp_path / "lad", TIE, ladder_alpha=0.01)
    assert build(mp, fz)["row"] == 3
    mp, fz = build_fixture(tmp_path / "lad2", TIE, ladder_alpha=0.01, break_ladder=True)
    rep = build(mp, fz)
    assert rep["row"] == 8 and any("ladder step" in g for g in rep["settings"]["a"]["gates"]["decision"])
    # G1 fails (ft512o64s not better than the baseline): voids the decision, not H3; only in (a)
    mp, fz = build_fixture(tmp_path / "g1", {"ft512o64s": 0.0, "S": 0.0, "sent512s": 0.0})
    rep = build(mp, fz)
    assert rep["row"] == 8 and any(g.startswith("[G1] (a)") for g in rep["settings"]["a"]["gates"]["decision"])
    assert not any("[G1]" in g for g in rep["settings"]["d"]["gates"].get("decision", []))
    # an S-regex receipt with the wrong segmenter voids H3 only; C3 leaves the Holm family in (d)
    mp, fz = build_fixture(tmp_path / "h3", TIE, bad_sregex=True)
    rep = build(mp, fz)
    assert rep["row"] == 3 and not rep["H3_any_worker"]
    assert "only where Punkt is asserted (reason: not evaluable: segmenter ×1;" in rep["sentences"][1]
    d = rep["settings"]["d"]["contrasts"]
    assert d["C3"]["voided_by"] and d["C3"]["in_family"] is False and "p_holm" not in d["C3"] and d["C1"]["in_family"]
    # absent inputs are not passed: re-embed cosines, the logs-endpoint capture, the request config
    mp, fz = build_fixture(tmp_path / "re", TIE, drop_reembed=True)
    rep = build(mp, fz)
    assert rep["row"] == 8 and "[reembed] re-embed cosines absent" in rep["settings"]["a"]["gates"]["decision"]
    mp, fz = build_fixture(tmp_path / "logs", TIE, drop_logs=True)
    rep = build(mp, fz)
    assert rep["row"] == 8 and any(g.startswith("[stderr_agree] S: no logs-endpoint") for g in rep["settings"]["a"]["gates"]["decision"])
    mp, fz = build_fixture(tmp_path / "logs2", TIE, logs_disagree=True)
    rep = build(mp, fz)
    assert rep["row"] == 8 and any(g.startswith("[stderr_agree] S: sentinel warning lines differ") for g in rep["settings"]["a"]["gates"]["decision"])
    mp, fz = build_fixture(tmp_path / "req", TIE, wrong_request=True)
    rep = build(mp, fz)
    assert rep["row"] == 8 and "[request] (a) request field rerank = False, frozen True" in rep["settings"]["a"]["gates"]["decision"]
    assert rep["settings"]["d"]["sequence"]["row"] == 3
    # a dirty harness tree
    mp, fz = build_fixture(tmp_path / "prov", TIE)
    rep = build(mp, fz, {**CLEAN_PROV, "dirty": True})
    assert rep["row"] == 8 and "provenance" in rep["settings"]["a"]["gate_counts"]["decision"]
    rep = build(mp, fz, None)
    assert rep["row"] == 8
    # a five-arm refusal naming S-regex voids H3, never the decision
    mp, fz = build_fixture(tmp_path / "ref", TIE, refuse_sregex=True)
    rep = build(mp, fz)
    assert rep["row"] == 3 and not rep["H3_any_worker"] and "score_refusal" in rep["settings"]["a"]["gate_counts"]["H3"]
    assert "decision" not in rep["settings"]["a"]["gates"]


def test_report_survives_sregex_or_sent512s_missing(tmp_path):
    mp, fz = build_fixture(tmp_path / "nosr", TIE, drop_sregex=True)
    rep = build(mp, fz)
    report.assert_public(rep)
    a = rep["settings"]["a"]
    assert rep["row"] == 3 and rep["decision"] == "S" and "decision" not in a["gates"]
    assert not rep["H3_any_worker"] and a["H3"]["evaluable"] is False and a["D3"] is None
    assert rep["sentences"][1].startswith("Segmenter: S-regex − S = not computed; S may be deployed only where Punkt is asserted (reason: not evaluable: arm_missing ×2")
    assert a["contrasts"]["C3"] == {"pair": "S-regex − S", "absent": True, "in_family": False}
    assert "p_holm" in a["contrasts"]["C1"] and a["arms_missing"] == ["S-regex"]
    assert rep["files"]["chunk_id_overlap"] is None
    mp, fz = build_fixture(tmp_path / "nosent", TIE, drop_sent512s_rows=True)
    rep = build(mp, fz)
    a = rep["settings"]["a"]
    assert rep["row"] == 3 and a["contrasts"]["C1"]["absent"] and "C1" in a["gates"] and "decision" not in a["gates"]
    assert rep["settings"]["d"]["contrasts"]["C2"]["in_family"]


def test_report_refuses_bad_pins(tmp_path):
    mp, fz = build_fixture(tmp_path / "pins", TIE)
    f = json.loads(fz.read_text())
    with pytest.raises(report.ReportError):
        report.build_report(json.loads(mp.read_text()), {**f, "cost_order": None}, {}, CLEAN_PROV)
    with pytest.raises(report.ReportError):
        report.build_report({**json.loads(mp.read_text()), "frozen_order": "A"}, f, {}, CLEAN_PROV)
    # an unexpected arm in the five-arm output refuses
    p = tmp_path / "pins" / "rep-a" / "score.json"
    sc = json.loads(p.read_text())
    sc["arms"].append("sent512sh")
    p.write_text(json.dumps(sc))
    with pytest.raises(report.ReportError):
        build(mp, fz)


def test_report_cli_and_prereg_refusal(tmp_path, capsys, monkeypatch):
    mp, fz = build_fixture(tmp_path / "cli", TIE)
    prereg = tmp_path / "PREREG.md"
    prereg.write_text("# draft\n")
    sha = gates.sha256_file(str(prereg))
    out = tmp_path / "artifacts" / "h2h-report.json"
    monkeypatch.setattr(report, "harness_provenance", lambda repo=None: dict(CLEAN_PROV))
    assert cli.main(["report", "--manifest", str(mp), "--frozen-inputs", str(fz), "--prereg", str(prereg),
                     "--prereg-sha256", sha, "--out", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["prereg"] == {"prereg_sha256": sha, "checked": True} and rep["files"]["harness_provenance"] == CLEAN_PROV
    report.assert_public(rep)
    printed = capsys.readouterr().out
    assert "Deploy S:" in printed and "Segmenter:" in printed
    with pytest.raises(report.ReportError):
        cli.main(["report", "--manifest", str(mp), "--frozen-inputs", str(fz), "--prereg", str(prereg),
                  "--prereg-sha256", "0" * 64, "--out", str(out)])
    with pytest.raises(SystemExit):  # --prereg and --prereg-sha256 are required
        cli.main(["report", "--manifest", str(mp), "--frozen-inputs", str(fz), "--out", str(out)])
    assert cli.main(["nope"]) == 2
    prov = report.harness_provenance(REPO)  # the real helper runs on this checkout and returns only public keys
    assert set(prov) == set(report.PROVENANCE_KEYS)


# ------------------------------------------------------------------- dryrun_check, reembed, configs, power


def test_dryrun_check(tmp_path):
    r = receipt("S-regex", "sentinel", 40, 168, build="2")
    other = receipt("S", "sentinel", 40, 168)
    other["chunk_ids"] = list(r["chunk_ids"])
    entry = {"chunk_method": "sentence", "chunk_size": 512, "model": "m", "dim": 4096,
             "provenance": {"chunk_overlap": 0, "embedding_api": "openai", "spec_hash": "29236085",
                            "chunk_params": {"size_unit": "tokens", "size_tolerance": 0.25, "min_piece": 128, "structure_cuts": "sections/1"}}}
    logs = {"data": {"stderr": "x\n" + WARN, "exit_code": 0, "stdout": ""}}
    bad, info = dryrun_check.run_checks("S-regex", r, summary={"failed_shards": [], "n_shards_without_provenance": 0, "n_chunks": 168},
                                        stderr_text="x\n" + WARN, logs=logs, qdrant=168, es=168, entry=entry, expect_build="2",
                                        expect_commit=COMMIT, expect_sentinel_chunks=168, other=other)
    assert bad == [] and info["chunk_id_overlap"]["share_of_S"] == 1.0 and info["logs_endpoint"]["warning_lines"] == {"sentinel": 1}
    bad, _ = dryrun_check.run_checks("S-regex", r, logs=logs, qdrant=168, es=167, expect_build="2", expect_commit=COMMIT)
    assert bad == ["[counts] S-regex: qdrant 168 != elasticsearch 167", "[counts] S-regex: elasticsearch 167 != receipts 168"]
    bad, _ = dryrun_check.run_checks("S", other, expect_build="1", expect_commit=COMMIT)
    assert bad == ["S: no worker stderr given (neither --stderr-file nor --logs-json)"]
    bad, _ = dryrun_check.run_checks("S", other, stderr_text="clean\n", logs={"data": {"stderr": WARN, "exit_code": 0}}, expect_build="1", expect_commit=COMMIT)
    assert "[stderr_lines] S/sentinel: 1 'NLTK Punkt is unavailable' line(s)" in bad[0] and bad[-1].endswith("differs between the staged file and the logs endpoint")
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(r))
    lp = tmp_path / "l.json"
    lp.write_text(json.dumps(logs))
    assert cli.main(["dryrun", "--arm", "S-regex", "--receipt", str(rp), "--logs-json", str(lp), "--expect-build", "2", "--expect-commit", COMMIT]) == 0
    assert cli.main(["dryrun", "--arm", "S-regex", "--receipt", str(rp), "--logs-json", str(lp)]) == 1  # absent pins fail
    assert cli.main(["dryrun", "--arm", "S", "--receipt", str(rp), "--logs-json", str(lp), "--expect-build", "1", "--expect-commit", COMMIT]) == 1


def test_reembed_select_and_verify(tmp_path):
    pop = [f"id{i:04d}" for i in range(500)]
    a = reembed_sample.select_ids(pop, 200, 7)
    b = reembed_sample.select_ids(list(reversed(pop)) + pop[:3], 200, 7)
    assert a == b and len(set(a)) == 200 and reembed_sample.select_ids(pop, 200, 8) != a
    with pytest.raises(ValueError):
        reembed_sample.select_ids(pop[:100], 200, 7)
    recs = [tmp_path / f"r{i}.json" for i in range(2)]
    for i, p in enumerate(recs):
        p.write_text(json.dumps({"chunk_ids": pop[i * 250:(i + 1) * 250]}))
    out = tmp_path / "ids.json"
    assert cli.main(["reembed", "select", "--receipts", *map(str, recs), "--seed", "7", "--out", str(out)]) == 0
    sample = json.loads(out.read_text())
    assert sample["chunk_ids"] == a and sample["ids_sha256"] == gates.ids_sha256(a) and sample["population_distinct"] == 500
    cos = tmp_path / "cos.json"
    cos.write_text(json.dumps([{"chunk_id": c, "cosine": 0.9996} for c in a]))
    assert cli.main(["reembed", "verify", "--cosines", str(cos), "--ids", str(out)]) == 0
    assert cli.main(["reembed", "verify", "--cosines", str(cos), "--ids-sha256", sample["ids_sha256"]]) == 0
    assert cli.main(["reembed", "verify", "--cosines", str(cos)]) == 1  # no pinned ids: absent is not passed
    cos.write_text(json.dumps([{"chunk_id": c, "cosine": 0.9996} for c in a[:199]] + [{"chunk_id": "zz", "cosine": 0.9996}]))
    assert cli.main(["reembed", "verify", "--cosines", str(cos), "--ids", str(out)]) == 1
    assert reembed_sample.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert reembed_sample.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


ADDENDUM_C_LIKE = {
    "_comment": "Addendum C", "out_dir": "/private/out-sec-a", "manifest": "/private/manifest.jsonl", "corpus_root": "/private/c",
    "extracted": ["/private/e.jsonl"], "sample": {"seed": 20261104}, "passages": {"chars": 1200},
    "decide": {"k": 5, "min_stratum_n": 30, "stratum_correction": "bonferroni", "tie_break": "paired_ladder", "extra_key": 7},
    "margins": {"M_ni": 2.0, "M_mid": 2.0, "M_high": 4.0}, "noise": {"n_questions": 300, "arms": ["ft512o64", "ft512o0"], "planned_n": 2035},
    "api": {"base_url": "http://private:1", "api_key_file": "/private/key", "top_k": 10, "endpoint": "/v1/retrieve",
            "request_extra": {"rerank": True, "use_graph": False, "retrieval_mode": "hybrid"}, "max_in_flight": 4},
    "score": {"exclude_noise_subset": True},
    "arms": [{"name": "ft512o64", "collection_id": "oa-eval-ft512o64-x", "cost_class": "cheap", "baseline": True, "cost_rank": 2},
             {"name": "sent512s", "collection_id": "oa-eval-sent512s-x", "cost_class": "cheap", "baseline": False, "cost_rank": 7},
             {"name": "ft512o64s", "collection_id": "oa-eval-ft512o64s-x", "cost_class": "cheap", "baseline": False, "cost_rank": 8}],
}


def test_make_configs(tmp_path):
    src = tmp_path / "a.config.json"
    src.write_text(json.dumps(ADDENDUM_C_LIKE))
    diff = tmp_path / "diff.json"
    assert cli.main(["configs", "--addendum-c", f"a={src}", "--out-root", str(tmp_path / "h2h"), "--public-diff", str(diff)]) == 0
    dec = json.loads((tmp_path / "h2h" / "a" / "decision.config.json").read_text())
    rep = json.loads((tmp_path / "h2h" / "a" / "reporting.config.json").read_text())
    assert [(a["name"], a["cost_rank"], a["baseline"]) for a in dec["arms"]] == [("ft512o64", 1, True), ("S", 2, False), ("ft512o64s", 3, False)]
    assert [a["name"] for a in rep["arms"]] == ["ft512o64", "S", "ft512o64s", "S-regex", "sent512s"]
    # reused arms' collection ids are copied from the Addendum C config, never typed
    assert {a["name"]: a["collection_id"] for a in rep["arms"]} == {"ft512o64": "oa-eval-ft512o64-x", "ft512o64s": "oa-eval-ft512o64s-x",
                                                                      "sent512s": "oa-eval-sent512s-x", "S": "oa-eval-s", "S-regex": "oa-eval-sregex"}
    assert dec["out_dir"].endswith("out-h2h-dec-a") and rep["out_dir"].endswith("out-h2h-rep-a")
    for k in ("margins", "api", "score", "noise", "sample", "passages", "manifest", "corpus_root", "extracted"):
        assert dec[k] == ADDENDUM_C_LIKE[k] == rep[k]
    assert dec["decide"] == {**make_configs.DECIDE, "extra_key": 7}
    assert dec["_derived_from"]["addendum_c_config_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    d = json.loads(diff.read_text())
    assert set(d["settings"]["a"]["decision"]["diff"]) == {"/out_dir", "/arms"}
    report.assert_public(d)
    with pytest.raises(ValueError):
        make_configs.derive(dict(ADDENDUM_C_LIKE, decide={**ADDENDUM_C_LIKE["decide"], "stratum_correction": "none"}), "a", "decision", "/x", "sha")
    with pytest.raises(ValueError):  # a reused arm absent from the Addendum C config
        make_configs.derive(dict(ADDENDUM_C_LIKE, arms=ADDENDUM_C_LIKE["arms"][:2]), "a", "decision", "/x", "sha")


def test_power_reproduces_prereg_section_5(tmp_path):
    mod = runpy.run_path(str(STUDY / "power.py"), run_name="h2h_power")
    assert round(mod["ni_power"](0.05, -1.0), 2) == 0.52 and round(mod["ni_power"](0.05, -0.5), 2) == 0.86 and round(mod["ni_power"](0.05, 0.0), 2) == 0.98
    assert round(mod["ni_power"](0.10, -1.0), 2) == 0.30 and round(mod["ni_power"](0.10, 0.0), 2) == 0.81
    assert round(mod["sup_power"](0.05, 1.5), 2) == 0.86 and round(mod["sup_power"](0.05, 2.0), 2) == 0.98
    assert round(mod["sup_power"](0.10, 2.0), 2) == 0.82 and round(mod["sup_power"](0.10, 3.0), 2) == 0.99
    assert round(mod["ni_power"](0.02, -1.0), 2) == 0.89
    assert round(mod["half_width"](0.05), 2) == 0.97 and round(mod["half_width"](0.10), 2) == 1.37
    assert round(mod["mde"](0.05, 0.80), 1) == 1.4 and round(mod["mde"](0.05, 0.90), 1) == 1.6
    # |d|/100 > pi is infeasible: two arms cannot differ by more than their discordance
    assert mod["ni_power"](0.005, -1.0) is None and mod["ni_power"](0.005, -0.5) is not None and mod["sup_power"](0.001, 0.5) is None
    assert mod["feasible"](0.02, -2.0) and not mod["feasible"](0.02, -2.01)
    t = mod["tables"]()
    assert t["n"] == 2035 and t["margin"] == 2.0
    assert t["power"][5]["pi"] == 0.005 and t["power"][5]["powers"]["-1.0"] is None and t["power"][5]["powers"]["+0.0"] == 1.0
    committed = json.loads((STUDY / "power.json").read_text())
    assert committed == t
    assert mod["main"](["--out", str(tmp_path / "p.json")]) == 0
    assert json.loads((tmp_path / "p.json").read_text()) == t


def _strip_comments(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_study_workflow_texts_pinned():
    """ingest-bulk-h2h.cwl: six image-name lines are the only difference between the S and
    S-regex texts; frozen-inputs.json pins both sha256; comments aside, the study text is the
    v1.6.25 base plus exactly the stderr edits."""
    text = (STUDY / "ingest-bulk-h2h.cwl").read_text(encoding="utf-8")
    assert text.count("ragstack-tools-v1.6.24-b1.sif") == 6
    sregex = text.replace("ragstack-tools-v1.6.24-b1.sif", "ragstack-tools-v1.6.24-b2.sif")
    assert sum(1 for a, b in zip(text.splitlines(), sregex.splitlines(), strict=True) if a != b) == 6
    fz = json.loads((STUDY / "frozen-inputs.json").read_text())
    wt = fz["workflow_texts"]
    assert wt["S"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert wt["S-regex"]["sha256"] == hashlib.sha256(sregex.encode()).hexdigest()
    assert "stderr: $(inputs.shard.nameroot).ingest-stderr.txt" in text and "stderr_log: {type: stderr}" in text
    assert fz["sentinel"]["doc_ids_also_in_main_shards"] == 0 and fz["sentinel"]["n_chunks"] == 168
    assert fz["cost_order"] == "B" and fz["images"]["S"]["build"] == "1" and fz["images"]["S-regex"]["build"] == "2"
    assert fz["images"]["S"]["commit"] == fz["images"]["S-regex"]["commit"] and len(fz["shards"]) == 17
    report.assert_public(fz)
    report.assert_public(json.loads((STUDY / "config-diff.json").read_text()))
    try:
        base = subprocess.run(["git", "-C", str(REPO), "show", "v1.6.25:cwl/ingest-bulk.cwl"], capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("tag v1.6.25 not available in this checkout")
    assert hashlib.sha256(base.encode()).hexdigest() == wt["base"]["sha256"]
    diff = list(difflib.unified_diff(_strip_comments(base), _strip_comments(text), lineterm="", n=0))
    removed = [ln[1:] for ln in diff if ln.startswith("-") and not ln.startswith("---")]
    added = [ln[1:] for ln in diff if ln.startswith("+") and not ln.startswith("+++")]
    assert removed == ["    out: [receipt]"]
    assert "      stderr: $(inputs.shard.nameroot).ingest-stderr.txt" in added and "        stderr_log: {type: stderr}" in added
    assert "    out: [receipt, stderr_log]" in added and "  ingest_stderr:" in added and "    outputSource: ingest_shard/stderr_log" in added
    assert all(("stderr" in ln) or ("File[]" in ln) or ("one per shard" in ln) for ln in added), added
    assert len(added) == 8
