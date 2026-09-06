"""Stage 0b step 5a -- the three MACHINE label gates of P.5 / SS6.4.

These are **stop gates for the study**, and they are the machine half of SS8.5.7 row 8.
The human half (kappa(human-human), kappa(Scout-human), positive-class agreement,
wrong-location / non-minimal / missed-evidence rates) requires two human readers and is
reported ``PENDING-HUMAN``; nothing here substitutes for it.

* **hallucinated-span rate** -- quote-verification failures as a fraction of ALL spans the
  labeler attempted (accepted + failed), not as a fraction of fully-dropped pairs: a pair
  that lost one span of three must not flatter the gate. Wilson upper bound reported.
  Gate: <= 0.05.
* **self-consistency** -- 10% of pairs are re-labeled with the units presented in a
  different order (seeded). Consistent iff the character-span union of the pair's evidence
  sets has Jaccard >= 0.5 across the two presentations; two "no localizable evidence"
  verdicts count as consistent (PINNED reading of SS6.4 rule 4's "primary-set char-span
  Jaccard"). Gate: >= 0.90 consistent.
* **minimality shrinkage** -- 10% of pairs are re-prompted with "remove any span not
  strictly needed"; the fraction whose span count fell is reported (descriptive, no gate).
"""
from __future__ import annotations

import json

import s0_common as C
import s0_math as M
from s0_score import jaccard


def union_of(sets):
    return [[sp["start"], sp["end"]] for s in sets for sp in s["spans"]]


def main() -> None:
    labels = [json.loads(x) for x in (C.WORK / "labels.jsonl").read_text().splitlines() if x]
    meta = json.loads((C.WORK / "label_meta.json").read_text())

    V: dict[str, int] = {}
    for r in labels:
        for k, v in (r.get("vstats") or {}).items():
            V[k] = V.get(k, 0) + v
    attempted = V.get("spans_seen", 0)
    failed = V.get("hallucinated", 0)              # quote NOT in the document (SS6.4 r2)
    unres = V.get("unresolvable", 0)
    mis = V.get("misindexed", 0)
    okix = V.get("index_ok", 0)
    hl = failed / attempted if attempted else 0.0
    hl_u = M.wilson(failed, attempted)[1] if attempted else 1.0

    dup = [r for r in labels if "dup_sets" in r]
    cons = []
    for r in dup:
        a, b = union_of(r["sets"]), union_of(r["dup_sets"])
        cons.append(True if (not a and not b) else jaccard(a, b) >= C.JACCARD_MERGE)
    sc = sum(cons) / len(cons) if cons else float("nan")

    aud = [r for r in labels if "audit_sets" in r]
    shrank = sum(1 for r in aud
                 if sum(len(s["spans"]) for s in r["audit_sets"])
                 < sum(len(s["spans"]) for s in r["sets"]))
    shrink = shrank / len(aud) if aud else float("nan")

    drops = sum(1 for r in labels if r["dropped"])
    none_v = sum(1 for r in labels if not r["dropped"] and not r["sets"])
    by_grade: dict[str, dict[str, int]] = {}
    for r in labels:
        g = str(r["grade"])
        d = by_grade.setdefault(g, {"pairs": 0, "none": 0})
        d["pairs"] += 1
        if not r["dropped"] and not r["sets"]:
            d["none"] += 1

    out = {
        "hallucinated_span_rate": {
            "failed_spans": failed, "attempted_spans": attempted,
            "rate": round(hl, 5), "wilson95_upper": round(hl_u, 5),
            "gate": "<= 0.05", "PASS": hl <= 0.05,
            "definition": "spans whose quoted words are NOWHERE in the document / all "
                          "spans attempted (SS6.4 rule 2 read literally: the checker "
                          "verifies substrings against the DOCUMENT). A quote that "
                          "verifies but sits at a different index is SS6.6.1's "
                          "`wrong-location`, a label error, and is counted separately."},
        "index_agreement": {
            "spans_attempted": attempted, "index_ok": okix, "misindexed_relocated": mis,
            "rate": round(okix / attempted, 5) if attempted else None,
            "gate": "none — reported as a finding about the labeler",
            "definition": "fraction of verified spans whose claimed (unit, first, last) "
                          "equalled the location its own quote resolves to. Misindexed "
                          "spans were RELOCATED to the quote's position (D1-snapped to "
                          "whole sentences in one unit), not discarded."},
        "unresolvable_spans": {
            "n": unres, "rate": round(unres / attempted, 5) if attempted else None,
            "definition": "quote is in the document but its interval crosses a unit "
                          "boundary or no unit contains it; span dropped"},
        "ambiguous_quotes": V.get("ambiguous_quote", 0),
        "spans_without_last_words": V.get("no_last_words", 0),
        "self_consistency": {
            "n_duplicated": len(cons), "consistent": sum(cons),
            "rate": None if cons == [] else round(sc, 4),
            "gate": ">= 0.90", "PASS": bool(cons) and sc >= 0.90,
            "definition": "Jaccard(char-span union of run 1, run 2) >= 0.5; "
                          "two empty verdicts count as consistent"},
        "minimality_shrinkage": {
            "n_audited": len(aud), "shrank": shrank,
            "rate": None if not aud else round(shrink, 4),
            "gate": "descriptive (no threshold)"},
        "pairs": len(labels), "dropped_pairs": drops,
        "no_localizable_evidence_pairs": none_v,
        "no_localizable_evidence_rate": round(none_v / max(len(labels), 1), 4),
        "by_grade": by_grade,
        "windowed_pairs": sum(1 for r in labels if r["windowed"]),
        "rubric_sha256": meta["rubric_sha256"],
        "prompt_sha256": meta["prompt_sha256"],
        "scout": meta["scout"],
        "HUMAN_HALF": "PENDING-HUMAN — kappa(human-human), kappa(Scout-human), "
                      "positive-class agreement and the wrong-location / non-minimal / "
                      "missed-evidence rates require the two-reader R-dev read (SS6.6.2). "
                      "No agent read is a substitute and none was performed.",
    }
    out["ALL_MACHINE_GATES_PASS"] = bool(
        out["hallucinated_span_rate"]["PASS"] and out["self_consistency"]["PASS"])
    C.atomic_json(C.WORK / "label_gates.json", out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
