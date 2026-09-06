"""Stage 0a step 1 -- assemble the shared 90-topic corpus fetchlist (SPEC SS4.2, P.2).

Rules carried forward verbatim from ``step2/build_fetchlist.py`` so the dev-10 slice
reproduces the pilot corpus byte-for-byte (SS4.2.6): a single ``random.Random(20260904)``
over ``sorted(dev_topics)``, 300 grade-0 per topic sampled from the SORTED negative list.
The 80 confirmation topics use a SEPARATE ``random.Random(20260912)``.

Only NON-OUTCOME data is touched (SS2.3 left column): qrels counts/grades and topic text.
"""
from __future__ import annotations

import collections
import json
import random
import sys

import s0_common as C


def merged_qrels() -> dict[str, dict[str, int]]:
    """{year_prefixed_topic: {pmcid: grade}} over all three CDS years (SS4.2.1)."""
    q: dict[str, dict[str, int]] = collections.defaultdict(dict)
    for y in ("2014", "2015", "2016"):
        for line in (C.CDS / f"qrels-treceval-{y}.txt").read_text().splitlines():
            p = line.split()
            if len(p) < 4:
                continue
            q[f"{y}_{int(p[0])}"][p[2]] = int(p[3])
    return dict(q)


def topics() -> dict:
    return json.loads((C.CDS / "topics_merged.json").read_text())


def build() -> dict:
    tops = topics()
    qrels = merged_qrels()
    tids = sorted(set(tops) & set(qrels))
    assert len(tids) == 90, f"expected 90 distinct year-prefixed topics, got {len(tids)}"
    dev = [t for t in tids if t in C.DEV_TOPICS]
    conf = [t for t in tids if t not in C.DEV_TOPICS]
    assert len(dev) == 10 and len(conf) == 80, (len(dev), len(conf))

    want: set[str] = set()
    stats = {}
    # -- dev 10: the step-2 algorithm, unchanged, one RNG over sorted(dev) ------
    rng = random.Random(C.SEED_GRADE0_DEV)
    dev_only: set[str] = set()
    for tid in sorted(dev):
        pos = sorted([d for d, g in qrels[tid].items() if g >= 1])
        neg = sorted([d for d, g in qrels[tid].items() if g == 0])
        negs = rng.sample(neg, min(300, len(neg)))
        dev_only |= set(pos) | set(negs)
        stats[tid] = {"set": "dev", "pos": len(pos), "neg_pool": len(neg),
                      "neg_sampled": len(negs)}
    want |= dev_only
    # -- confirmation 80: separate RNG, same rule ------------------------------
    rng2 = random.Random(C.SEED_GRADE0_CONF)
    for tid in sorted(conf):
        pos = sorted([d for d, g in qrels[tid].items() if g >= 1])
        neg = sorted([d for d, g in qrels[tid].items() if g == 0])
        negs = rng2.sample(neg, min(300, len(neg)))
        want |= set(pos) | set(negs)
        stats[tid] = {"set": "conf", "pos": len(pos), "neg_pool": len(neg),
                      "neg_sampled": len(negs)}

    # -- the reproduction check (cheap, and the whole point of reusing the seed)
    old = sorted({x.strip() for x in (C.STEP2 / "fetchlist.txt").read_text().split()
                  if x.strip()}, key=int)
    new_dev = sorted(dev_only, key=int)
    repro = {"step2_fetchlist_n": len(old), "dev_slice_n": len(new_dev),
             "identical": old == new_dev,
             "only_in_step2": [x for x in old if x not in set(new_dev)][:20],
             "only_in_new": [x for x in new_dev if x not in set(old)][:20]}

    pos_all = {t: sorted([d for d, g in qrels[t].items() if g >= 1]) for t in tids}
    out = {
        "topics": tids, "dev": dev, "conf": conf,
        "per_topic": stats,
        "n_pmcid_wanted": len(want),
        "n_distinct_grade1_docs": len({d for t in tids for d in pos_all[t]}),
        "n_topic_doc_pairs_grade1": sum(len(v) for v in pos_all.values()),
        "dev_reproduction_check": repro,
    }
    (C.WORK / "fetchlist.txt").write_text(
        "\n".join(sorted(want, key=int)) + "\n")
    C.atomic_json(C.WORK / "qrels_all.json", qrels)
    C.atomic_json(C.WORK / "corpus_plan.json", out)
    return out


if __name__ == "__main__":
    o = build()
    print(json.dumps({k: v for k, v in o.items() if k != "per_topic"}, indent=1))
    if not o["dev_reproduction_check"]["identical"]:
        print("!! dev slice does NOT reproduce step2/fetchlist.txt", file=sys.stderr)
