#!/usr/bin/env python
"""Glossary benchmark, gold labelling: the precision analysis (rubric v3). Counts only, no text.

Committed form of round 1's scratch ``analyse_r3.py``, with the arm list, judges,
contrasts and seed as arguments (prereg §6):

    analyse_gold.py --item-arms DIR/item_arms.json \\
        --judge claude=labels-claude.jsonl --judge codex=labels-codex.jsonl --judge gemini=labels-gw.jsonl \\
        [--owner owner_labels.csv] [--arms rules,qwen36p,...] [--contrast qwen36p:rules ...] \\
        [--overlap-judge codex] [--seed 261101] [--n-boot 2000] --out gold-analysis.json

Per arm (an item counts for every arm that proposed it): harm (``wrong``), strict
precision (``correct``), lenient (``correct + partial``), not-a-symbol share, under each
judge and under the majority of the judges (:func:`gold_common.majority`: ``split`` items
are excluded and counted); 95% document-clustered bootstrap CIs; per-corpus counts
(corpus = the alias in the document key); arm contrasts; pairwise judge κ (4 labels and
partial = correct); κ of each judge and of the majority against the owner's labels; and,
with ``--overlap-judge``, the whole majority table again without the judge that shares a
vendor with an arm.
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

ItemKey = gc.ItemKey


def read_judge(path: Path) -> dict[ItemKey, str]:
    lab: dict[ItemKey, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("ok"):
            for x in r["labels"]:
                lab[(r["key"], x["id"])] = x["label"]
    return lab


def read_owner(path: Path) -> dict[ItemKey, str]:
    """``doc,item,symbol,definition,label,note`` rows; ``missed`` rows and unknown labels skipped."""
    rows = list(csv.reader(path.open(encoding="utf-8")))[1:]
    return {(r[0], r[1]): r[4] for r in rows if len(r) >= 5 and r[1] != "missed" and r[4] in gc.LABELS}


def majority_labels(lab: dict[str, dict[ItemKey, str]], items: list[ItemKey]) -> dict[ItemKey, str | None]:
    return {k: gc.majority([lab[j][k] for j in lab if k in lab[j]]) for k in items}


def precision_block(labels: dict[ItemKey, str | None], item_arms: dict[str, Any], docs: list[str],
                    arms: list[str], seed: int, n_boot: int, contrasts: list[tuple[str, str]]) -> dict[str, Any]:
    per = {a: ({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} if r else None)
           for a, r in ((a, gc.rates(c)) for a, c in gc.counts(labels, item_arms, docs, arms).items())}
    by_corpus = {}
    for alias in sorted({gc.doc_alias(d) for d in docs}):
        ds = [d for d in docs if gc.doc_alias(d) == alias]
        by_corpus[alias] = {a: dict(c) for a, c in gc.counts(labels, item_arms, ds, arms).items()}
    return {
        "per_arm": per,
        "ci95_doc_bootstrap": gc.doc_bootstrap(labels, item_arms, docs, arms, seed, n_boot),
        "by_corpus": by_corpus,
        "contrasts": {f"{a}-{b}": {k: gc.diff_bootstrap(labels, item_arms, docs, arms, a, b, k, seed + 1, n_boot)
                                   for k in ("strict", "lenient", "harm")} for a, b in contrasts},
        "majority_mix": dict(Counter(v for v in labels.values() if v)),
    }


def analyse(item_arms: dict[str, Any], lab: dict[str, dict[ItemKey, str]], owner: dict[ItemKey, str] | None,
            arms: list[str], contrasts: list[tuple[str, str]], seed: int, n_boot: int,
            overlap_judge: str | None = None) -> dict[str, Any]:
    docs = sorted(item_arms)
    items = [(d, i) for d in docs for i in item_arms[d]["items"]]
    maj = majority_labels(lab, items)
    out: dict[str, Any] = {
        "exploratory": False, "rubric": "v3", "seed": seed, "n_boot": n_boot, "arms": arms,
        "documents": len(docs), "items": len(items),
        "labelled": {j: len(v) for j, v in lab.items()},
        "label_mix": {j: dict(Counter(v.values())) for j, v in lab.items()},
        "split_items": sum(1 for v in maj.values() if v == gc.SPLIT),
        "unlabelled_items": sum(1 for v in maj.values() if v is None),
        "per_judge": {j: {a: gc.rates(c) for a, c in gc.counts(v, item_arms, docs, arms).items()}
                      for j, v in lab.items()},
        "majority": precision_block(maj, item_arms, docs, arms, seed, n_boot, contrasts),
        "judge_kappa": {},
    }
    js = sorted(lab)
    for x in range(len(js)):
        for y in range(x + 1, len(js)):
            out["judge_kappa"][f"{js[x]}~{js[y]}"] = {
                "4-label": gc.kappa(lab[js[x]], lab[js[y]]),
                "partial=correct": gc.kappa(lab[js[x]], lab[js[y]], gc.MERGE_PARTIAL)}
    if overlap_judge:
        if overlap_judge not in lab:
            raise SystemExit(f"--overlap-judge {overlap_judge!r} is not a judge")
        rest = {j: v for j, v in lab.items() if j != overlap_judge}
        out["majority_without_overlap_judge"] = {
            "excluded_judge": overlap_judge,
            **precision_block(majority_labels(rest, items), item_arms, docs, arms, seed, n_boot, contrasts)}
    if owner is not None:
        out["owner"] = {"items": len(owner), "mix": dict(Counter(owner.values())), "vs": {}}
        for name, lf in [*lab.items(), ("majority", {k: v for k, v in maj.items() if v})]:
            conf = Counter((owner[k], lf[k]) for k in owner if lf.get(k) in gc.LABELS)
            out["owner"]["vs"][name] = {
                "4-label": gc.kappa(owner, lf), "partial=correct": gc.kappa(owner, lf, gc.MERGE_PARTIAL),
                "confusion_owner_to_judge": {f"{a}->{b}": n for (a, b), n in sorted(conf.items()) if a != b}}
    return out


def _pairs(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values:
        k, sep, p = v.partition("=")
        if not sep:
            raise SystemExit(f"expected NAME=PATH, got {v!r}")
        out[k] = p
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--item-arms", required=True)
    ap.add_argument("--judge", action="append", required=True, help="NAME=labels.jsonl (repeat)")
    ap.add_argument("--owner", default=None, help="the owner's labels CSV")
    ap.add_argument("--arms", default=None, help="comma list; default: every arm in item_arms.json")
    ap.add_argument("--contrast", action="append", default=[], help="A:B over arm names (repeat)")
    ap.add_argument("--overlap-judge", default=None, help="judge sharing a vendor with an arm")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    ia_path = Path(args.item_arms)
    item_arms = json.loads(ia_path.read_text(encoding="utf-8"))
    inputs = {"item_arms.json": gc.sha256_file(ia_path)}
    lab = {}
    for j, p in _pairs(args.judge).items():
        lab[j] = read_judge(Path(p))
        inputs[f"judge:{j}"] = gc.sha256_file(p)
    owner = None
    if args.owner:
        owner = read_owner(Path(args.owner))
        inputs["owner"] = gc.sha256_file(args.owner)
    seen = []
    for d in item_arms.values():
        for m in d["items"].values():
            for a in m["arms"]:
                if a not in seen:
                    seen.append(a)
    arms = args.arms.split(",") if args.arms else seen
    contrasts = []
    for c in args.contrast:
        a, _, b = c.partition(":")
        if a not in arms or b not in arms:
            raise SystemExit(f"--contrast {c!r}: expected A:B over arm names")
        contrasts.append((a, b))
    out = analyse(item_arms, lab, owner, arms, contrasts, args.seed, args.n_boot, args.overlap_judge)
    out["inputs_sha256"] = inputs
    try:
        from ragstack.provenance import experiment_provenance

        prov = experiment_provenance()
        out["provenance"] = prov
        out["citable"] = bool(prov.get("dirty") is False and prov.get("untracked") == 0)
    except Exception as e:  # noqa: BLE001
        out["provenance"] = {"error": type(e).__name__}
        out["citable"] = False
    gc.write_private(Path(args.out), json.dumps(out, indent=1))
    print(json.dumps({k: out.get(k) for k in ("labelled", "split_items", "judge_kappa")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
