"""POST-HOC sensitivity (not pre-registered): pairs whose control chunk text is byte-identical to the stage-A window.

Found after the frozen report by the gateway coordinator's audit (duplicate request bodies across the two arms).
Reads the private confirm run directory and writes counts only (no ids, no text) to
artifacts/posthoc-identical-pairs.json. The frozen primary result is unchanged.

Usage: PYTHONPATH=python:python/scripts/eval python posthoc_identical.py CONFIRM_DIR OUT_JSON
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from chunktrial_stats import mcnemar_exact
from ragstack.provenance import experiment_provenance


def rows(p: Path):
    with p.open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    c, out = Path(sys.argv[1]), Path(sys.argv[2])
    ctl = {r["chunk_id"]: r["content"] for r in rows(c / "run" / "chunks-control.jsonl")}
    ret = {(r.get("chunk_id") or r.get("id")): r["content"] for r in rows(c / "retest" / "chunks-retest.jsonl")}
    seeds = list(rows(c / "run" / "pairs-seed.jsonl"))
    cf = {(r["record_id"], r["chunk_id"]): r for r in rows(c / "judge-control" / "final.jsonl") if "record_id" in r}
    rf = {(r["record_id"], r["chunk_id"]): r for r in rows(c / "judge-retest" / "final.jsonl") if "record_id" in r}
    cells: Counter = Counter()
    cov_ident = Counter()
    for s in seeds:
        a, b = ctl[s["chunk_id"]], ret[s["stage_a_chunk_id"]]
        kc, kr = cf[(s["record_id"], s["chunk_id"])], rf[(s["record_id"], s["stage_a_chunk_id"])]
        if kc["label"] is None or kr["label"] is None:
            raise SystemExit("REFUSED: an unjudged final row (the frozen report had 0)")
        grp = "identical" if a == b else "changed"
        if grp == "identical":
            cov_ident[round(float(s["coverage"]), 6)] += 1
        cells[(grp, "control_not" if kc["label"] == 0 else "control_answers",
               "retest_not" if kr["label"] == 0 else "retest_answers")] += 1

    def table(g: str) -> dict:
        t = {f"{x}|{y}": cells[(g, x, y)] for x in ("control_answers", "control_not") for y in ("retest_answers", "retest_not")}
        n = sum(t.values()); b = t["control_not|retest_answers"]; cc = t["control_answers|retest_not"]
        return {"n": n, "table": t, "b": b, "c": cc, "d": round((b - cc) / n, 6) if n else None,
                "mcnemar_one_sided_p": mcnemar_exact(b, cc, "greater") if (b + cc) else None}

    rec = {
        "schema": "alias-confirm-posthoc-identical/1", "post_hoc": True, "pre_registered": False,
        "definition": "control (max-overlap) chunk content == stage-A window content, byte for byte",
        "pairs": len(seeds), "identical": table("identical"), "changed": table("changed"),
        "all": {"n": len(seeds), "b": cells[("identical", "control_not", "retest_answers")] + cells[("changed", "control_not", "retest_answers")],
                "c": cells[("identical", "control_answers", "retest_not")] + cells[("changed", "control_answers", "retest_not")]},
        "identical_coverage_values": {str(k): v for k, v in sorted(cov_ident.items())},
        "provenance": experiment_provenance(),
    }
    rec["provenance"].pop("host", None)
    w = rec["provenance"].pop("warnings", None)
    rec["provenance"]["warnings_n"] = len(w or [])
    out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"identical_n": rec["identical"]["n"], "changed": {k: rec["changed"][k] for k in ("n", "b", "c", "d")}}))


if __name__ == "__main__":
    main()
