#!/usr/bin/env python
"""Glossary benchmark, gold labelling: the owner's labels against the judges (κ). Counts only.

Committed form of round 1's scratch ``human_agree.py`` (rubric v3 labels):

    human_agree.py --owner owner_labels.csv --judge claude=labels-claude.jsonl \\
        --judge codex=labels-codex.jsonl [--judge gemini=...] [--exclude KEY:ITEM ...] --out agree.json

Reports, for each judge and for the judges' majority (``split`` items left out): Cohen's κ
and raw agreement on the items both labelled, under the four labels and with partial =
correct, and the owner -> judge confusion counts. ``missed`` rows (symbols the owner says
no item covers) are counted per document when present.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import gold_common as gc  # noqa: E402
from analyse_gold import read_judge  # noqa: E402


def read_owner_csv(path: Path, exclude: set[tuple[str, str]]) -> tuple[dict[gc.ItemKey, str], dict[str, int], int]:
    owner: dict[gc.ItemKey, str] = {}
    missed: dict[str, int] = {}
    unlabelled = 0
    for r in list(csv.reader(path.open(encoding="utf-8")))[1:]:
        if len(r) < 5:
            continue
        if r[1] == "missed":
            missed[r[0]] = len([x for x in (r[5] if len(r) > 5 else "").split(";") if "=" in x])
            continue
        if (r[0], r[1]) in exclude:
            continue
        if r[4] in gc.LABELS:
            owner[(r[0], r[1])] = r[4]
        else:
            unlabelled += 1
    return owner, missed, unlabelled


def agreement(owner: dict[gc.ItemKey, str], lab: dict[str, dict[gc.ItemKey, str]]) -> dict[str, Any]:
    maj = {k: gc.majority([lab[j][k] for j in lab if k in lab[j]]) for k in owner}
    raters = {**lab, "majority": {k: v for k, v in maj.items() if v and v != gc.SPLIT}}
    out: dict[str, Any] = {}
    for name, lf in raters.items():
        conf = Counter((owner[k], lf[k]) for k in owner if k in lf)
        out[name] = {"4-label": gc.kappa(owner, lf), "partial=correct": gc.kappa(owner, lf, gc.MERGE_PARTIAL),
                     "confusion_owner_to_judge": {f"{a}->{b}": n for (a, b), n in sorted(conf.items()) if a != b}}
    out["majority_split_items"] = sum(1 for v in maj.values() if v == gc.SPLIT)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--owner", required=True)
    ap.add_argument("--judge", action="append", required=True, help="NAME=labels.jsonl (repeat)")
    ap.add_argument("--exclude", action="append", default=[], help="KEY:ITEM to leave out (repeat)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    exclude = {tuple(x.rsplit(":", 1)) for x in args.exclude}
    owner, missed, unlabelled = read_owner_csv(Path(args.owner), exclude)  # type: ignore[arg-type]
    lab = {}
    inputs = {"owner": gc.sha256_file(args.owner)}
    for v in args.judge:
        j, _, p = v.partition("=")
        lab[j] = read_judge(Path(p))
        inputs[f"judge:{j}"] = gc.sha256_file(p)
    out = {"rubric": "v3", "owner_items": len(owner), "unlabelled": unlabelled, "excluded": sorted(map(list, exclude)),
           "owner_mix": dict(Counter(owner.values())), "missed_symbols_total": sum(missed.values()),
           "missed_docs": sum(1 for v in missed.values() if v), "vs_owner": agreement(owner, lab),
           "inputs_sha256": inputs}
    gc.write_private(Path(args.out), json.dumps(out, indent=1))
    print(json.dumps(out["vs_owner"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
