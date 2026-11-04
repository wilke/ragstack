"""Alias study judge-rule replay (prereg §6.4; §15 item 9): C = majority of the first 5 samples,
E = the first sample, agree -> final, else Argon, else unlabelled; scoring on the Opus-unanimous
pairs; the O10 subsets; and an output with no id. Synthetic label files; no /rag path."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import alias_study_common as asc  # noqa: E402
import alias_study_replay as rp  # noqa: E402

# ------------------------------------------------------------------ the rule

def test_compose_agree() -> None:
    assert rp.compose([1, 1, 1, 0, 0], [1, 0, 0], 0) == (1, "agree")
    assert rp.compose([0, 0, 0, 0, 0], [0], None) == (0, "agree")


def test_compose_reads_only_the_first_samples() -> None:
    # C's 6th sample and E's 2nd and 3rd are ignored
    assert rp.compose([1, 1, 1, 0, 0, 0, 0], [1, 0, 0], None) == (1, "agree")
    assert rp.compose([0, 0, 0, 1, 1], [1, 0, 0], 1) == (1, "tiebreak")


def test_compose_tie_and_invalid_escalate() -> None:
    # C ties among its valid samples (2-2 with one invalid): no majority -> Argon
    assert rp.compose([1, 1, 0, 0, None], [1], 0) == (0, "tiebreak")
    # C has no valid sample
    assert rp.compose([None] * 5, [0], 1) == (1, "tiebreak")
    # E's first sample invalid, even though its later samples are valid
    assert rp.compose([1, 1, 1, 1, 1], [None, 1, 1], 0) == (0, "tiebreak")
    # C and E disagree
    assert rp.compose([1, 1, 1, 1, 0], [0], True) == (1, "tiebreak")
    # escalated and Argon has no valid label -> unlabelled
    assert rp.compose([1, 1, 1, 1, 0], [0], None) == (None, "unlabelled")


def test_compose_no_label_on_either_side_is_never_agreement() -> None:
    # C has no majority and E's first sample is invalid: None == None must not count as agreement
    assert rp.compose([None] * 5, [None], 1) == (1, "tiebreak")
    assert rp.compose([None] * 5, [None], None) == (None, "unlabelled")
    assert rp.compose([1, 1, 0, 0, None], [None], 0) == (0, "tiebreak")  # C tie, E invalid
    # C no label, E valid; C valid, E no label
    assert rp.compose([None] * 5, [1], 0) == (0, "tiebreak")
    assert rp.compose([1, 1, 1, 0, 0], [None], 0) == (0, "tiebreak")
    assert rp.compose([1, 1, 1, 0, 0], [None], None) == (None, "unlabelled")


def test_compose_majority_over_valid_only() -> None:
    assert rp.compose([1, None, None, 1, 0], [1], None) == (1, "agree")


# ------------------------------------------------------------------ scoring

def test_score_counts_argon_and_unlabelled() -> None:
    final = {"a": (1, "agree"), "b": (0, "agree"), "c": (1, "tiebreak"), "d": (0, "tiebreak"), "e": (None, "unlabelled")}
    ref = {"a": 1, "b": 1, "c": 1, "d": 1, "e": 0}
    strata = {k: "gold" if k in "ab" else "coll_same" for k in final}
    s = rp.score(sorted(final), final, ref, strata)
    assert (s["pairs"], s["correct"], s["escalated"], s["argon_right"], s["argon_wrong"], s["unlabelled"]) == \
        (5, 2, 3, 1, 1, 1)
    assert s["accuracy"] == 0.4 and s["escalation_share"] == 0.6
    assert s["accepted_errors"] == 1 and s["accepted_false_no"] == 1 and s["accepted_false_yes"] == 0
    # the gate as validated: escalated pairs take the reference label
    assert s["gate_as_validated"] == {"correct": 4, "accuracy": 0.8}
    assert s["by_stratum"]["gold"] == {"pairs": 2, "correct": 1, "escalated": 0}


def test_compare_rounding() -> None:
    out = rp.compare({"composed.all.accuracy": 0.9897, "gate.all.escalation_share_pct": 2.46, "composed.all.pairs": 486},
                     {"composed.all.accuracy": 0.990, "gate.all.escalation_share_pct": 2.5, "composed.all.pairs": 487,
                      "gate.x.pairs": 1})
    assert out["composed.all.accuracy"]["status"] == "match"
    assert out["gate.all.escalation_share_pct"]["status"] == "match"
    assert out["composed.all.pairs"]["status"] == "MISMATCH"
    assert out["gate.x.pairs"]["status"] == "not_checked"


# ------------------------------------------------------------------ a synthetic validation set

def pair(i: int, q: str, c: str, stratum: str = "lex_other") -> dict[str, Any]:
    return {"pair_id": f"pairsyn{i:09d}", "record_id": f"alias-V-syn{i:06d}-a1", "q_paper": q, "c_paper": c,
            "chunk_id": f"chunk-syn-{i:08d}", "stratum": stratum, "record_kind": "G",
            "question": f"What does synthetic validation question number {i} ask about the measured quantity?"}


def world() -> dict[str, Any]:
    """8 pairs. p0-p3 accepted correctly; p4 accepted wrongly; p5 escalated, Argon right; p6
    escalated, Argon wrong; p7 escalated, no Argon label. p8 Opus split, p9 no reference."""
    pairs = [pair(0, "PMCQ1", "PMCQ1", "gold"), pair(1, "PMCSAT", "PMCQ2"), pair(2, "PMCQ3", "PMCSAT"),
             pair(3, "PMCQ3", "PMCQ4", "coll_diff"), pair(4, "PMCQ3", "PMCQ4"), pair(5, "PMCSAT", "PMCQ4"),
             pair(6, "PMCQ3", "PMCQ4"), pair(7, "PMCQ3", "PMCQ4"), pair(8, "PMCQ3", "PMCQ4"), pair(9, "PMCQ3", "PMCQ4")]
    ids = [p["pair_id"] for p in pairs]
    c = [[1] * 5, [0] * 5, [0] * 5, [0] * 5, [1] * 5, [1, 1, 0, 0, None], [1] * 5, [0] * 5, [0] * 5, [1] * 5]
    e = [[1, 0, 0], [0, 1, 1], [0, 0, 0], [0, 0, 0], [1, 1, 1], [1, 1, 1], [0, 0, 0], [None, 0, 0], [0, 0, 0], [1, 1, 1]]
    ref = [1, 0, 0, 0, 0, 1, 1, 0]
    argon = {5: True, 6: False, 7: None}
    c_rows = [{"k": f"qwen|{i}", "pair_id": i, "n": 5, "verdicts": v} for i, v in zip(ids, c, strict=True)]
    e_rows = [{"k": f"qwen|{i}", "pair_id": i, "n": 3, "verdicts": v} for i, v in zip(ids, e, strict=True)]
    a_rows = [{"k": f"gw|{i}|r1", "pair_id": i, "rep": 1, "verdict": argon.get(j, False)} for j, i in enumerate(ids)]
    a_rows.append({"k": f"gw|{ids[7]}|r2", "pair_id": ids[7], "rep": 2, "verdict": False})  # rep 2 never read
    opus = []
    for j, i in enumerate(ids[:8]):
        opus += [{"k": f"opus|{i}|r{r}", "pair_id": i, "rep": r, "verdict": bool(ref[j])} for r in (1, 2, 3)]
    opus += [{"k": f"opus|{ids[8]}|r{r}", "pair_id": ids[8], "rep": r, "verdict": v}
             for r, v in ((1, True), (2, False), (3, False))]
    opus += [{"k": f"opus|{ids[9]}|r{r}", "pair_id": ids[9], "rep": r, "verdict": None} for r in (1, 2, 3)]
    return {"pairs": pairs, "c": c_rows, "e": e_rows, "argon": a_rows, "opus": opus}


def test_replay_scores_on_unanimous_pairs() -> None:
    w = world()
    res = rp.replay({p["pair_id"]: p for p in w["pairs"]}, w["c"], w["e"], w["argon"], w["opus"], {"PMCSAT"})
    assert res["classes"] == {"pairs": 10, "unanimous": 8, "split": 1, "no_reference": 1}
    s = res["scores"]["all"]
    assert (s["pairs"], s["correct"], s["escalated"], s["argon_right"], s["unlabelled"]) == (8, 5, 3, 1, 1)
    assert s["gate_as_validated"]["correct"] == 7
    assert res["no_reference"] == {"pairs": 1, "escalated": 0, "final_yes": 1, "final_no": 0, "unlabelled": 0}
    assert res["all_pairs"]["unlabelled"] == 1 and res["all_pairs"]["tiebreak"] == 2
    assert res["scores"]["excl_coll_diff"]["pairs"] == 7
    # O10: pair 1 and 5 have the satisfied paper as question paper; pair 2 as passage paper
    assert res["scores"]["excl_q_satisfied"]["pairs"] == 6
    assert res["scores"]["excl_touching_satisfied"]["pairs"] == 5
    assert res["o10"]["question_papers_in_satisfied"] == 1
    assert res["o10"]["pairs_with_question_paper_in_satisfied"] == 2 and res["o10"]["pairs_touching_satisfied"] == 3


def test_replay_without_records_has_no_o10() -> None:
    w = world()
    res = rp.replay({p["pair_id"]: p for p in w["pairs"]}, w["c"], w["e"], w["argon"], w["opus"], None)
    assert set(res["scores"]) == {"all", "excl_coll_diff"} and res["o10"] is None


def test_replay_refuses_short_or_missing_rows() -> None:
    w = world()
    pairs = {p["pair_id"]: p for p in w["pairs"]}
    with pytest.raises(SystemExit, match="without a C row"):
        rp.replay(pairs, w["c"][1:], w["e"], w["argon"], w["opus"])
    short = [{**w["c"][0], "verdicts": [1, 1]}] + w["c"][1:]
    with pytest.raises(SystemExit, match="fewer stored samples"):
        rp.replay(pairs, short, w["e"], w["argon"], w["opus"])


def test_gate_reproduction() -> None:
    w = world()
    res = rp.replay({p["pair_id"]: p for p in w["pairs"]}, w["c"], w["e"], w["argon"], w["opus"])
    stored = {"primary": {"all": {"pairs": 8, "correct": 7, "escalated": 3},
                          "excl_coll_diff": {"pairs": 7, "correct": 5, "escalated": 3}}}
    out = rp.gate_reproduction(res, stored)
    assert out["all"]["status"] == "match" and out["excl_coll_diff"]["status"] == "MISMATCH"


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_main_writes_counts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = world()
    vd = tmp_path / "validation"
    vd.mkdir()
    _write(vd / "pairs.jsonl", w["pairs"])
    _write(vd / "labels-qwen-C.jsonl", w["c"])
    _write(vd / "labels-qwen-E.jsonl", w["e"])
    _write(vd / "labels-opus.jsonl", w["opus"])
    argon = _write(tmp_path / "labels-gw-gemini4argon.jsonl", w["argon"])
    monkeypatch.setattr(rp, "PAIRS_SHA256", asc.sha256_file(vd / "pairs.jsonl"))
    monkeypatch.setattr(rp, "provenance", lambda: {"commit": "c" * 40, "dirty": True, "untracked": 2, "host": "h"})
    out = tmp_path / "pub" / "replay.json"
    assert rp.main(["--validation-dir", str(vd), "--argon-labels", str(argon), "--out", str(out)]) == 0
    text = out.read_text()
    data = json.loads(text)
    assert data["citable"] is False and "host" not in data["provenance"]
    assert data["scores"]["all"]["correct"] == 5
    for p in w["pairs"]:
        for k in ("pair_id", "record_id", "q_paper", "c_paper", "chunk_id"):
            assert p[k] not in text
    assert set(data["inputs"]) == {"pairs", "c_labels", "e_labels", "opus_labels", "argon_labels"}
    assert data["checks"]["composed.all.pairs"]["status"] == "MISMATCH"  # synthetic, not the prereg's 487


def test_main_refuses_other_pairs(tmp_path: Path) -> None:
    w = world()
    vd = tmp_path / "validation"
    vd.mkdir()
    _write(vd / "pairs.jsonl", w["pairs"])
    with pytest.raises(SystemExit, match="frozen pair set"):
        rp.main(["--validation-dir", str(vd), "--argon-labels", str(vd / "x.jsonl"), "--out", str(tmp_path / "o.json")])
