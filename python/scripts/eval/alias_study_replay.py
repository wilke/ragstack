#!/usr/bin/env python
"""Alias study (OA-1000): the committed replay of the composed judge rule on the validation labels
(DRAFT prereg §6.4 last row; §15 item 9).

    alias_study_replay.py --validation-dir /rag/tmp/alias-study-2026-11/calib/validation \\
                          --argon-labels /rag/tmp/alias-study-2026-11/calib/tiebreaker/labels-gw-gemini4argon.jsonl \\
                          [--records accepted-alias-O.jsonl] --out judge-rule-replay.json

**The rule** (prereg §6.3, TIEBREAKER-PLAN.md, JUDGE-VALIDATION-PLAN.md §1), read from the stored
labels exactly as the validation read them: judge C's label is the majority of its **first 5**
stored samples, judge E's label its **first** stored sample (both via
``glossbench_alias_judge.majority``, so a tie or no valid sample is no label). Both labelled and
equal: that label is final. Otherwise the pair is **escalated** and takes the Argon tiebreaker's
rep-1 label (``alias_study_judge.final_label``'s order); no valid Argon label: **unlabelled**.

**Scoring** as ``analyse-gate`` scored the validation: the reference is the Opus reference of the
validation set (``glossbench_alias_judge.opus_reference``, 3 repetitions), the scored pairs are
the Opus-**unanimous** ones, the pair set is pinned by ``pairs.jsonl``'s sha256 (the config's
``pairs_sha256``). The validation gave an escalated pair the Opus label (correct by construction);
the replay gives it Argon's label, so an Argon error costs a point, and an unlabelled pair counts
as an error. The same scoring with the Opus label in place of Argon's (the gate as validated) is
reported beside it, and must reproduce ``analysis-gate.json`` when it is given.

**O10 sensitivity** (prereg §4.3 item 6): with ``--records``, the same figures without the pairs
whose question paper is one of the study's satisfied papers (the balanced set, by
``alias_study_common.split_records``), and without every pair that touches one (question or
passage paper), each also without ``coll_diff``.

The output holds counts, rates and shas only (no pair, record, paper or chunk id;
:func:`alias_study_common.public_problems`; ``host`` / ``warnings`` dropped from provenance).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import alias_study_common as asc  # noqa: E402

SCHEMA = "alias-study-judge-rule-replay/1"
C_N, E_N, REPS = 5, 1, 3
#: the validation's frozen pair set (cfg-gate.json ``pairs_sha256``)
PAIRS_SHA256 = "20ed67ba163b25c363a9673a11ddb8a835562c5b62ef6bc5a44bec34cf6feb15"

#: the prereg's scratch figures (DRAFT v3 §6.4 last row; §4.3 item 6)
PREREG: dict[str, Any] = {
    "composed.all.pairs": 487, "composed.all.correct": 482, "composed.all.accuracy": 0.990,
    "composed.all.escalated": 12, "composed.all.argon_right": 11, "composed.all.unlabelled": 0,
    # §6.4: the gate as validated (escalation -> Opus)
    "gate.all.accuracy": 0.992, "gate.all.escalation_share_pct": 2.5,
    # §4.3 item 6: the frozen validation rule (C + E, escalations to Opus) without the overlap pairs
    "gate.excl_q_satisfied.pairs": 388, "gate.excl_q_satisfied.accuracy": 0.992,
    "gate.excl_q_satisfied.escalation_share_pct": 2.8,
    "gate.excl_touching_satisfied.pairs": 287, "gate.excl_touching_satisfied.accuracy": 0.990,
    "gate.excl_touching_satisfied.escalation_share_pct": 2.8,
    "gate.excl_q_satisfied_excl_coll_diff.accuracy": 0.991,
    "gate.excl_q_satisfied_excl_coll_diff.escalation_share_pct": 3.5,
    "gate.excl_touching_satisfied_excl_coll_diff.accuracy": 0.988,
    "gate.excl_touching_satisfied_excl_coll_diff.escalation_share_pct": 3.3,
}


# ------------------------------------------------------------------ the rule

def majority(verdicts: Sequence[Any]) -> int | None:
    """``glossbench_alias_judge.majority``: the majority of the valid (non-None) verdicts, None
    on a tie or with none valid."""
    import glossbench_alias_judge as jd
    return jd.majority(list(verdicts))


def compose(c_verdicts: Sequence[Any], e_verdicts: Sequence[Any], argon_verdict: Any,
            c_n: int = C_N, e_n: int = E_N) -> tuple[int | None, str]:
    """``(label, source)``: ``agree`` (C's first ``c_n`` majority equals E's first ``e_n``
    majority), ``tiebreak`` (escalated, Argon's valid label), ``unlabelled`` (escalated, no
    valid Argon label)."""
    c = majority(list(c_verdicts)[:c_n])
    e = majority(list(e_verdicts)[:e_n])
    if c is not None and c == e:
        return c, "agree"
    if argon_verdict is not None:
        return int(bool(argon_verdict)), "tiebreak"
    return None, "unlabelled"


def score(ids: Sequence[str], final: Mapping[str, tuple[int | None, str]], ref: Mapping[str, int],
          strata: Mapping[str, str]) -> dict[str, Any]:
    """The composed rule on ``ids`` against ``ref``: an unlabelled pair counts as an error. Also
    the gate as validated (escalated pairs take ``ref``)."""
    n = len(ids)
    esc = [p for p in ids if final[p][1] != "agree"]
    correct = sum(final[p][0] == ref[p] for p in ids)
    acc_err = sum(final[p][1] == "agree" and final[p][0] != ref[p] for p in ids)
    gate_correct = n - acc_err
    argon_right = sum(final[p][1] == "tiebreak" and final[p][0] == ref[p] for p in esc)

    def r(x: int) -> float | None:
        return round(x / n, 4) if n else None

    by: dict[str, Any] = {}
    for s in sorted({strata[p] for p in ids}):
        sub = [p for p in ids if strata[p] == s]
        by[s] = {"pairs": len(sub), "correct": sum(final[p][0] == ref[p] for p in sub),
                 "escalated": sum(final[p][1] != "agree" for p in sub)}
    return {"pairs": n, "correct": correct, "accuracy": r(correct), "escalated": len(esc),
            "escalation_share": r(len(esc)), "argon_labelled": sum(final[p][1] == "tiebreak" for p in esc),
            "argon_right": argon_right, "argon_wrong": sum(final[p][1] == "tiebreak" and final[p][0] != ref[p] for p in esc),
            "unlabelled": sum(final[p][1] == "unlabelled" for p in ids),
            "accepted_errors": acc_err,
            "accepted_false_yes": sum(final[p][1] == "agree" and final[p][0] == 1 and ref[p] == 0 for p in ids),
            "accepted_false_no": sum(final[p][1] == "agree" and final[p][0] == 0 and ref[p] == 1 for p in ids),
            "gate_as_validated": {"correct": gate_correct, "accuracy": r(gate_correct)},
            "by_stratum": by}


def subsets(una: Sequence[str], pairs: Mapping[str, Mapping[str, Any]], satisfied: set[str] | None
            ) -> dict[str, list[str]]:
    """``all`` and ``excl_coll_diff``; with ``satisfied``: without the pairs whose question paper is
    a satisfied paper (``excl_q_satisfied``), without every pair touching one as question or
    passage paper (``excl_touching_satisfied``), each also without ``coll_diff``."""
    def nocd(p: str) -> bool:
        return str(pairs[p]["stratum"]) != "coll_diff"

    keep: dict[str, Callable[[str], bool]] = {"all": lambda p: True}
    if satisfied is not None:
        keep["excl_q_satisfied"] = lambda p: str(pairs[p]["q_paper"]) not in satisfied
        keep["excl_touching_satisfied"] = lambda p: (str(pairs[p]["q_paper"]) not in satisfied
                                                     and str(pairs[p]["c_paper"]) not in satisfied)
    out: dict[str, list[str]] = {}
    for name, f in keep.items():
        ids = [p for p in una if f(p)]
        out[name] = ids
        out[f"{name}_excl_coll_diff" if name != "all" else "excl_coll_diff"] = [p for p in ids if nocd(p)]
    return out


def compare(found: Mapping[str, Any], prereg: Mapping[str, Any] = PREREG) -> dict[str, Any]:
    """Each prereg figure beside the recomputed one. A prereg rate is printed to 3 decimals (a
    share in percent to 1), so the recomputed value is rounded the same way before comparing."""
    out: dict[str, Any] = {}
    for k, want in prereg.items():
        got = found.get(k)
        if got is None:
            out[k] = {"prereg": want, "recomputed": None, "status": "not_checked"}
            continue
        if k.endswith("_pct"):
            cmp = round(float(got), 1)
        elif isinstance(want, float):
            cmp = round(float(got), 3)
        else:
            cmp = got
        out[k] = {"prereg": want, "recomputed": got, "status": "match" if cmp == want else "MISMATCH"}
    return out


def flatten(scores: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, s in scores.items():
        found[f"composed.{name}.pairs"] = s["pairs"]
        found[f"composed.{name}.correct"] = s["correct"]
        found[f"composed.{name}.accuracy"] = s["accuracy"]
        found[f"composed.{name}.escalated"] = s["escalated"]
        found[f"composed.{name}.argon_right"] = s["argon_right"]
        found[f"composed.{name}.unlabelled"] = s["unlabelled"]
        found[f"gate.{name}.pairs"] = s["pairs"]
        found[f"gate.{name}.accuracy"] = s["gate_as_validated"]["accuracy"]
        found[f"gate.{name}.escalation_share_pct"] = (round(100 * s["escalation_share"], 2)
                                                      if s["escalation_share"] is not None else None)
    return found


# ------------------------------------------------------------------ I/O

def load_rows(path: Path) -> list[dict[str, Any]]:
    """A harness label store: the last row of a call key ``k`` wins (``deployed_docs_part4.Store``)."""
    import deployed_docs_part4 as p4
    return list(p4.Store(path).rows.values())


def replay(pairs: Mapping[str, Mapping[str, Any]], c_rows: Sequence[Mapping[str, Any]],
           e_rows: Sequence[Mapping[str, Any]], argon_rows: Sequence[Mapping[str, Any]],
           opus_rows: Sequence[Mapping[str, Any]], satisfied: set[str] | None = None) -> dict[str, Any]:
    import glossbench_alias_judge as jd

    c = {str(r["pair_id"]): r for r in c_rows}
    e = {str(r["pair_id"]): r for r in e_rows}
    a = {str(r["pair_id"]): r for r in argon_rows if int(r.get("rep", 1)) == 1}
    missing = {k: len(set(pairs) - set(d)) for k, d in (("c", c), ("e", e), ("argon", a))}
    if missing["c"] or missing["e"]:
        raise SystemExit(f"replay: pairs without a C row {missing['c']}, without an E row {missing['e']}")
    short = sum(len(c[p].get("verdicts") or []) < C_N or len(e[p].get("verdicts") or []) < E_N for p in pairs)
    if short:
        raise SystemExit(f"replay: {short} pairs have fewer stored samples than the rule reads")
    final = {p: compose(c[p]["verdicts"], e[p]["verdicts"], (a.get(p) or {}).get("verdict")) for p in pairs}
    ref_all = jd.opus_reference(opus_rows, REPS)
    una = sorted(p for p, r in ref_all.items() if r["unanimous"] and p in pairs)
    noref = sorted(set(pairs) - set(ref_all))
    split = sorted(p for p, r in ref_all.items() if not r["unanimous"] and p in pairs)
    ref = {p: int(ref_all[p]["ref"]) for p in una}
    strata = {p: str(pairs[p]["stratum"]) for p in pairs}
    subs = subsets(una, pairs, satisfied)
    scores = {name: score(ids, final, ref, strata) for name, ids in subs.items()}
    src = Counter(s for _, s in final.values())
    res: dict[str, Any] = {
        "rule": {"c": f"majority of the first {C_N} stored samples (a tie or no valid sample: no label)",
                 "e": f"the first {E_N} stored sample", "final": "C and E labelled and equal; otherwise the "
                 "Argon rep-1 label; no valid Argon label: unlabelled",
                 "reference": f"Opus ({REPS} repetitions), unanimous pairs only"},
        "classes": {"pairs": len(pairs), "unanimous": len(una), "split": len(split), "no_reference": len(noref)},
        "all_pairs": {"pairs": len(pairs), "agree": src.get("agree", 0), "tiebreak": src.get("tiebreak", 0),
                      "unlabelled": src.get("unlabelled", 0), "argon_rows_rep1": len(a),
                      "pairs_without_argon_row": missing["argon"]},
        "no_reference": {"pairs": len(noref), "escalated": sum(final[p][1] != "agree" for p in noref),
                         "final_yes": sum(final[p][0] == 1 for p in noref), "final_no": sum(final[p][0] == 0 for p in noref),
                         "unlabelled": sum(final[p][1] == "unlabelled" for p in noref)},
        "split": {"pairs": len(split), "escalated": sum(final[p][1] != "agree" for p in split)},
        "scores": scores,
        "o10": None if satisfied is None else {
            "satisfied_papers": len(satisfied),
            "question_papers_in_satisfied": len({str(pairs[p]["q_paper"]) for p in pairs} & satisfied),
            "pairs_with_question_paper_in_satisfied": sum(str(pairs[p]["q_paper"]) in satisfied for p in pairs),
            "pairs_touching_satisfied": sum(str(pairs[p]["q_paper"]) in satisfied or str(pairs[p]["c_paper"]) in satisfied
                                            for p in pairs),
            "unanimous_pairs_removed": {k: len(una) - len(v) for k, v in subs.items()
                                        if k in ("excl_q_satisfied", "excl_touching_satisfied")}},
    }
    res["checks"] = compare(flatten(scores))
    res["checks_summary"] = dict(sorted(Counter(v["status"] for v in res["checks"].values()).items()))
    return res


def gate_reproduction(res: Mapping[str, Any], analysis_gate: Mapping[str, Any]) -> dict[str, Any]:
    """The gate-as-validated figures against the stored ``analysis-gate.json`` (``primary``)."""
    out: dict[str, Any] = {}
    for name in ("all", "excl_coll_diff"):
        stored = (analysis_gate.get("primary") or {}).get(name) or {}
        mine = res["scores"].get(name) or {}
        got = {"pairs": mine.get("pairs"), "correct": (mine.get("gate_as_validated") or {}).get("correct"),
               "escalated": mine.get("escalated")}
        want = {"pairs": stored.get("pairs"), "correct": stored.get("correct"), "escalated": stored.get("escalated")}
        out[name] = {"stored": want, "replayed": got, "status": "match" if got == want else "MISMATCH"}
    return out


def public_copy(obj: Any, _in_prov: bool = False) -> Any:
    if isinstance(obj, Mapping):
        return {k: public_copy(v, k == "provenance") for k, v in obj.items()
                if not (_in_prov and k in ("host", "warnings"))}
    if isinstance(obj, list):
        return [public_copy(v) for v in obj]
    return obj


def provenance() -> dict[str, Any]:
    try:
        from ragstack.provenance import experiment_provenance
        return experiment_provenance()
    except Exception:  # noqa: BLE001
        return {}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--validation-dir", required=True, type=Path,
                    help="pairs.jsonl, labels-qwen-C.jsonl, labels-qwen-E.jsonl, labels-opus.jsonl (read only)")
    ap.add_argument("--argon-labels", required=True, type=Path, help="the tiebreaker's labels-gw-gemini4argon.jsonl")
    ap.add_argument("--records", type=Path, default=None, help="the accepted-record file (O10 sensitivity)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    vd = args.validation_dir
    files = {"pairs": vd / "pairs.jsonl", "c_labels": vd / "labels-qwen-C.jsonl", "e_labels": vd / "labels-qwen-E.jsonl",
             "opus_labels": vd / "labels-opus.jsonl", "argon_labels": args.argon_labels}
    if (vd / "analysis-gate.json").is_file():
        files["analysis_gate"] = vd / "analysis-gate.json"
    psha = asc.sha256_file(files["pairs"])
    if psha != PAIRS_SHA256:
        raise SystemExit(f"replay: pairs.jsonl sha256 {psha[:12]}… is not the validation's frozen pair set")
    pairs = {str(p["pair_id"]): p for p in asc.read_jsonl(files["pairs"])}
    satisfied = None
    records: list[dict[str, Any]] = []
    if args.records is not None:
        records = asc.load_records(args.records)
        asc.verify_record_set(args.records, records)
        bal, _ = asc.split_records(records)
        satisfied = {str(r["paper"]) for r in bal}
        files["records"] = args.records
    res = replay(pairs, load_rows(files["c_labels"]), load_rows(files["e_labels"]), load_rows(files["argon_labels"]),
                 load_rows(files["opus_labels"]), satisfied)
    if "analysis_gate" in files:
        res["gate_reproduction"] = gate_reproduction(res, json.loads(files["analysis_gate"].read_text()))
    inputs = {k: {"file": p.name, "sha256": asc.sha256_file(p)} for k, p in sorted(files.items())}
    prov = provenance()
    out = public_copy({"schema": SCHEMA, **res, "inputs": inputs, "provenance": prov,
                       "citable": bool(prov.get("dirty") is False and prov.get("untracked") == 0)})
    forbidden = (set(pairs) |{str(p[f]) for p in pairs.values() for f in ("record_id", "q_paper", "c_paper", "chunk_id")}
                 | {str(r["record_id"]) for r in records} | {str(r["paper"]) for r in records})
    texts = [str(p["question"]) for p in pairs.values()]
    bad = asc.public_problems(out, forbidden, texts)
    if bad:
        print(f"REFUSED: the output would carry {len(bad)} private string(s) ({sorted(set(bad))})", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"checks": res["checks_summary"],
                      "not_matching": {k: v for k, v in res["checks"].items() if v["status"] != "match"},
                      "gate_reproduction": {k: v["status"] for k, v in (res.get("gate_reproduction") or {}).items()},
                      "all": {k: res["scores"]["all"][k] for k in ("pairs", "correct", "accuracy", "escalated",
                                                                 "argon_right", "unlabelled")},
                      "citable": out["citable"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
