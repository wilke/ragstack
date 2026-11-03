#!/usr/bin/env python
"""Alias study prereg (DRAFT v3), §13.5: how often the judged-pair spot check flags a judge (R15).

    python -I spot_check_detection.py --out spot_check_detection.json

A planning calculation, not a study result: it reads no pair, no label and no record. Exact
binomial; no random number is drawn, so no seed is needed.

**The rule** (§13.5): owner-judge agreement below 0.90 at the point estimate is stated in every
result sentence. For a stratum of n pairs and a judge whose true error rate on that stratum is e,
the owner's count of disagreements X is Binomial(n, e) (the owner taken as truth, pairs
independent), and the rule fires when (n - X) / n < 0.90, i.e. X > 0.1 n. The **detection
probability** is P(X > 0.1 n).

**What is computed.** The *answers* stratum alone (a false *yes* is what inflates found@10, and
that stratum is read on its own by the rule): 30 pairs on the 60-pair sheet of R5, 50 pairs on the
100-pair sheet of R15 (R5's 30 / 20 / 10 split scaled to 100: 50 / 33 / 17). Also the whole sheet
read as one unweighted sample (60 and 100), for comparison only: the rule's overall figure is
re-weighted to the stage-A label shares, which do not exist before stage A. True error rates
0.10 (the threshold itself), 0.15 and 0.20.

The provenance block comes from ``ragstack.provenance.experiment_provenance`` of this repository's
``python/`` tree when it imports; otherwise it is recorded as absent with the reason.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

THRESHOLD = Fraction(9, 10)
ERROR_RATES = (0.10, 0.15, 0.20)
SHEETS = {
    "R5_60_pairs": {"answers_stratum": 30, "whole_sheet": 60, "split": [30, 20, 10]},
    "R15_100_pairs": {"answers_stratum": 50, "whole_sheet": 100, "split": [50, 33, 17]},
}
REPO = Path(__file__).resolve().parents[6]


def fires_at(n: int) -> int:
    """Smallest disagreement count X with (n - X) / n < 0.90."""
    return next(x for x in range(n + 1) if Fraction(n - x, n) < THRESHOLD)


def detect(n: int, e: float) -> float:
    x0 = fires_at(n)
    return sum(math.comb(n, x) * e ** x * (1 - e) ** (n - x) for x in range(x0, n + 1))


def provenance() -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "python"))
    try:
        from ragstack.provenance import experiment_provenance
    except Exception as e:  # noqa: BLE001 - recorded, never raised
        return {"absent": f"ragstack.provenance did not import: {type(e).__name__}"}
    return experiment_provenance(repo=REPO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    prov = provenance()   # at launch: the tree that ran
    out: dict[str, Any] = {}
    for name, sh in SHEETS.items():
        rec: dict[str, Any] = {"split_answers_not_tiebroken": sh["split"]}
        for part in ("answers_stratum", "whole_sheet"):
            n = sh[part]
            rec[part] = {"n": n, "fires_at_disagreements": fires_at(n),
                         "detection": {f"e{e:g}": round(detect(n, e), 4) for e in ERROR_RATES}}
        out[name] = rec
    curve = {str(n): round(detect(n, 0.10), 4) for n in (20, 30, 40, 50, 60, 80, 100, 150, 200)}
    doc = {
        "schema": "alias-prereg-spot-check-detection/1",
        "what": "detection probability of the judged-pair spot check's < 0.90 rule (prereg DRAFT v3 §13.5, "
                "R15); exact binomial, a planning calculation, no study data",
        "rule": "fires when owner-judge agreement (n - X) / n < 0.90 at the point estimate",
        "model": "X ~ Binomial(n, e), e the judge's true error rate on the stratum, owner labels as truth",
        "sheets": out,
        "detection_at_e0.1_by_n": curve,
        "note": "at e = 0.10 the true agreement equals the threshold, so detection stays below 0.5 at any n",
        "provenance": prov,
    }
    Path(a.out).write_text(json.dumps(doc, indent=1) + "\n")
    for name, rec in out.items():
        print(name, json.dumps({k: rec[k]["detection"] for k in ("answers_stratum", "whole_sheet")}))
    print("e=0.10 by n:", curve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
