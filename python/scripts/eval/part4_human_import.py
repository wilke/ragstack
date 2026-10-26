"""Import the human grades for part 4 of the deployed-docs chunk-size trial and apply the
pre-registered validation rules (prereg §5, "Human grades").

Inputs, all PRIVATE and kept outside the repository:

- the items file written by ``part4 --export-human`` (only to check that the grades belong to it);
- its sibling key file (``<items>.key.json``): per item, the part (A or B), the model grades,
  and for part B the arm / condition / reader;
- one or two grades files exported by the blinded grading page.

Rules (fixed in the pre-registration, not here; this file only applies them):

- **Part A** (Sol and Opus 4.8 disagree on correct vs not): report P(the human sides with Sol)
  with a Clopper–Pearson 95% CI.
- **Part B** (discordant pairs of H4, T512o0 vs L): report the human's gained / lost against
  Sol's, and κ(correct vs not), human vs Sol, with a 95% item-bootstrap CI.
- Sol is *validated* when κ_B ≥ 0.6 (point estimate) **and** P_A ≥ 0.5.
- If the human agrees with **neither** model, the confirmatory results are reported as
  *grader-unvalidated*. The prereg leaves the item sets for this check open. They were fixed
  before any human grade existed (DEVIATIONS.md, note of 2026-10-25):
  - against Sol: κ_B, the pre-registered κ;
  - against Opus 4.8: κ(correct vs not) over the part-B items Opus also graded. Their labels are
    read from the run's results file (``--results``), because the key carries Opus labels only for
    part A. Part A cannot be used: there, Sol and Opus disagree by construction, so human–Opus
    agreement is exactly 1 − P_A.
  - Both κ must be below 0.6, and the Opus κ needs at least ``MIN_OPUS_B`` items. Otherwise the
    check is not evaluable, and a failed Sol check is reported as *not validated*.
  - κ human vs Opus on part A is reported only as the complement of P_A.
- κ CIs bootstrap items, not pairs; the prereg does not fix the unit.
- The repository guard covers the checkout this script runs from.
- A second rater on part A gives a human–human κ.

Usage::

    PYTHONPATH=python python python/scripts/eval/part4_human_import.py \\
        --items /private/human-items-part4.json --results /private/results-part4.json \\
        --grades grades-1.json [--grades2 grades-2.json] --out /private/human-results-part4.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chunktrial_stats as cs  # noqa: E402

LABELS = ("correct", "partial", "incorrect", "abstained")
KAPPA_MIN = 0.6
SIDES_MIN = 0.5
MIN_OPUS_B = 10


def export_id(items_path: Path) -> str:
    """The id the grading page embeds: ``part4-`` + the first 12 hex of the items file's sha256."""
    return "part4-" + hashlib.sha256(items_path.read_bytes()).hexdigest()[:12]


def load_grades(path: Path, expected_id: str) -> dict[str, str]:
    d = json.loads(path.read_text())
    if d.get("export_id") != expected_id:
        raise SystemExit(f"{path}: grades belong to {d.get('export_id')!r}, not {expected_id!r}")
    out: dict[str, str] = {}
    for g in d["grades"]:
        if g["item"] in out:
            raise SystemExit(f"{path}: item {g['item']} appears twice")
        if g.get("grade") is None:
            continue
        if g["grade"] not in LABELS:
            raise SystemExit(f"{path}: unknown grade {g['grade']!r} for item {g['item']}")
        out[g["item"]] = g["grade"]
    return out


def is_correct(label: str | None) -> bool:
    return label == "correct"


def opus_labels_for_b(key: dict[str, dict[str, Any]], per_item: dict[str, Any], tag: str = "opus48") -> dict[str, str]:
    """Opus labels for part-B items, from the run's per_item block (None where Opus did not grade)."""
    out = {}
    for i, v in key.items():
        if v["part"] != "B":
            continue
        lab = per_item[v["record_id"]][v["arm"]][v["condition"]]["readers"][v["reader"]].get(tag)
        if lab is not None:
            out[i] = lab
    return out


def analyse(key: dict[str, dict[str, Any]], human: dict[str, str],
            human2: dict[str, str] | None = None, seed: int = 261025,
            opus_b: dict[str, str] | None = None) -> dict[str, Any]:
    """Apply the §5 rules. ``key`` maps item id → key entry; ``human`` maps item id → grade;
    ``opus_b`` maps part-B item id → Opus label (see the module docstring)."""
    unknown = sorted(set(human) - set(key))
    if unknown:
        raise SystemExit(f"grades for items not in the key: {unknown[:5]}")
    a_items = [i for i, v in key.items() if v["part"] == "A"]
    b_items = [i for i, v in key.items() if v["part"] == "B"]
    missing = sorted(i for i in key if i not in human)

    # Part A: does the human side with Sol on correct vs not?
    a_graded = [i for i in a_items if i in human]
    sides = sum(is_correct(human[i]) == is_correct(key[i]["sol"]) for i in a_graded)
    part_a = cs.proportion_ci(sides, len(a_graded)) if a_graded else {"k": 0, "n": 0, "p": None, "ci": None}

    # Part B: pairs (record, reader, condition) with the two arms of H4.
    pairs: dict[tuple[str, str, str], dict[str, str]] = {}
    for i in b_items:
        v = key[i]
        pairs.setdefault((v["record_id"], v["reader"], v["condition"]), {})[v["arm"]] = i
    full = [p for p in pairs.values() if len(p) == 2 and all(x in human for x in p.values())]
    arms = sorted({a for p in pairs.values() for a in p})
    if len(arms) != 2:
        raise SystemExit(f"part B needs exactly two arms, found {arms}")
    first, second = ("T512o0", "L") if set(arms) == {"T512o0", "L"} else (arms[0], arms[1])

    def gl(get: Any) -> dict[str, int]:
        g = sum(get(p[first]) and not get(p[second]) for p in full)
        lost = sum(get(p[second]) and not get(p[first]) for p in full)
        return {"gained": g, "lost": lost, "net": g - lost}

    b_human = gl(lambda i: is_correct(human[i]))
    b_sol = gl(lambda i: is_correct(key[i]["sol"]))
    b_ids = [x for p in full for x in p.values()]
    kb = (cs.cohen_kappa_ci([is_correct(human[i]) for i in b_ids], [is_correct(key[i]["sol"]) for i in b_ids],
                            categories=[True, False], seed=seed) if b_ids else None)

    # Neither-model check: Sol via κ_B; Opus over the part-B items it also graded.
    ob = [i for i in b_ids if opus_b and i in opus_b]
    k_opus_b = (cs.cohen_kappa_ci([is_correct(human[i]) for i in ob], [is_correct(opus_b[i]) for i in ob],
                                  categories=[True, False], seed=seed) if ob else None)
    k_opus_a = (cs.cohen_kappa_ci([is_correct(human[i]) for i in a_graded],
                                  [is_correct(key[i]["opus48"]) for i in a_graded],
                                  categories=[True, False], seed=seed) if a_graded else None)

    kb_point = kb["kappa"] if kb else None
    validated = (kb_point is not None and part_a["p"] is not None
                 and kb_point >= KAPPA_MIN and part_a["p"] >= SIDES_MIN)
    neither_evaluable = kb_point is not None and k_opus_b is not None and len(ob) >= MIN_OPUS_B
    neither = neither_evaluable and kb_point < KAPPA_MIN and k_opus_b["kappa"] < KAPPA_MIN
    verdict = "validated" if validated else ("grader-unvalidated" if neither else "not validated")

    out: dict[str, Any] = {
        "n_items": len(key), "n_graded": len(key) - len(missing), "ungraded_items": missing,
        "part_a": {"n": len(a_graded), "sides_with_sol": part_a},
        "part_b": {"pairs_complete": len(full), "contrast": f"{first} - {second}",
                   "human": b_human, "sol": b_sol, "kappa_correct_vs_not_human_vs_sol": kb},
        "neither_check": {"evaluable": neither_evaluable, "kappa_human_vs_sol_B": kb_point,
                          "human_vs_opus48_B": k_opus_b, "n_opus_B": len(ob), "min_opus_B": MIN_OPUS_B},
        "kappa_human_vs_opus48_A_complement_of_P_A": k_opus_a,
        "rule": {"kappa_min": KAPPA_MIN, "sides_min": SIDES_MIN}, "verdict": verdict,
    }
    if human2 is not None:
        both = [i for i in a_items if i in human and i in human2]
        out["human_human"] = {
            "n": len(both),
            "kappa_4class": cs.cohen_kappa_ci([human[i] for i in both], [human2[i] for i in both],
                                              categories=list(LABELS), seed=seed) if both else None,
            "kappa_correct_vs_not": cs.cohen_kappa_ci([is_correct(human[i]) for i in both],
                                                      [is_correct(human2[i]) for i in both],
                                                      categories=[True, False], seed=seed) if both else None,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--grades", required=True, type=Path)
    ap.add_argument("--grades2", type=Path, default=None, help="a second rater's file (part A κ)")
    ap.add_argument("--results", type=Path, default=None,
                    help="the run's results JSON (Opus labels for part B; without it the neither-check is not evaluable)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    for p in (args.out,):
        rp = p.resolve()
        if rp == repo or repo in rp.parents:
            raise SystemExit("refusing to write private human-grade results inside the repository")
    eid = export_id(args.items)
    key = json.loads(args.items.with_name(args.items.stem + ".key.json").read_text())["key"]
    human = load_grades(args.grades, eid)
    human2 = load_grades(args.grades2, eid) if args.grades2 else None
    opus_b = opus_labels_for_b(key, json.loads(args.results.read_text())["per_item"]) if args.results else None
    res = analyse(key, human, human2, opus_b=opus_b)
    res["export_id"] = eid
    args.out.write_text(json.dumps(res, indent=1, default=float, allow_nan=False) + "\n")
    print(json.dumps({"verdict": res["verdict"], "graded": res["n_graded"], "of": res["n_items"],
                      "part_a_sides_with_sol": res["part_a"]["sides_with_sol"].get("p"),
                      "part_b_kappa": (res["part_b"]["kappa_correct_vs_not_human_vs_sol"] or {}).get("kappa")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
