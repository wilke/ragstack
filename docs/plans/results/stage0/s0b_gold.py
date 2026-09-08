"""Stage 0b' -- the gold for both populations, built once and cached.

**CDS.** r3 SS10 item 4 leaves *what "where" means* open and the owner is pursuing all three
options, so this module builds all three and ``s0b_score`` reports them side by side:

(a) **graded / support-weighted** -- per sentence, the fraction of the **30 pooled
    readings** (Scout x20 + Qwen x10, `artifacts/r31ext/`) that select it. The
    Spearman-Brown reliability of this instrument is 0.9205 (#512), above r3 SS3.7's 0.90
    labeler bar. The **one flagged locator blow-up** (#513: Scout, `2016_1`/`4212306`,
    presentation 11 -- 22 model spans, 986 emitted, 22,366 sentences) is **dropped**, so
    that pair's denominator is 29. The criterion is #512's own, read off the record's
    ``vstats``: ``spans_emitted >= 10 x max(spans_seen, 1)``.
(b) **core** -- the sentences with pooled support >= 0.5, unweighted.
(c) **unit-based** -- D3 rules 1-3 (within-document merge at span-union Jaccard >= 0.5,
    containment pruning, no cross-document merge) over the **k = 0 union of both judges**,
    then r3 SS3.1's replacement for rule 4: **<= 4 units per DOCUMENT**, seeded 20260918.
    This is the r2-style reading of the same labels.

An **evidence-bearing document** is one with any pooled support > 0 (reading (a)'s
denominator-free definition), which is what r3 SS3.1's ``ERET`` counts over. Documents
longer than 120,000 characters are **flagged** on this population (they are *excluded* on
the pointed one, #513) and a sensitivity without them is reported.

**Pointed.** Gold is the construction passage: the ``gold_spans`` of
``work/pointed/pointed-dev.jsonl``, as D1 char spans ``[start, end)`` in the source
document. One query per document, so the cluster is the query.
"""
from __future__ import annotations

import json
import random

import s0b_common as K
import s0_common as C
from s0_label import segment
from s0_score import jaccard, merge_iv, total

EXT = K.HERE / "artifacts" / "r31ext"
JUDGE_READINGS = {"scout": 20, "qwen": 10}
OUTLIER_EMITTED_RATIO = 10          # #512's criterion, reused verbatim


def _sentence_map(text: str, units: list[dict]) -> dict:
    """``(unit_index, sentence_index) -> (start_char, end_char)`` over the indexed text."""
    out = {}
    for ui, _title, sents in segment(text, units):
        for k, a, b, _s in sents:
            out[(ui, k)] = (a, b)
    return out


def _sentences(rec) -> set:
    out = set()
    for s in rec["sets"]:
        for sp in s["spans"]:
            for i in range(sp["first_sentence"], sp["last_sentence"] + 1):
                out.add((sp["unit"], i))
    return out


def _flagged(rec) -> bool:
    v = rec.get("vstats") or {}
    return v.get("spans_emitted", 0) >= OUTLIER_EMITTED_RATIO * max(v.get("spans_seen", 0), 1)


def load_readings() -> tuple[dict, dict]:
    """``(topic, docno) -> [reading, ...]`` pooled over judges, plus the drop ledger."""
    by: dict = {}
    dropped = []
    for judge, n in JUDGE_READINGS.items():
        p = EXT / f"labels-r31-{judge}.jsonl"
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r["presentation"] >= n:
                continue
            key = (r["topic"], r["docno"])
            if _flagged(r):
                dropped.append({"judge": judge, "topic": r["topic"], "docno": r["docno"],
                                "presentation": r["presentation"],
                                "spans_seen": (r.get("vstats") or {}).get("spans_seen"),
                                "spans_emitted": (r.get("vstats") or {}).get("spans_emitted")})
                continue
            by.setdefault(key, []).append(r)
    bad = {t for t, _d in by} - set(K.DEV_TOPICS)
    assert not bad, f"non-development topic in the labels: {sorted(bad)}"
    return by, dropped


def _d3_units(sets: list[dict]) -> list[dict]:
    """D3 rules 1-3 on one document's evidence sets (s0_score's algorithm, unchanged)."""
    cur = [{"spans": s["spans"],
            "iv": merge_iv([[sp["start"], sp["end"]] for sp in s["spans"]])}
           for s in sets if s.get("spans")]
    changed = True
    while changed:
        changed = False
        for i in range(len(cur)):
            for j in range(i + 1, len(cur)):
                if jaccard(cur[i]["iv"], cur[j]["iv"]) >= C.JACCARD_MERGE:
                    keep = cur[i] if total(cur[i]["iv"]) <= total(cur[j]["iv"]) else cur[j]
                    cur = [c for k, c in enumerate(cur) if k not in (i, j)] + [keep]
                    changed = True
                    break
            if changed:
                break
    keys = [frozenset((sp["unit"], sp["first_sentence"], sp["last_sentence"])
                      for sp in c["spans"]) for c in cur]
    drop = {j for i in range(len(cur)) for j in range(len(cur))
            if i != j and keys[i] < keys[j]}
    return [c for k, c in enumerate(cur) if k not in drop]


def build_cds(docs, units) -> dict:
    readings, dropped = load_readings()
    out: dict = {"topics": {}, "dropped_readings": dropped,
                 "readings_per_pair": {}, "long_documents": []}
    rng_cap = random.Random(K.SEED_UNITCAP_R3)
    for (topic, docno), recs in sorted(readings.items()):
        text = docs[docno]
        smap = _sentence_map(text, units[docno])
        n = len(recs)
        tally: dict = {}
        for r in recs:
            for s in _sentences(r):
                tally[s] = tally.get(s, 0) + 1
        support = {}
        for s, c in tally.items():
            if s in smap:                      # a sentence index the segmenter has
                support[s] = c / n
        k0 = [r for r in recs if r["presentation"] == 0]
        sets = [s for r in k0 for s in r["sets"] if s.get("spans")]
        d3 = _d3_units(sets)
        if len(d3) > K.UNIT_CAP_PER_DOC:
            rng = random.Random(f"{K.SEED_UNITCAP_R3}:{topic}:{docno}")
            rng.shuffle(d3)
            d3 = d3[:K.UNIT_CAP_PER_DOC]
        rec = {
            "docno": docno, "grade": recs[0]["grade"], "kind": recs[0]["kind"],
            "n_readings": n,
            "chars": len(text),
            "support": {f"{u}:{i}": round(w, 6) for (u, i), w in sorted(support.items())},
            "sentence_spans": {f"{u}:{i}": list(smap[(u, i)])
                               for (u, i) in sorted(support)},
            "core": sorted(f"{u}:{i}" for (u, i), w in support.items()
                           if w >= K.CORE_SUPPORT),
            "units": [{"spans": [{"unit": sp["unit"], "start": sp["start"],
                                  "end": sp["end"]} for sp in c["spans"]]} for c in d3],
        }
        rec["support_mass"] = round(sum(support.values()), 6)
        rec["evidence_bearing"] = rec["support_mass"] > 0
        if len(text) > K.LONG_DOC_CHARS:
            out["long_documents"].append({"topic": topic, "docno": docno,
                                          "chars": len(text)})
        out["topics"].setdefault(topic, {})[docno] = rec
        out["readings_per_pair"][f"{topic}/{docno}"] = n
    del rng_cap
    out["summary"] = {
        "pairs": sum(len(v) for v in out["topics"].values()),
        "evidence_bearing_docs_per_topic": {
            t: sum(1 for r in v.values() if r["evidence_bearing"] and r["kind"] == "pooled")
            for t, v in sorted(out["topics"].items())},
        "pooled_pairs_per_topic": {
            t: sum(1 for r in v.values() if r["kind"] == "pooled")
            for t, v in sorted(out["topics"].items())},
        "dropped_readings": len(dropped),
        "long_documents": len(out["long_documents"]),
        "unit_cap_per_document": K.UNIT_CAP_PER_DOC,
        "unit_cap_seed": K.SEED_UNITCAP_R3,
    }
    return out


def build_pointed() -> dict:
    out = {}
    for line in K.POINTED.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["qid"]] = {
            "docno": r["docno"], "topic": r["draw_topic"],
            "spans": merge_iv([[g["start"], g["end"]] for g in r["gold_spans"]]),
            "n_gold_spans": len(r["gold_spans"]),
            "unit": r["unit"], "query": r["query"]}
    return out


def main() -> None:
    docs = K.load_docs()
    units = K.load_units()
    cds = build_cds(docs, units)
    K.atomic_json(K.OUT / "gold_cds.json", cds)
    pt = build_pointed()
    K.atomic_json(K.OUT / "gold_pointed.json", pt)
    print(json.dumps(cds["summary"], indent=1), flush=True)
    print("dropped readings:", json.dumps(cds["dropped_readings"]), flush=True)
    print("pointed queries:", len(pt), flush=True)


if __name__ == "__main__":
    main()
